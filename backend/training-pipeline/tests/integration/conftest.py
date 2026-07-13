"""Fixtures for integration tests: a real Postgres via testcontainers.

Marked ``integration`` (require Docker). Spins up a throwaway Postgres,
creates the schema from ORM metadata (the migration itself is exercised
separately in test_migrations.py), and builds the FastAPI app via
``httpx.AsyncClient`` + ``ASGITransport`` with the session, Dataset Builder
client, artifact store, and git-info provider overridden — no live Dataset
Builder instance required. Mirrors ``aqros_dataset_builder``'s integration
conftest.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio

os.environ.setdefault("AQROS_ARTIFACT_DIR", tempfile.mkdtemp(prefix="training-pipeline-"))

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.postgres import PostgresContainer

from aqros_training_pipeline.adapters import db as db_adapter
from aqros_training_pipeline.adapters.local_artifact_store import (
    LocalArtifactStore,
)
from aqros_training_pipeline.adapters.orm import Base
from aqros_training_pipeline.api.deps import (
    get_artifact_store,
    get_dataset_builder_client,
    get_git_info_provider,
    get_session,
)
from aqros_training_pipeline.domain.models import (
    DatasetBuildRun,
    DatasetManifest,
)
from aqros_training_pipeline.domain.ports import (
    DatasetBuilderClient,
    DatasetBuildRunNotFoundError,
    UpstreamSourceError,
)
from tests.unit.builders import (
    make_build_run,
    make_dataframe,
    make_manifest,
    to_parquet_bytes,
)


class ConfigurableFakeDatasetBuilderClient(DatasetBuilderClient):
    """Test double serving a preloaded, mutable build run / manifest / artifact."""

    def __init__(self) -> None:
        df = make_dataframe(n_folds=2, rows_per_role=16)
        self.artifact = to_parquet_bytes(df)
        checksum = hashlib.sha256(self.artifact).hexdigest()
        self.manifest: DatasetManifest = make_manifest(checksum=checksum)
        self.build_run: DatasetBuildRun = make_build_run(leakage_audit_passed=True)
        self.not_found = False
        self.upstream_error = False

    def _guard(self) -> None:
        if self.not_found:
            raise DatasetBuildRunNotFoundError("fake: not found")
        if self.upstream_error:
            raise UpstreamSourceError("fake: upstream error")

    async def get_build_run(self, build_run_id: int) -> DatasetBuildRun:
        self._guard()
        return self.build_run

    async def get_manifest(self, build_run_id: int) -> DatasetManifest:
        self._guard()
        return self.manifest

    async def download_dataset(self, build_run_id: int) -> bytes:
        self._guard()
        return self.artifact


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
    from aqros_training_pipeline.app import app, health_registry

    fake_client = ConfigurableFakeDatasetBuilderClient()
    artifact_store = LocalArtifactStore(str(tmp_path_factory.mktemp("artifacts")))

    from tests.unit.fakes import FakeGitInfoProvider

    git_provider = FakeGitInfoProvider(commit_sha="testsha")

    async def _override_get_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = _override_get_session
    app.dependency_overrides[get_dataset_builder_client] = lambda: fake_client
    app.dependency_overrides[get_artifact_store] = lambda: artifact_store
    app.dependency_overrides[get_git_info_provider] = lambda: git_provider

    app.state.session_factory = session_factory
    app.state.artifact_store = artifact_store
    app.state.dataset_builder_client = fake_client
    app.state.git_info_provider = git_provider
    health_registry.register("database", lambda: db_adapter.ping(engine))
    health_registry.register("dataset_builder_service", lambda: True)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        test_client.fake_dataset_builder = fake_client  # type: ignore[attr-defined]
        yield test_client

    app.dependency_overrides.clear()
