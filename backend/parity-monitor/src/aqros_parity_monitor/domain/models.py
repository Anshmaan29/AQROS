"""Domain models for the Parity Monitor.

Defines ``ComparisonStatus`` (enum of possible comparison outcomes),
``FeatureComparisonResult`` (the individual feature comparison), and
``ParityReport`` (the aggregated check result for one symbol).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class ComparisonStatus(StrEnum):
    """Outcome of a single feature comparison."""

    MATCH = "match"
    OUTSIDE_TOLERANCE = "outside_tolerance"
    MISSING_OFFLINE = "missing_offline"
    MISSING_ONLINE = "missing_online"
    TYPE_MISMATCH = "type_mismatch"
    VERSION_MISMATCH = "version_mismatch"
    STALE = "stale"
    ERROR = "error"


@dataclass(frozen=True)
class FeatureComparisonResult:
    """The result of comparing one feature across offline and online stores.

    Every comparison produces a status and a human-readable reason. For
    numeric features, the difference and tolerance are recorded so consumers
    can aggregate skew metrics.
    """

    feature_name: str
    offline_value: Any | None
    online_value: Any | None
    status: ComparisonStatus
    reason: str = ""
    difference: float | None = None
    tolerance: float | None = None
    offline_version: int | None = None
    online_version: int | None = None

    @property
    def passed(self) -> bool:
        """True if the feature is consistent between offline and online."""
        return self.status is ComparisonStatus.MATCH


@dataclass
class ParityReport:
    """Aggregated result of checking all features for one symbol.

    Generated after every parity check and persisted for audit and trend
    analysis.
    """

    symbol: str
    timestamp: datetime
    correlation_id: str | None = None
    feature_count: int = 0
    matching_count: int = 0
    failed_count: int = 0
    missing_count: int = 0
    stale_count: int = 0
    version_mismatch_count: int = 0
    type_mismatch_count: int = 0
    maximum_difference: float | None = None
    average_difference: float | None = None
    comparisons: list[FeatureComparisonResult] = field(default_factory=list)
    failure_reasons: dict[str, str] = field(default_factory=dict)
    latency_ms: float | None = None
    schema_version: str = "1.0"

    @property
    def passed(self) -> bool:
        """True if every compared feature matched within tolerance."""
        return self.failed_count == 0 and self.missing_count == 0


@dataclass(frozen=True)
class ParityStatistics:
    """Aggregate parity statistics across many reports."""

    total_checks: int = 0
    total_matches: int = 0
    total_failures: int = 0
    total_skew: int = 0
    total_stale: int = 0
    total_missing: int = 0
    total_version_mismatches: int = 0
    average_latency_ms: float = 0.0
    overall_passing_rate: float = 0.0
