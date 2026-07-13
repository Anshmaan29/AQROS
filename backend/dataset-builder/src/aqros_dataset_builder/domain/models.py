"""Core domain types for the dataset-builder service.

Pure data structures — no I/O, no framework imports. Mirrors the pattern
established by ``aqros_market_data.domain.models`` and
``aqros_feature_store.domain.models``.

Two local, decoupled types cross service boundaries here on purpose:
``OHLCVBar`` (read from Market Data's REST API) and the feature values read
from Feature Store's REST API. Neither is imported from those services'
Python packages — CLAUDE.md §7.9 forbids a service depending on another
service's internals; duplicating these small, stable shapes is the price of
respecting that boundary, not an oversight.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
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


@dataclass(frozen=True, slots=True)
class FeatureValue:
    """A single feature value as read from the Feature Store Service's API."""

    symbol: str
    feature_name: str
    feature_version: int
    event_time: datetime
    value: float
    knowledge_time: datetime


class PredictionHorizon(StrEnum):
    """Configurable label horizons, expressed in trading bars (not calendar days).

    "5D" means 5 *trading* bars ahead, not 5 calendar days — daily bars skip
    weekends/holidays, so bar-count is the only horizon definition that is
    unambiguous and directly indexable into a per-symbol bar sequence.
    """

    ONE_DAY = "1d"
    FIVE_DAY = "5d"
    TWENTY_DAY = "20d"

    @property
    def bars(self) -> int:
        """Number of trading bars this horizon spans."""
        return {
            PredictionHorizon.ONE_DAY: 1,
            PredictionHorizon.FIVE_DAY: 5,
            PredictionHorizon.TWENTY_DAY: 20,
        }[self]


class LabelType(StrEnum):
    """Supported supervised-learning label families."""

    BINARY_DIRECTION = "binary_direction"
    FUTURE_RETURN = "future_return"
    VOLATILITY = "volatility"


class SplitStrategy(StrEnum):
    """Supported train/validation/test partitioning schemes.

    Each produces one or more *folds*; every fold assigns every retained row
    a :class:`SplitRole` (or leaves it unassigned/purged). See
    ``domain/splitters.py`` for the exact algorithm behind each strategy —
    the four are deliberately non-overlapping in what they model:

    - ``WALK_FORWARD``: multiple sequential folds, fixed-size sliding window
      (the classic periodic-retrain cadence).
    - ``ROLLING_WINDOW``: a single fold using only the most recent fixed-size
      window of history (the "current production model" split).
    - ``EXPANDING_WINDOW``: a single fold where train uses *all* history
      before the validation/test tail (train grows as more data arrives).
    - ``PURGED_CV``: genuine k-fold cross-validation in time, with purging
      and an embargo gap to prevent label-window leakage across folds
      (López de Prado; `claude_MLResearchFramework.md` §8.4-8.5).
    """

    WALK_FORWARD = "walk_forward"
    ROLLING_WINDOW = "rolling_window"
    EXPANDING_WINDOW = "expanding_window"
    PURGED_CV = "purged_cv"


class SplitRole(StrEnum):
    """The role a row plays within one fold of a split."""

    TRAIN = "train"
    VALIDATION = "validation"
    TEST = "test"


@dataclass(frozen=True, slots=True)
class WalkForwardParams:
    """Parameters for :attr:`SplitStrategy.WALK_FORWARD` (bar counts)."""

    train_size: int
    validation_size: int
    test_size: int
    step_size: int


@dataclass(frozen=True, slots=True)
class RollingWindowParams:
    """Parameters for :attr:`SplitStrategy.ROLLING_WINDOW` (bar counts)."""

    train_size: int
    validation_size: int
    test_size: int


@dataclass(frozen=True, slots=True)
class ExpandingWindowParams:
    """Parameters for :attr:`SplitStrategy.EXPANDING_WINDOW` (bar counts)."""

    validation_size: int
    test_size: int


@dataclass(frozen=True, slots=True)
class PurgedCVParams:
    """Parameters for :attr:`SplitStrategy.PURGED_CV`."""

    n_splits: int
    embargo_size: int


SplitParams = WalkForwardParams | RollingWindowParams | ExpandingWindowParams | PurgedCVParams


