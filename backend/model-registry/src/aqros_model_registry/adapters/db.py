"""Async SQLAlchemy 2.0 engine and session management.

One engine per process, created from typed config, with a session-per-request
pattern exposed via ``session_scope`` (scripts/tests) and via ``api/deps.py``
for request-scoped DI. Exact mirror of ``aqros_training_pipeline.adapters.db``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from aqros_model_registry.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    """Create the async engine for this service, configured from ``Settings``."""
    return create_async_engine(
        str(settings.database_url),
        echo=settings.db_echo,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create a session factory bound to ``engine``."""
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


@asynccontextmanager
async def session_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Provide a transactional session: commits on success, rolls back on error."""
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def ping(engine: AsyncEngine) -> bool:
    """Lightweight connectivity check used by the readiness probe."""
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return True
