"""End-to-end API integration tests against a real Postgres.

Exercises the full stack (FastAPI routes -> services -> SQLAlchemy
repositories -> Postgres) with the ``MarketDataSource`` port swapped for an
in-memory fake, proving the pipeline and retrieval endpoints work together
without depending on network access to a running Market Data Service.

Uses ``httpx.AsyncClient`` (ASGI transport) rather than
``fastapi.testclient.TestClient`` for the same reason as
``aqros_market_data``'s equivalent test: the latter runs the app on a
separate event loop, incompatible with an asyncpg engine created on the
test's own loop.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from aqros_feature_store.adapters import db as db_adapter
from aqros_feature_store.adapters.repository import SqlAlchemyFeatureDefinitionRepository
from aqros_feature_store.api.deps import get_market_data_source, get_session
from aqros_feature_store.domain.feature_definitions import FEATURE_REGISTRY
from aqros_feature_store.domain.models import BarInterval, OHLCVBar
from aqros_feature_store.domain.ports import MarketDataSource

pytestmark = pytest.mark.integration


class _FakeMarketDataSource(MarketDataSource):
    """A MarketDataSource double so API tests never call a real HTTP service."""

    def __init__(self) -> None:
        self.symbol_data: dict[str, list[OHLCVBar]] = {}

    async def get_bars(
        self,
        symbol: str,
        *,
        start: date | None = None,
        end: date | None = None,
        interval: BarInterval = BarInterval.DAILY,
    ) -> list[OHLCVBar]:
        return self.symbol_data.get(symbol.upper(), [])


def _bar(symbol: str, day: int, close: float = 100.0) -> OHLCVBar:
    event_time = datetime(2024, 1, 1, tzinfo=UTC) + timedelta(days=day - 1)
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


@pytest.fixture
async def client(
    engine: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
) -> AsyncIterator[AsyncClient]:
    # Import lazily so `Settings()` (and its default DSN) isn't evaluated
    # until the test container fixtures above have set up the schema.
    from aqros_feature_store.app import app, health_registry

    fake_source = _FakeMarketDataSource()

    async def _override_get_session():  # type: ignore[no-untyped-def]
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = _override_get_session
    app.dependency_overrides[get_market_data_source] = lambda: fake_source
    app.state.fake_source = fake_source  # convenience handle for tests
    # Point the readiness DB check at the test container's engine, not the
    # module-level engine (which targets Settings' default DSN).
    health_registry.register("database", lambda: db_adapter.ping(engine))
    health_registry.register("market_data_service", lambda: True)

    # httpx.AsyncClient + ASGITransport never triggers the app's lifespan
    # (see https://github.com/encode/httpx/issues/1441), so the startup
    # definition-seeding step in app.py never runs here — replicate it
    # manually against the test container's session factory.
    async with session_factory() as session:
        repository = SqlAlchemyFeatureDefinitionRepository(session)
        for registration in FEATURE_REGISTRY:
            await repository.upsert_definition(registration.definition)
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        test_client.app_state = app.state  # convenience handle for tests
        yield test_client

    app.dependency_overrides.clear()


async def test_definitions_are_seeded_on_startup(client: AsyncClient) -> None:
    resp = await client.get("/v1/definitions")
    assert resp.status_code == 200
    names = {d["name"] for d in resp.json()}
    assert "sma_20" in names
    assert "rsi_14" in names
    assert len(names) == 18


async def test_get_latest_definition(client: AsyncClient) -> None:
    resp = await client.get("/v1/definitions/sma_20")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "sma_20"
    assert body["category"] == "trend"


async def test_get_unknown_definition_returns_404(client: AsyncClient) -> None:
    resp = await client.get("/v1/definitions/not_a_real_feature")
    assert resp.status_code == 404


async def test_full_pipeline_compute_then_retrieve_values(client: AsyncClient) -> None:
    client.app_state.fake_source.symbol_data["AAPL"] = [
        _bar("AAPL", day, close=100.0 + day) for day in range(1, 31)
    ]

    compute_resp = await client.post(
        "/v1/pipeline/compute", json={"symbol": "aapl", "mode": "full"}
    )
    assert compute_resp.status_code == 201
    run_body = compute_resp.json()
    assert run_body["symbol"] == "AAPL"
    assert run_body["status"] == "succeeded"
    assert run_body["bars_read"] == 30
    assert run_body["features_persisted"] > 0

    values_resp = await client.get("/v1/instruments/AAPL/features/sma_20")
    assert values_resp.status_code == 200
    values_body = values_resp.json()
    assert values_body["total"] > 0
    assert len(values_body["values"]) == values_body["total"]

    stats_resp = await client.get("/v1/instruments/AAPL/features/sma_20/statistics")
    assert stats_resp.status_code == 200
    stats_body = stats_resp.json()
    assert stats_body["count"] == values_body["total"]
    assert stats_body["mean"] is not None


async def test_pipeline_compute_with_no_bars_succeeds_with_zero_counts(
    client: AsyncClient,
) -> None:
    resp = await client.post("/v1/pipeline/compute", json={"symbol": "NOPE", "mode": "full"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["bars_read"] == 0
    assert body["features_persisted"] == 0


async def test_get_run_and_list_runs(client: AsyncClient) -> None:
    client.app_state.fake_source.symbol_data["MSFT"] = [_bar("MSFT", day) for day in range(1, 25)]
    compute_resp = await client.post(
        "/v1/pipeline/compute", json={"symbol": "MSFT", "mode": "full"}
    )
    run_id = compute_resp.json()["id"]

    get_resp = await client.get(f"/v1/pipeline/runs/{run_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["symbol"] == "MSFT"

    list_resp = await client.get("/v1/pipeline/runs", params={"symbol": "MSFT"})
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 1


async def test_get_unknown_run_returns_404(client: AsyncClient) -> None:
    resp = await client.get("/v1/pipeline/runs/999999")
    assert resp.status_code == 404


async def test_feature_values_as_of_filters_point_in_time(client: AsyncClient) -> None:
    client.app_state.fake_source.symbol_data["AAPL"] = [
        _bar("AAPL", day, close=100.0 + day) for day in range(1, 31)
    ]
    await client.post("/v1/pipeline/compute", json={"symbol": "AAPL", "mode": "full"})

    # A cutoff before the computation ran must see nothing — this is the
    # point-in-time query surface end to end, through the API.
    before = "2020-01-01T00:00:00Z"
    resp = await client.get("/v1/instruments/AAPL/features/sma_20", params={"as_of": before})
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


async def test_readiness_reports_database_and_market_data_health(client: AsyncClient) -> None:
    resp = await client.get("/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    checks = {check["name"]: check["healthy"] for check in body["checks"]}
    assert checks["database"] is True
    assert checks["market_data_service"] is True
