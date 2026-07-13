"""End-to-end API integration tests against a real Postgres.

Exercises the full stack (FastAPI routes -> services -> SQLAlchemy
repositories -> Postgres) with both the ``MarketDataSource`` and
``FeatureSource`` ports swapped for in-memory fakes, proving the
definition-registration and build-triggering endpoints work together
without depending on a running Market Data or Feature Store service.

Uses ``httpx.AsyncClient`` (ASGI transport) rather than
``fastapi.testclient.TestClient`` for the same reason as
``aqros_market_data``'s and ``aqros_feature_store``'s equivalent tests: the
latter runs the app on a separate event loop, incompatible with an asyncpg
engine created on the test's own loop.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from aqros_dataset_builder.adapters import db as db_adapter
from aqros_dataset_builder.api.deps import (
    get_feature_source,
    get_git_info_provider,
    get_market_data_source,
    get_session,
)
from aqros_dataset_builder.domain.models import BarInterval, FeatureValue, OHLCVBar
from aqros_dataset_builder.domain.ports import FeatureSource, GitInfoProvider, MarketDataSource

pytestmark = pytest.mark.integration

_EPOCH = datetime(2024, 1, 1, tzinfo=UTC)


class _FakeMarketDataSource(MarketDataSource):
    def __init__(self) -> None:
        self.bars_by_symbol: dict[str, list[OHLCVBar]] = {}

    async def get_bars(self, symbol, *, start=None, end=None, interval=BarInterval.DAILY):
        return self.bars_by_symbol.get(symbol.upper(), [])


class _FakeFeatureSource(FeatureSource):
    def __init__(self) -> None:
        self.values_by_key: dict[tuple[str, str], list[FeatureValue]] = {}

    async def get_feature_values(self, symbol, feature_name, *, start=None, end=None):
        return self.values_by_key.get((symbol.upper(), feature_name), [])


class _FakeGitInfoProvider(GitInfoProvider):
    async def get_commit_sha(self) -> str | None:
        return "test-commit-sha"


def _bar(symbol: str, day: int, close: float = 100.0) -> OHLCVBar:
    event_time = _EPOCH + timedelta(days=day - 1)
    return OHLCVBar(
        symbol=symbol,
        event_time=event_time,
        interval=BarInterval.DAILY,
        open=Decimal(str(close)),
        high=Decimal(str(close + 1)),
        low=Decimal(str(close - 1)),
        close=Decimal(str(close)),
        volume=1000,
        source="fake",
        knowledge_time=event_time,
    )


def _feature_value(
    symbol: str, day: int, value: float, feature_name: str = "sma_20"
) -> FeatureValue:
    event_time = _EPOCH + timedelta(days=day - 1)
    return FeatureValue(
        symbol=symbol,
        feature_name=feature_name,
        feature_version=1,
        event_time=event_time,
        value=value,
        knowledge_time=event_time,
    )


@pytest.fixture
async def client(
    engine: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
) -> AsyncIterator[AsyncClient]:
    from aqros_dataset_builder.adapters.parquet_storage import LocalParquetStorage
    from aqros_dataset_builder.app import app, health_registry, settings

    fake_market_data = _FakeMarketDataSource()
    fake_feature_source = _FakeFeatureSource()
    fake_git_provider = _FakeGitInfoProvider()

    async def _override_get_session():  # type: ignore[no-untyped-def]
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = _override_get_session
    app.dependency_overrides[get_market_data_source] = lambda: fake_market_data
    app.dependency_overrides[get_feature_source] = lambda: fake_feature_source
    app.dependency_overrides[get_git_info_provider] = lambda: fake_git_provider
    app.state.fake_market_data = fake_market_data
    app.state.fake_feature_source = fake_feature_source
    # httpx.AsyncClient + ASGITransport never triggers the app's lifespan
    # (see https://github.com/encode/httpx/issues/1441), so app.state
    # attributes normally set there (dataset_storage, settings, ...) must be
    # populated here directly — the `get_dataset_storage`/`get_builder_service`
    # dependencies read them straight off `app.state` rather than via an
    # overridable Depends(), so this is the only way to supply them in tests.
    app.state.settings = settings
    app.state.dataset_storage = LocalParquetStorage(settings.dataset_artifact_dir)
    health_registry.register("database", lambda: db_adapter.ping(engine))
    health_registry.register("market_data_service", lambda: True)
    health_registry.register("feature_store_service", lambda: True)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        test_client.app_state = app.state
        yield test_client

    app.dependency_overrides.clear()


async def test_create_definition_registers_version_one(client: AsyncClient) -> None:
    payload = {
        "name": "aapl_direction",
        "symbols": ["aapl"],
        "feature_names": ["sma_20"],
        "label_type": "binary_direction",
        "horizon": "1d",
        "split_strategy": "walk_forward",
        "walk_forward_params": {
            "train_size": 3,
            "validation_size": 1,
            "test_size": 1,
            "step_size": 1,
        },
        "start_date": "2024-01-01",
        "end_date": "2024-02-01",
    }
    resp = await client.post("/v1/datasets", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "aapl_direction"
    assert body["version"] == 1
    assert body["symbols"] == ["AAPL"]


async def test_create_definition_twice_increments_version(client: AsyncClient) -> None:
    payload = {
        "name": "aapl_v2",
        "symbols": ["AAPL"],
        "feature_names": ["sma_20"],
        "label_type": "binary_direction",
        "horizon": "1d",
        "split_strategy": "rolling_window",
        "rolling_window_params": {"train_size": 3, "validation_size": 1, "test_size": 1},
        "start_date": "2024-01-01",
        "end_date": "2024-02-01",
    }
    first = await client.post("/v1/datasets", json=payload)
    second = await client.post("/v1/datasets", json=payload)
    assert first.json()["version"] == 1
    assert second.json()["version"] == 2


async def test_create_definition_rejects_end_before_start(client: AsyncClient) -> None:
    payload = {
        "name": "bad_dates",
        "symbols": ["AAPL"],
        "feature_names": ["sma_20"],
        "label_type": "future_return",
        "horizon": "5d",
        "split_strategy": "expanding_window",
        "expanding_window_params": {"validation_size": 1, "test_size": 1},
        "start_date": "2024-02-01",
        "end_date": "2024-01-01",
    }
    resp = await client.post("/v1/datasets", json=payload)
    assert resp.status_code == 422


async def test_create_definition_requires_matching_split_params(client: AsyncClient) -> None:
    payload = {
        "name": "missing_params",
        "symbols": ["AAPL"],
        "feature_names": ["sma_20"],
        "label_type": "future_return",
        "horizon": "5d",
        "split_strategy": "purged_cv",
        # purged_cv_params intentionally omitted
        "start_date": "2024-01-01",
        "end_date": "2024-02-01",
    }
    resp = await client.post("/v1/datasets", json=payload)
    assert resp.status_code == 422


async def test_full_build_pipeline_persists_dataset_and_manifest(client: AsyncClient) -> None:
    client.app_state.fake_market_data.bars_by_symbol["AAPL"] = [
        _bar("AAPL", day, close=100.0 + day) for day in range(1, 11)
    ]
    client.app_state.fake_feature_source.values_by_key[("AAPL", "sma_20")] = [
        _feature_value("AAPL", day, value=float(day)) for day in range(1, 11)
    ]

    create_resp = await client.post(
        "/v1/datasets",
        json={
            "name": "aapl_build_test",
            "symbols": ["AAPL"],
            "feature_names": ["sma_20"],
            "label_type": "binary_direction",
            "horizon": "1d",
            "split_strategy": "walk_forward",
            "walk_forward_params": {
                "train_size": 3,
                "validation_size": 1,
                "test_size": 1,
                "step_size": 1,
            },
            "start_date": "2024-01-01",
            "end_date": "2024-02-01",
        },
    )
    assert create_resp.status_code == 201

    build_resp = await client.post("/v1/datasets/aapl_build_test/build", json={"version": 1})
    assert build_resp.status_code == 201
    run_body = build_resp.json()
    assert run_body["status"] == "succeeded"
    assert run_body["bars_read"] == 10
    assert run_body["rows_generated"] > 0
    assert run_body["leakage_audit_passed"] is True
    assert run_body["quality_report"] is not None
    assert run_body["quality_report"]["total_rows"] == run_body["rows_generated"]
    assert run_body["parquet_path"] is not None
    assert run_body["manifest_path"] is not None

    run_id = run_body["id"]

    manifest_resp = await client.get(f"/v1/runs/{run_id}/manifest")
    assert manifest_resp.status_code == 200
    manifest = manifest_resp.json()
    assert manifest["dataset_name"] == "aapl_build_test"
    assert manifest["git_commit"] == "test-commit-sha"
    assert manifest["checksum"]

    preview_resp = await client.get(
        f"/v1/datasets/aapl_build_test/runs/{run_id}/preview", params={"limit": 5}
    )
    assert preview_resp.status_code == 200
    assert len(preview_resp.json()) > 0


async def test_build_unknown_definition_returns_404(client: AsyncClient) -> None:
    resp = await client.post("/v1/datasets/does_not_exist/build", json={"version": 1})
    assert resp.status_code == 404


async def test_get_unknown_definition_returns_404(client: AsyncClient) -> None:
    resp = await client.get("/v1/datasets/does_not_exist")
    assert resp.status_code == 404


async def test_get_unknown_run_returns_404(client: AsyncClient) -> None:
    resp = await client.get("/v1/runs/999999")
    assert resp.status_code == 404


async def test_readiness_reports_all_health_checks(client: AsyncClient) -> None:
    resp = await client.get("/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    checks = {check["name"]: check["healthy"] for check in body["checks"]}
    assert checks["database"] is True
    assert checks["market_data_service"] is True
    assert checks["feature_store_service"] is True
