"""Business logic: ingestion orchestration.

Pure orchestration over ports — no direct DB or HTTP client usage here. This
is what the "provider abstraction" and "repository pattern" requirements
compose into: a service depends on ``MarketDataProvider`` and
``BarRepository`` (interfaces), never on ``YFinanceProvider`` or a concrete
SQLAlchemy repository. Swapping either is a wiring change in ``api/deps.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from aqros_market_data.domain.models import BarInterval, Instrument, OHLCVBar
from aqros_market_data.domain.ports import (
    BarRepository,
    InstrumentRepository,
    MarketDataProvider,
)
from aqros_market_data.domain.validation import validate_bars


@dataclass(frozen=True, slots=True)
class IngestionResult:
    """Outcome of an ingestion run for a single symbol."""

    symbol: str
    fetched: int
    persisted: int
    rejected: int
    rejection_reasons: list[str] = field(default_factory=list)


class IngestionService:
    """Downloads historical OHLCV data and persists it after validation."""

    def __init__(
        self,
        provider: MarketDataProvider,
        bar_repository: BarRepository,
        instrument_repository: InstrumentRepository,
    ) -> None:
        self._provider = provider
        self._bar_repository = bar_repository
        self._instrument_repository = instrument_repository

    async def ingest_symbol(
        self,
        symbol: str,
        *,
        start: date,
        end: date,
        interval: BarInterval = BarInterval.DAILY,
        now: datetime | None = None,
    ) -> IngestionResult:
        """Fetch, validate, and persist historical bars for one symbol.

        Invalid bars are rejected (never persisted) and reported back rather
        than silently dropped or raised as a hard failure — a partial-quality
        vendor delivery should not block the valid rows in it (fail-open on
        the alpha/research path, per CLAUDE.md §5).
        """
        bars = await self._provider.fetch_history(symbol, start=start, end=end, interval=interval)

        # `now` is captured *after* the (potentially slow, network-bound)
        # fetch completes, not before — the provider stamps each bar's
        # `knowledge_time` at fetch completion, so the validator's reference
        # clock must be at least that late or every bar would be flagged as
        # having a future knowledge_time purely due to fetch latency.
        reference_now = now if now is not None else datetime.now(UTC)
        results = validate_bars(bars, now=reference_now)

        valid_bars = [r.bar for r in results if r.is_valid]
        rejection_reasons = [
            f"{r.bar.event_time.isoformat()}: {'; '.join(r.violations)}"
            for r in results
            if not r.is_valid
        ]

        await self._instrument_repository.upsert_instrument(Instrument(symbol=symbol.upper()))

        persisted = 0
        if valid_bars:
            persisted = await self._bar_repository.upsert_bars(valid_bars)

        return IngestionResult(
            symbol=symbol.upper(),
            fetched=len(bars),
            persisted=persisted,
            rejected=len(rejection_reasons),
            rejection_reasons=rejection_reasons,
        )


class MarketDataQueryService:
    """Read-side operations over stored market data."""

    def __init__(
        self,
        bar_repository: BarRepository,
        instrument_repository: InstrumentRepository,
    ) -> None:
        self._bar_repository = bar_repository
        self._instrument_repository = instrument_repository

    async def get_bars(
        self,
        symbol: str,
        *,
        start: date | None = None,
        end: date | None = None,
        interval: BarInterval = BarInterval.DAILY,
        limit: int = 1000,
        offset: int = 0,
    ) -> tuple[list[OHLCVBar], int]:
        """Return (bars, total_count) for the given filters."""
        bars = await self._bar_repository.get_bars(
            symbol, start=start, end=end, interval=interval, limit=limit, offset=offset
        )
        total = await self._bar_repository.count_bars(
            symbol, start=start, end=end, interval=interval
        )
        return bars, total

    async def get_instrument(self, symbol: str) -> Instrument | None:
        return await self._instrument_repository.get_instrument(symbol)

    async def list_instruments(self, *, limit: int = 1000, offset: int = 0) -> list[Instrument]:
        return await self._instrument_repository.list_instruments(limit=limit, offset=offset)
