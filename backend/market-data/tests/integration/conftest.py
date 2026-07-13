"""Fixtures for integration tests: a real Postgres via testcontainers.

These tests are marked ``integration`` and require Docker. They spin up a
throwaway Postgres, create the schema directly from the ORM metadata (a
migration-equivalent for test speed — the migration itself is exercised
separately, see test_migrations.py), and hand out a session factory bound to
it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.postgres import PostgresContainer

from aqros_market_data.adapters.orm import Base


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as container:
        yield container


@pytest_asyncio.fixture
async def engine(postgres_container: PostgresContainer) -> AsyncIterator[AsyncEngine]:
    url = postgres_container.get_connection_url()
    test_engine = create_async_engine(url, poolclass=None)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield test_engine
    finally:
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await test_engine.dispose()


@pytest_asyncio.fixture
async def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


@pytest_asyncio.fixture
async def db_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session
        await session.rollback()
