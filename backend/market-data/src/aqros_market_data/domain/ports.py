"""Ports (abstract interfaces) the domain depends on.

These are the seams between pure business logic and I/O. ``domain/`` and
``api/`` depend only on these protocols; concrete implementations live in
``adapters/`` and are wired in via dependency injection (``api/deps.py``).

This is what satisfies the "provider abstraction" requirement: adding Alpaca,
Polygon, or TwelveData later means writing one new class that implements
``MarketDataProvider`` in ``adapters/providers/`` — no change to
``domain/services.py``, the API routes, or persistence.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from aqros_market_data.domain.models import BarInterval, Instrument, OHLCVBar


class MarketDataProvider(ABC):
    """Port for any historical OHLCV data source."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short, stable identifier for this provider (stored as `source`)."""

    @abstractmethod
    async def fetch_history(
        self,
        symbol: str,
        *,
        start: date,
        end: date,
        interval: BarInterval = BarInterval.DAILY,
    ) -> list[OHLCVBar]:
        """Fetch historical OHLCV bars for ``symbol`` in ``[start, end]`` (inclusive).

        Implementations must raise :class:`MarketDataProviderError` (or a
        subclass) on any failure — never return partial/silent-empty data
        to mask an error.
        """


class MarketDataProviderError(RuntimeError):
    """Raised when a provider fails to fetch data (network, rate limit, bad symbol)."""


class SymbolNotFoundError(MarketDataProviderError):
    """Raised when the provider has no data for the requested symbol."""


class BarRepository(ABC):
    """Port for persisting and retrieving OHLCV bars."""

    @abstractmethod
    async def upsert_bars(self, bars: list[OHLCVBar]) -> int:
        """Insert or update bars, keyed by (symbol, event_time, interval, source).

        Returns the number of rows affected. Upsert (not insert) is required
        because re-ingesting an overlapping date range must not raise or
        duplicate rows — historical re-downloads are a normal operation.
        """

    @abstractmethod
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
        """Retrieve stored bars for ``symbol``, ordered by ``event_time`` ascending."""

    @abstractmethod
    async def count_bars(
        self,
        symbol: str,
        *,
        start: date | None = None,
        end: date | None = None,
        interval: BarInterval = BarInterval.DAILY,
    ) -> int:
        """Count stored bars matching the given filters (for pagination totals)."""


class InstrumentRepository(ABC):
    """Port for persisting and retrieving instrument reference data."""

    @abstractmethod
    async def upsert_instrument(self, instrument: Instrument) -> None:
        """Insert or update an instrument by its symbol."""

    @abstractmethod
    async def get_instrument(self, symbol: str) -> Instrument | None:
        """Retrieve an instrument by symbol, or ``None`` if unknown."""

    @abstractmethod
    async def list_instruments(self, *, limit: int = 1000, offset: int = 0) -> list[Instrument]:
        """List known instruments."""