@dataclass(frozen=True, slots=True)
class DatasetDefinition:
    """An immutable, versioned specification of a reproducible dataset.

    Definitions are never mutated once created — changing any generation
    parameter (feature list, label definition, horizon, split scheme, date
    range) registers a *new version* under the same name, never overwrites
    the old one (CLAUDE.md §10: "model/dataset versions are immutable and
    content-addressed, never mutated in place"). This is what lets a
    consumer pin to an exact dataset version and reproduce it.
    """

    name: str
    version: int
    symbols: tuple[str, ...]
    feature_names: tuple[str, ...]
    label_type: LabelType
    horizon: PredictionHorizon
    split_strategy: SplitStrategy
    split_params: SplitParams
    start_date: date
    end_date: date
    created_at: datetime


class BuildStatus(StrEnum):
    """Lifecycle state of a dataset build run."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class LabelBalance:
    """Summary statistics of the generated label column."""

    count: int
    mean: float | None
    std: float | None
    minimum: float | None
    maximum: float | None
    # Only populated for BINARY_DIRECTION: fraction of rows labeled 1 (up).
    positive_fraction: float | None = None


@dataclass(frozen=True, slots=True)
class FeatureStatistics:
    """Descriptive statistics for one feature column, computed over the raw
    (pre-clean) joined data — i.e. before rows missing a value are dropped,
    so ``missing_count`` is meaningful."""

    feature_name: str
    count: int
    missing_count: int
    mean: float | None
    std: float | None
    minimum: float | None
    maximum: float | None


@dataclass(frozen=True, slots=True)
class DatasetQualityReport:
    """Dataset quality metrics and basic data-quality validation findings.

    This is separate from — and never gates — the leakage audit
    (``domain/validation.py``): quality metrics describe the *character* of
    the generated dataset for a consumer (how much data is missing, how
    balanced the classes are, per-feature summary statistics), whereas the
    leakage audit is the hard go/no-go gate on whether the dataset may be
    persisted at all.
    """

    total_rows: int
    duplicate_row_count: int
    missing_value_counts: dict[str, int]
    class_balance: dict[str, float]
    feature_statistics: tuple[FeatureStatistics, ...]
    validation_passed: bool
    validation_findings: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class DatasetManifest:
    """A versioned, reproducibility manifest accompanying every generated dataset.

    Written alongside the dataset's Parquet artifact (never overwritten —
    one manifest per build run) so that anyone downloading the dataset has,
    in one self-contained document, everything needed to understand *and
    reproduce* exactly how it was built (CLAUDE.md §5: "Any model, dataset,
    feature, or result reconstructs bit-for-bit from an immutable manifest
    (data snapshot + code SHA + config)").
    """

    dataset_name: str
    dataset_version: int
    build_run_id: int
    symbols: tuple[str, ...]
    feature_names: tuple[str, ...]
    feature_versions: dict[str, int]
    label_type: LabelType
    label_definition: str
    horizon: PredictionHorizon
    split_strategy: SplitStrategy
    split_params: dict[str, int]
    start_date: date
    end_date: date
    created_at: datetime
    row_count: int
    checksum: str
    checksum_algorithm: str
    git_commit: str | None
    market_data_source_url: str
    feature_store_source_url: str
    quality_report: DatasetQualityReport


@dataclass(frozen=True, slots=True)
class DatasetBuildRun:
    """An audit record of one dataset-generation pipeline execution.

    This is the "leakage-clearance certificate" concept from
    `claude_MLResearchFramework.md` §2 (Stage 3: "Output: a registered
    dataset artifact + a leakage-clearance certificate") — every run records
    whether the automated leakage audit passed and, if not, exactly why.
    """

    dataset_name: str
    dataset_version: int
    status: BuildStatus
    started_at: datetime
    bars_read: int = 0
    rows_generated: int = 0
    rows_rejected: int = 0
    rejection_reasons: list[str] = field(default_factory=list)
    leakage_audit_passed: bool | None = None
    leakage_audit_findings: list[str] = field(default_factory=list)
    label_balance: LabelBalance | None = None
    row_counts_by_role: dict[str, int] = field(default_factory=dict)
    quality_report: DatasetQualityReport | None = None
    parquet_path: str | None = None
    manifest_path: str | None = None
    completed_at: datetime | None = None
    error_message: str | None = None
    id: int | None = None
