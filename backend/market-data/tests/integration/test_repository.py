"""Integration tests for the SQLAlchemy repositories against a real Postgres.

These prove the upsert-on-conflict logic, filtering, and pagination actually
work against Postgres semantics (e.g. ON CONFLICT, Numeric precision) that a
fake/in-memory repository can't verify.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from aqros_market_data.adapters.repository import (
    SqlAlchemyBarRepository,
    SqlAlchemyInstrumentRepository,
)
from aqros_market_data.domain.models import BarInterval, Instrument, OHLCVBar

pytestmark = pytest.mark.integration


def _bar(day: int, **overrides: object) -> OHLCVBar:
    defaults: dict[str, object] = {
        "symbol": "AAPL",
        "event_time": datetime(2024, 1, day, tzinfo=UTC),
        "interval": BarInterval.DAILY,
        "open": Decimal("100.50"),
        "high": Decimal("105.25"),
        "low": Decimal("99.75"),
        "close": Decimal("103.10"),
        "volume": 1_500_000,
        "source": "yfinance",
        "knowledge_time": datetime(2024, 1, day, tzinfo=UTC),
    }
    defaults.update(overrides)
    return OHLCVBar(**defaults)  # type: ignore[arg-type]


async def test_upsert_bars_inserts_new_rows(db_session: AsyncSession) -> None:
    repo = SqlAlchemyBarRepository(db_session)
    bars = [_bar(10), _bar(11), _bar(12)]

    affected = await repo.upsert_bars(bars)
    await db_session.commit()

    assert affected == 3
    stored = await repo.get_bars("AAPL")
    assert len(stored) == 3
    assert stored[0].event_time < stored[1].event_time < stored[2].event_time


async def test_upsert_bars_updates_on_conflict(db_session: AsyncSession) -> None:
    repo = SqlAlchemyBarRepository(db_session)
    await repo.upsert_bars([_bar(10, close=Decimal("100.00"))])
    await db_session.commit()

    # Re-ingest the same (symbol, event_time, interval, source) with a revised
    # close — this must update in place, not duplicate (repeated historical
    # downloads are a normal operation).
    await repo.upsert_bars([_bar(10, close=Decimal("111.11"))])
    await db_session.commit()

    stored = await repo.get_bars("AAPL")
    assert len(stored) == 1
    assert stored[0].close == Decimal("111.11")


async def test_get_bars_filters_by_date_range(db_session: AsyncSession) -> None:
    repo = SqlAlchemyBarRepository(db_session)
    await repo.upsert_bars([_bar(1), _bar(15), _bar(31)])
    await db_session.commit()

    stored = await repo.get_bars("AAPL", start=date(2024, 1, 10), end=date(2024, 1, 20))

    assert len(stored) == 1
    assert stored[0].event_time.day == 15


async def test_get_bars_pagination(db_session: AsyncSession) -> None:
    repo = SqlAlchemyBarRepository(db_session)
    await repo.upsert_bars([_bar(d) for d in range(1, 11)])
    await db_session.commit()

    page1 = await repo.get_bars("AAPL", limit=5, offset=0)
    page2 = await repo.get_bars("AAPL", limit=5, offset=5)
    total = await repo.count_bars("AAPL")

    assert len(page1) == 5
    assert len(page2) == 5
    assert total == 10
    assert page1[0].event_time != page2[0].event_time


async def test_get_bars_for_unknown_symbol_returns_empty(db_session: AsyncSession) -> None:
    repo = SqlAlchemyBarRepository(db_session)
    stored = await repo.get_bars("NOPE")
    assert stored == []


async def test_instrument_upsert_preserves_enriched_fields(db_session: AsyncSession) -> None:
    repo = SqlAlchemyInstrumentRepository(db_session)
    await repo.upsert_instrument(
        Instrument(symbol="AAPL", name="Apple Inc.", exchange="NASDAQ", currency="USD")
    )
    await db_session.commit()

    # A later upsert that only knows the symbol (as ingestion does) must not
    # blank out the previously-enriched name/exchange/currency.
    await repo.upsert_instrument(Instrument(symbol="AAPL"))
    await db_session.commit()

    fetched = await repo.get_instrument("AAPL")
    assert fetched is not None
    assert fetched.name == "Apple Inc."
    assert fetched.exchange == "NASDAQ"
    assert fetched.currency == "USD"


async def test_list_instruments_orders_by_symbol(db_session: AsyncSession) -> None:
    repo = SqlAlchemyInstrumentRepository(db_session)
    for symbol in ("MSFT", "AAPL", "GOOG"):
        await repo.upsert_instrument(Instrument(symbol=symbol))
    await db_session.commit()

    instruments = await repo.list_instruments()

    assert [i.symbol for i in instruments] == ["AAPL", "GOOG", "MSFT"]
