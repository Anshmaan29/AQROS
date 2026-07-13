"""End-to-end API integration tests against a real Postgres.

Exercises the full stack (FastAPI routes -> services -> SQLAlchemy
repositories -> Postgres) with the market-data provider swapped for an
in-memory fake, proving the ingestion and retrieval endpoints work together
without depending on network access to Yahoo Finance.

Uses ``httpx.AsyncClient`` (ASGI transport) rather than
``fastapi.testclient.TestClient``: the latter runs the app in a separate
worker thread with its own event loop, which is incompatible with an
asyncpg-backed async engine created on the test's own loop (asyncpg futures
cannot cross event loops). The async client runs the app on the *same* loop
as the test, avoiding that class of error entirely.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from aqros_market_data.adapters import db as db_adapter
from aqros_market_data.api.deps import get_provider, get_session
from aqros_market_data.domain.models import BarInterval, OHLCVBar
from aqros_market_data.domain.ports import MarketDataProvider, SymbolNotFoundError

pytestmark = pytest.mark.integration


class _FakeProvider(MarketDataProvider):
    """A provider double so API tests never hit the network."""

    def __init__(self) -> None:
        self.symbol_data: dict[str, list[OHLCVBar]] = {}

    @property
    def name(self) -> str:
        return "fake"

    async def fetch_history(
        self, symbol: str, *, start: date, end: date, interval: BarInterval = BarInterval.DAILY
    ) -> list[OHLCVBar]:
        bars = self.symbol_data.get(symbol.upper())
        if bars is None:
            raise SymbolNotFoundError(f"no data for {symbol}")
        return bars


def _bar(symbol: str, day: int) -> OHLCVBar:
    return OHLCVBar(
        symbol=symbol,
        event_time=datetime(2024, 1, day, tzinfo=UTC),
        interval=BarInterval.DAILY,
        open=Decimal("100"),
        high=Decimal("105"),
        low=Decimal("99"),
        close=Decimal("103"),
        volume=1000,
        source="fake",
        knowledge_time=datetime(2024, 1, day, tzinfo=UTC),
    )


@pytest.fixture
async def client(
    engine: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
) -> AsyncIterator[AsyncClient]:
    # Import lazily so `Settings()` (and its default DSN) isn't evaluated
    # until the test container fixtures above have set up the schema.
    from aqros_market_data.app import app, health_registry

    fake_provider = _FakeProvider()

    async def _override_get_session():  # type: ignore[no-untyped-def]
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = _override_get_session
    app.dependency_overrides[get_provider] = lambda: fake_provider
    app.state.fake_provider = fake_provider  # convenience handle for tests
    # Point the readiness DB check at the test container's engine, not the
    # module-level engine (which targets Settings' default DSN).
    health_registry.register("database", lambda: db_adapter.ping(engine))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        test_client.app_state = app.state  # convenience handle for tests
        yield test_client

    app.dependency_overrides.clear()


async def test_ingest_then_retrieve_bars(client: AsyncClient) -> None:
    client.app_state.fake_provider.symbol_data["AAPL"] = [
        _bar("AAPL", 10),
        _bar("AAPL", 11),
    ]

    ingest_resp = await client.post(
        "/v1/ingestion",
        json={"symbol": "aapl", "start": "2024-01-10", "end": "2024-01-11"},
    )
    assert ingest_resp.status_code == 201
    body = ingest_resp.json()
    assert body["symbol"] == "AAPL"
    assert body["persisted"] == 2
    assert body["rejected"] == 0

    bars_resp = await client.get("/v1/instruments/AAPL/bars")
    assert bars_resp.status_code == 200
    bars_body = bars_resp.json()
    assert bars_body["total"] == 2
    assert len(bars_body["bars"]) == 2

    instrument_resp = await client.get("/v1/instruments/AAPL")
    assert instrument_resp.status_code == 200
    assert instrument_resp.json()["symbol"] == "AAPL"


async def test_ingest_unknown_symbol_returns_404(client: AsyncClient) -> None:
    resp = await client.post(
        "/v1/ingestion",
        json={"symbol": "BOGUS", "start": "2024-01-01", "end": "2024-01-02"},
    )
    assert resp.status_code == 404


async def test_get_bars_for_unknown_symbol_returns_empty_page(client: AsyncClient) -> None:
    resp = await client.get("/v1/instruments/NOPE/bars")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["bars"] == []


async def test_get_unknown_instrument_returns_404(client: AsyncClient) -> None:
    resp = await client.get("/v1/instruments/NOPE")
    assert resp.status_code == 404


async def test_list_instruments_after_ingestion(client: AsyncClient) -> None:
    client.app_state.fake_provider.symbol_data["MSFT"] = [_bar("MSFT", 5)]
    await client.post(
        "/v1/ingestion", json={"symbol": "MSFT", "start": "2024-01-05", "end": "2024-01-05"}
    )

    resp = await client.get("/v1/instruments")
    assert resp.status_code == 200
    symbols = [i["symbol"] for i in resp.json()]
    assert "MSFT" in symbols


async def test_readiness_reports_database_health(client: AsyncClient) -> None:
    resp = await client.get("/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert any(check["name"] == "database" and check["healthy"] for check in body["checks"])
