"""Fixtures for integration tests: a real Postgres via testcontainers.

Marked ``integration`` (require Docker). Spins up a throwaway Postgres,
creates the schema from ORM metadata (the migration itself is exercised
separately in test_migrations.py), and builds the FastAPI app via
``httpx.AsyncClient`` + ``ASGITransport`` with the session, Training
Pipeline client, artifact store, and artifact signer overridden — no live
Training Pipeline instance required. Mirrors ``aqros_training_pipeline``'s
integration conftest. Never imports ``aqros_training_pipeline``.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator, Iterator

import pytest

try:
    import docker as _docker

    _docker.from_env().ping()
except Exception:
    pytest.skip("Docker is required for integration tests", allow_module_level=True)
import pytest_asyncio

os.environ.setdefault("AQROS_ARTIFACT_DIR", tempfile.mkdtemp(prefix="model-registry-"))

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.postgres import PostgresContainer

from aqros_model_registry.adapters import db as db_adapter
from aqros_model_registry.adapters.local_artifact_store import LocalArtifactStore
from aqros_model_registry.adapters.orm import Base
from aqros_model_registry.api.deps import (
    get_artifact_signer,
    get_artifact_store,
    get_session,
    get_training_pipeline_client,
)

from ..unit.fakes import FakeArtifactSigner, FakeTrainingPipelineClient


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


@pytest_asyncio.fixture
async def client(
    engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path_factory: pytest.TempPathFactory,
) -> AsyncIterator[AsyncClient]:
    """FastAPI app under ASGITransport with overridden session + upstream client.

    ``ASGITransport`` never runs the app lifespan, so ``app.state`` is
    populated manually here (the ``get_*`` deps read the fakes off it).
    """
    from aqros_model_registry.app import app, health_registry

    fake_client = FakeTrainingPipelineClient()
    artifact_store = LocalArtifactStore(str(tmp_path_factory.mktemp("artifacts")))
    artifact_signer = FakeArtifactSigner()

    async def _override_get_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = _override_get_session
    app.dependency_overrides[get_training_pipeline_client] = lambda: fake_client
    app.dependency_overrides[get_artifact_store] = lambda: artifact_store
    app.dependency_overrides[get_artifact_signer] = lambda: artifact_signer

    app.state.session_factory = session_factory
    app.state.artifact_store = artifact_store
    app.state.training_pipeline_client = fake_client
    app.state.artifact_signer = artifact_signer
    health_registry.register("database", lambda: db_adapter.ping(engine))
    health_registry.register("artifact_store", lambda: True)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        test_client.fake_training_pipeline = fake_client  # type: ignore[attr-defined]
        yield test_client

    app.dependency_overrides.clear()
