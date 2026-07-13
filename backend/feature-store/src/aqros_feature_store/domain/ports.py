"""Ports (abstract interfaces) the domain depends on.

These are the seams between pure business logic and I/O. ``domain/`` and
``api/`` depend only on these protocols; concrete implementations live in
``adapters/`` and are wired in via dependency injection (``api/deps.py``) —
the same pattern ``aqros_market_data.domain.ports`` establishes.

``MarketDataSource`` is the boundary-respecting substitute for reading
market-data's database directly (forbidden by CLAUDE.md §7.9): it is
implemented in ``adapters/market_data_client.py`` as an HTTP client against
market-data's *published* REST API. Nothing in ``domain/`` knows or cares
that the bars arrived over HTTP rather than a shared table.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime

from aqros_feature_store.domain.models import (
    BarInterval,
    FeatureComputationRun,
    FeatureDefinition,
    FeatureStatistics,
    FeatureValue,
    OHLCVBar,
)


class MarketDataSource(ABC):
    """Port for reading validated OHLCV data owned by the Market Data Service."""

    @abstractmethod
    async def get_bars(
        self,
        symbol: str,
        *,
        start: date | None = None,
        end: date | None = None,
        interval: BarInterval = BarInterval.DAILY,
    ) -> list[OHLCVBar]:
        """Fetch stored OHLCV bars for ``symbol``, ordered by ``event_time`` ascending.

        Implementations must raise :class:`MarketDataSourceError` on failure
        (unreachable service, non-2xx response) — never return a silently
        empty/partial result to mask an error.
        """


class MarketDataSourceError(RuntimeError):
    """Raised when the Market Data Service is unreachable or returns an error."""


class FeatureDefinitionRepository(ABC):
    """Port for persisting and retrieving feature definitions (the catalog)."""

    @abstractmethod
    async def upsert_definition(self, definition: FeatureDefinition) -> None:
        """Register or update a feature definition, keyed by (name, version)."""

    @abstractmethod
    async def get_definition(self, name: str, version: int) -> FeatureDefinition | None:
        """Fetch one feature definition by (name, version)."""

    @abstractmethod
    async def get_latest_definition(self, name: str) -> FeatureDefinition | None:
        """Fetch the highest registered version of a named feature."""

    @abstractmethod
    async def list_definitions(self) -> list[FeatureDefinition]:
        """List every registered feature definition."""


class FeatureValueRepository(ABC):
    """Port for persisting and retrieving computed feature values."""

    @abstractmethod
    async def upsert_values(self, values: list[FeatureValue]) -> int:
        """Insert or update values, keyed by (symbol, feature_name, feature_version, event_time).

        Upsert (not insert) is required because recomputation (full or
        incremental re-run) must not raise or duplicate rows.
        """

    @abstractmethod
    async def get_values(
        self,
        symbol: str,
        feature_name: str,
        *,
        feature_version: int | None = None,
        start: date | None = None,
        end: date | None = None,
        as_of: datetime | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[FeatureValue]:
        """Retrieve stored values, ordered by ``event_time`` ascending.

        ``as_of`` enforces point-in-time correctness: when set, only values
        with ``knowledge_time <= as_of`` are returned (docs/claude_ROI.md
        §17) — the query is structurally incapable of returning a value that
        was not yet knowable as of that instant.
        """

    @abstractmethod
    async def count_values(
        self,
        symbol: str,
        feature_name: str,
        *,
        feature_version: int | None = None,
        start: date | None = None,
        end: date | None = None,
        as_of: datetime | None = None,
    ) -> int:
        """Count stored values matching the given filters (for pagination totals)."""

    @abstractmethod
    async def get_latest_event_time(
        self, symbol: str, feature_name: str, feature_version: int
    ) -> datetime | None:
        """Return the most recent ``event_time`` already computed, or ``None``.

        This is the high-water mark incremental updates key off: a run only
        needs to (re)compute bars at or after this point (minus a lookback
        buffer for indicators with trailing windows).
        """

    @abstractmethod
    async def compute_statistics(
        self,
        symbol: str,
        feature_name: str,
        feature_version: int,
        *,
        start: date | None = None,
        end: date | None = None,
    ) -> FeatureStatistics:
        """Compute count/mean/std/min/max for a feature over an optional date range."""


class FeatureComputationRunRepository(ABC):
    """Port for persisting the audit trail of pipeline executions."""

    @abstractmethod
    async def create_run(self, run: FeatureComputationRun) -> FeatureComputationRun:
        """Persist a new run record (typically in ``RUNNING`` status) and return it with its id."""

    @abstractmethod
    async def complete_run(self, run: FeatureComputationRun) -> None:
        """Update an existing run record with its final status and counters."""

    @abstractmethod
    async def get_run(self, run_id: int) -> FeatureComputationRun | None:
        """Fetch one computation run by id."""

    @abstractmethod
    async def list_runs(
        self, *, symbol: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[FeatureComputationRun]:
        """List computation runs, most recent first, optionally filtered by symbol."""
