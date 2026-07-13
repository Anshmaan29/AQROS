"""Core domain types for the feature-store service.

Pure data structures — no I/O, no framework imports. Mirrors the pattern
established by ``aqros_market_data.domain.models``: these types are what
``domain/`` logic and ports speak in; the API layer maps its own Pydantic
schemas to/from these, and the adapters layer maps ORM rows and HTTP
responses to/from these.

Note: ``OHLCVBar`` here is a *local* type, intentionally not imported from
``aqros_market_data``. The feature-store consumes market data through
market-data's published REST API (see ``adapters/market_data_client.py``),
never its database or internal Python types — CLAUDE.md §7.9 forbids a
service reaching into another service's internals. Duplicating this small,
stable shape is the price of that boundary, not an oversight.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class BarInterval(StrEnum):
    """OHLCV bar resolution (kept in sync with market-data's own enum)."""

    DAILY = "1d"
    WEEKLY = "1wk"
    MONTHLY = "1mo"


@dataclass(frozen=True, slots=True)
class OHLCVBar:
    """An OHLCV bar as read from the Market Data Service's API."""

    symbol: str
    event_time: datetime
    interval: BarInterval
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    source: str
    knowledge_time: datetime
    adjusted_close: Decimal | None = None


class FeatureCategory(StrEnum):
    """Groupings of feature definitions, matching the milestone requirements."""

    TREND = "trend"
    MOMENTUM = "momentum"
    VOLATILITY = "volatility"
    VOLUME = "volume"
    RETURNS = "returns"
    ROLLING_STATS = "rolling_stats"


@dataclass(frozen=True, slots=True)
class FeatureDefinition:
    """Metadata describing a versioned, named feature.

    Definitions are immutable once created: changing the computation logic
    of a feature creates a *new version* (e.g. ``sma_20@2``), never mutates
    the existing one in place (CLAUDE.md §10 — "model/dataset versions are
    immutable and content-addressed, never mutated in place"). This is what
    lets a consumer pin to an exact feature definition and reproduce results.
    """

    name: str
    version: int
    category: FeatureCategory
    description: str
    parameters: dict[str, float]
    min_bars_required: int


@dataclass(frozen=True, slots=True)
class FeatureValue:
    """A single computed feature value for one instrument at one point in time.

    Bitemporal per project convention: ``event_time`` is the bar this value
    describes; ``knowledge_time`` is when the *feature* became knowable (i.e.
    when it was computed) — not to be confused with the underlying bar's own
    knowledge_time. A feature can only be computed once its inputs are known,
    so ``knowledge_time`` is always >= ``event_time`` and is stamped at
    computation time, mirroring how market-data stamps ingestion time.
    """

    symbol: str
    feature_name: str
    feature_version: int
    event_time: datetime
    value: float
    knowledge_time: datetime
    computation_run_id: int | None = None


class ComputationStatus(StrEnum):
    """Lifecycle state of a feature computation run."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ComputationMode(StrEnum):
    """Whether a run recomputed all history or only new/changed bars."""

    FULL = "full"
    INCREMENTAL = "incremental"


@dataclass(frozen=True, slots=True)
class FeatureComputationRun:
    """An audit record of one feature-engineering pipeline execution."""

    symbol: str
    mode: ComputationMode
    status: ComputationStatus
    started_at: datetime
    bars_read: int = 0
    features_computed: int = 0
    features_persisted: int = 0
    features_rejected: int = 0
    rejection_reasons: list[str] = field(default_factory=list)
    completed_at: datetime | None = None
    error_message: str | None = None
    id: int | None = None


@dataclass(frozen=True, slots=True)
class FeatureStatistics:
    """Summary statistics for one feature, over some symbol and time range."""

    symbol: str
    feature_name: str
    feature_version: int
    count: int
    mean: float | None
    std: float | None
    minimum: float | None
    maximum: float | None
