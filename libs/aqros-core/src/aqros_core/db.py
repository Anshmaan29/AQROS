"""Database helpers shared by every service that owns a database.

CLAUDE.md §3 gives each service its own Postgres, and several of them duplicate
this same engine/session code. It lives here once so they cannot drift.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from aqros_core.health import CheckFn

#: The table Alembic writes to track applied migrations. Its presence is the
#: cheapest reliable signal that migrations have run.
ALEMBIC_VERSION_TABLE = "alembic_version"


def create_engine(
    url: str,
    *,
    echo: bool = False,
    pool_size: int = 5,
    max_overflow: int = 10,
) -> AsyncEngine:
    """Create an async engine with a bounded, self-healing pool.

    ``pool_pre_ping`` matters more than it looks: a service that was idle across
    a database restart holds dead connections and fails its first real request
    after it looks healthy again.
    """
    return create_async_engine(
        url,
        echo=echo,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


async def ping(engine: AsyncEngine) -> bool:
    """Connectivity check: can we reach the database at all?"""
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return True


async def schema_ready(engine: AsyncEngine) -> bool:
    """Whether migrations have been applied.

    This exists because a bare connectivity check is not a readiness signal. A
    freshly started service with an empty database answers ``SELECT 1`` happily
    and reports itself healthy, then 500s on every real request because its
    tables do not exist. That is worse than not-ready: it looks fine until
    something depends on it.

    Checks for ``alembic_version`` because its absence means migrations have not
    run, which is the actual precondition for serving traffic.
    """
    query = text(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = :table"
    )
    async with engine.connect() as conn:
        result = await conn.execute(query, {"table": ALEMBIC_VERSION_TABLE})
        return result.scalar_one_or_none() is not None


def schema_check(engine: AsyncEngine) -> CheckFn:
    """A ``HealthRegistry``-compatible check for :func:`schema_ready`.

    Returns a *callable*, not a coroutine. The registry invokes its check on
    every probe, so handing it an already-created coroutine would work exactly
    once and then raise ("cannot reuse already awaited coroutine"), which the
    registry would faithfully report as unhealthy — a readiness probe that lies
    about itself after the first call.
    """

    async def _check() -> bool:
        return await schema_ready(engine)

    return _check
