"""Unit tests for IngestionService, using fakes for both ports.

No DB, no network — this proves the domain orchestration logic (fetch ->
validate -> persist) is correct independent of any concrete adapter, which is
the entire point of the provider/repository abstraction (CLAUDE.md §5).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from aqros_market_data.domain.models import BarInterval, Instrument, OHLCVBar
from aqros_market_data.domain.ports import (
    BarRepository,
    InstrumentRepository,
    MarketDataProvider,
    SymbolNotFoundError,
)
from aqros_market_data.domain.services import IngestionService

FIXED_NOW = datetime(2024, 1, 15, tzinfo=UTC)


def _bar(symbol: str, day: int, **overrides: object) -> OHLCVBar:
    defaults: dict[str, object] = {
        "symbol": symbol,
        "event_time": datetime(2024, 1, day, tzinfo=UTC),
        "interval": BarInterval.DAILY,
        "open": Decimal("100"),
        "high": Decimal("105"),
        "low": Decimal("99"),
        "close": Decimal("103"),
        "volume": 1000,
        "source": "fake",
        "knowledge_time": datetime(2024, 1, day, tzinfo=UTC),
    }
    defaults.update(overrides)
    return OHLCVBar(**defaults)  # type: ignore[arg-type]


class FakeProvider(MarketDataProvider):
    def __init__(self, bars: list[OHLCVBar] | None = None, raise_not_found: bool = False) -> None:
        self._bars = bars or []
        self._raise_not_found = raise_not_found

    @property
    def name(self) -> str:
        return "fake"

    async def fetch_history(
        self, symbol: str, *, start: date, end: date, interval: BarInterval = BarInterval.DAILY
    ) -> list[OHLCVBar]:
        if self._raise_not_found:
            raise SymbolNotFoundError(f"no data for {symbol}")
        return self._bars


class SlowRealClockProvider(MarketDataProvider):
    """Simulates a real provider: stamps knowledge_time with the wall clock

    *after* a (simulated) network delay, exactly like ``YFinanceProvider``
    does. Used to regression-test the ordering bug where the service captured
    its validation reference clock *before* the (slow) fetch instead of
    after, which caused every bar's real knowledge_time to look like it was
    "in the future" relative to that stale reference.
    """

    def __init__(self, delay_seconds: float) -> None:
        self._delay_seconds = delay_seconds

    @property
    def name(self) -> str:
        return "slow-real-clock"

    async def fetch_history(
        self, symbol: str, *, start: date, end: date, interval: BarInterval = BarInterval.DAILY
    ) -> list[OHLCVBar]:
        import asyncio

        await asyncio.sleep(self._delay_seconds)
        fetch_completed_at = datetime.now(UTC)
        return [
            OHLCVBar(
                symbol=symbol.upper(),
                event_time=datetime(2024, 1, 10, tzinfo=UTC),
                interval=interval,
                open=Decimal("100"),
                high=Decimal("105"),
                low=Decimal("99"),
                close=Decimal("103"),
                volume=1000,
                source=self.name,
                knowledge_time=fetch_completed_at,
            )
        ]


class FakeBarRepository(BarRepository):
    def __init__(self) -> None:
        self.stored: list[OHLCVBar] = []

    async def upsert_bars(self, bars: list[OHLCVBar]) -> int:
        self.stored.extend(bars)
        return len(bars)

    async def get_bars(
        self,
        symbol: str,
        *,
        start: date | None = None,
        end: date | None = None,
        interval: BarInterval = BarInterval.DAILY,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[OHLCVBar]:
        return [b for b in self.stored if b.symbol == symbol.upper()]

    async def count_bars(
        self,
        symbol: str,
        *,
        start: date | None = None,
        end: date | None = None,
        interval: BarInterval = BarInterval.DAILY,
    ) -> int:
        return len([b for b in self.stored if b.symbol == symbol.upper()])


class FakeInstrumentRepository(InstrumentRepository):
    def __init__(self) -> None:
        self.instruments: dict[str, Instrument] = {}

    async def upsert_instrument(self, instrument: Instrument) -> None:
        self.instruments[instrument.symbol] = instrument

    async def get_instrument(self, symbol: str) -> Instrument | None:
        return self.instruments.get(symbol.upper())

    async def list_instruments(self, *, limit: int = 1000, offset: int = 0) -> list[Instrument]:
        return list(self.instruments.values())


@pytest.mark.asyncio
async def test_ingest_symbol_persists_all_valid_bars() -> None:
    bars = [_bar("AAPL", 10), _bar("AAPL", 11), _bar("AAPL", 12)]
    provider = FakeProvider(bars=bars)
    bar_repo = FakeBarRepository()
    instrument_repo = FakeInstrumentRepository()
    service = IngestionService(provider, bar_repo, instrument_repo)

    result = await service.ingest_symbol(
        "aapl", start=date(2024, 1, 10), end=date(2024, 1, 12), now=FIXED_NOW
    )

    assert result.symbol == "AAPL"
    assert result.fetched == 3
    assert result.persisted == 3
    assert result.rejected == 0
    assert len(bar_repo.stored) == 3
    assert instrument_repo.instruments["AAPL"].symbol == "AAPL"


@pytest.mark.asyncio
async def test_ingest_symbol_rejects_invalid_bars_without_persisting_them() -> None:
    valid_bar = _bar("AAPL", 10)
    invalid_bar = _bar("AAPL", 11, volume=-5)
    provider = FakeProvider(bars=[valid_bar, invalid_bar])
    bar_repo = FakeBarRepository()
    instrument_repo = FakeInstrumentRepository()
    service = IngestionService(provider, bar_repo, instrument_repo)

    result = await service.ingest_symbol(
        "AAPL", start=date(2024, 1, 10), end=date(2024, 1, 11), now=FIXED_NOW
    )

    assert result.fetched == 2
    assert result.persisted == 1
    assert result.rejected == 1
    assert len(result.rejection_reasons) == 1
    assert len(bar_repo.stored) == 1


@pytest.mark.asyncio
async def test_ingest_symbol_propagates_symbol_not_found() -> None:
    provider = FakeProvider(raise_not_found=True)
    bar_repo = FakeBarRepository()
    instrument_repo = FakeInstrumentRepository()
    service = IngestionService(provider, bar_repo, instrument_repo)

    with pytest.raises(SymbolNotFoundError):
        await service.ingest_symbol(
            "BOGUS", start=date(2024, 1, 1), end=date(2024, 1, 2), now=FIXED_NOW
        )


@pytest.mark.asyncio
async def test_ingest_symbol_does_not_reject_bars_due_to_slow_fetch() -> None:
    # Regression test: the validation reference clock must be captured
    # *after* the (potentially slow) provider fetch, not before, or a bar
    # whose real knowledge_time is stamped post-fetch will look like it is
    # from the future relative to a stale pre-fetch reference clock. No
    # `now=` override here — this must hold against the real wall clock.
    provider = SlowRealClockProvider(delay_seconds=0.05)
    bar_repo = FakeBarRepository()
    instrument_repo = FakeInstrumentRepository()
    service = IngestionService(provider, bar_repo, instrument_repo)

    result = await service.ingest_symbol("AAPL", start=date(2024, 1, 10), end=date(2024, 1, 10))

    assert result.rejected == 0
    assert result.persisted == 1


@pytest.mark.asyncio
async def test_ingest_symbol_with_no_bars_still_registers_instrument() -> None:
    provider = FakeProvider(bars=[])
    bar_repo = FakeBarRepository()
    instrument_repo = FakeInstrumentRepository()
    service = IngestionService(provider, bar_repo, instrument_repo)

    result = await service.ingest_symbol(
        "AAPL", start=date(2024, 1, 1), end=date(2024, 1, 2), now=FIXED_NOW
    )

    assert result.fetched == 0
    assert result.persisted == 0
    assert "AAPL" in instrument_repo.instruments
