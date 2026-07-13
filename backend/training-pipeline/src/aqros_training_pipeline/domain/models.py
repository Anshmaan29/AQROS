"""Pure domain model for the Training Pipeline service.

Frozen/slots dataclasses and StrEnums only — no I/O, no framework
dependencies. This mirrors ``aqros_dataset_builder.domain.models`` exactly
in spirit: everything here is data, and every side effect (HTTP calls,
database access, filesystem access) is pushed behind the ports defined in
``domain/ports.py``.

``DatasetManifest`` and ``DatasetBuildRun`` are local, decoupled copies of
the Dataset Builder's own shapes, populated only from its published REST
API responses (CLAUDE.md §7.9) — never imported from
``aqros_dataset_builder`` itself, exactly as ``aqros_dataset_builder``
duplicates ``OHLCVBar`` rather than importing it from ``aqros_market_data``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum

import pandas as pd


class ModelType(StrEnum):
    """The four supported candidate model types (Requirement 7)."""

    LOGISTIC_REGRESSION = "logistic_regression"
    RANDOM_FOREST = "random_forest"
    XGBOOST = "xgboost"
    LIGHTGBM = "lightgbm"


class SplitRole(StrEnum):
    """Read-only mirror of the Dataset Builder's own split-role enum.

    The Training Pipeline never constructs a value of this type itself —
    it only parses it verbatim from downloaded dataset rows (Requirement 5).
    """

    TRAIN = "train"
    VALIDATION = "validation"
    TEST = "test"


class TrainingRunStatus(StrEnum):
    """Lifecycle states of a ``TrainingRun`` (see design.md Section 15)."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class DatasetManifest:
    """Local, decoupled copy of the Dataset Builder's manifest shape.

    Populated exclusively from ``GET /v1/runs/{run_id}/manifest`` responses.
    """

    dataset_name: str
    dataset_version: int
    build_run_id: int
    checksum: str
    checksum_algorithm: str
    feature_names: tuple[str, ...]
    feature_versions: dict[str, int]
    label_type: str
    label_definition: str
    horizon: str
    split_strategy: str
    split_params: dict[str, int]
    start_date: date
    end_date: date
    created_at: datetime
    row_count: int
    git_commit: str | None
    market_data_source_url: str
    feature_store_source_url: str
    quality_report: dict[str, object]


@dataclass(frozen=True, slots=True)
class DatasetBuildRun:
    """Local, decoupled copy of the Dataset Builder's build-run shape.

    Populated exclusively from ``GET /v1/runs/{run_id}`` responses. Carries
    only the fields the Training Pipeline actually needs (the leakage-audit
    gate, Requirement 4) rather than the Dataset Builder's full internal
    representation.
    """

    id: int
    dataset_name: str
    dataset_version: int
    leakage_audit_passed: bool | None
    leakage_audit_findings: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class TrainingRequest:
    """A caller's request to train one or more ``ModelType``s (Requirement 7)."""

    dataset_name: str
    build_run_id: int
    model_types: tuple[ModelType, ...]
    hyperparameters: dict[ModelType, dict[str, object]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FoldFrames:
    """The ``train``/``test``-role row slices of one fold, produced by
    ``Fold_Partitioner.partition`` (Requirements 5.1-5.5).

    Both slices are plain, read-only views (no copy, no reorder) of the
    original downloaded ``Dataset_Artifact`` DataFrame, restricted to the
    rows whose existing ``fold`` column equals ``fold`` and whose existing
    ``split_role`` column equals ``train``/``test`` respectively — never a
    newly computed or re-split partition. ``validation``-role rows for the
    fold are deliberately omitted: the Training Pipeline never trains or
    evaluates on them (only ``train`` and ``test`` roles are used, per
    Requirement 5.5 and Requirement 6.1).
    """

    fold: int
    train: pd.DataFrame
    test: pd.DataFrame


@dataclass(frozen=True, slots=True)
class ConfusionMatrix:
    """A binary-classification confusion matrix for one fold's evaluation."""

    true_negative: int
    false_positive: int
    false_negative: int
    true_positive: int


@dataclass(frozen=True, slots=True)
class PerFoldMetrics:
    """Metrics computed independently from one fold's ``test``-role rows."""

    fold: int
    accuracy: float
    precision: float
    recall: float
    f1_score: float
    roc_auc: float | None
    confusion_matrix: ConfusionMatrix
    test_row_count: int


@dataclass(frozen=True, slots=True)
class AggregatedMetrics:
    """Mean/std of ``PerFoldMetrics`` across every fold of one ``TrainedModel``."""

    accuracy_mean: float
    accuracy_std: float
    precision_mean: float
    precision_std: float
    recall_mean: float
    recall_std: float
    f1_mean: float
    f1_std: float
    roc_auc_mean: float | None
    roc_auc_std: float | None
    evaluated_fold_count: int
    roc_auc_evaluated_fold_count: int


@dataclass(frozen=True, slots=True)
class ReproducibilityMetadata:
    """Everything needed to reproduce a ``TrainedModel`` (Requirement 12)."""

    model_version: int
    dataset_name: str
    dataset_version: int
    dataset_checksum: str
    manifest_reference: str
    git_commit: str | None
    trained_at: datetime
    hyperparameters: dict[str, object]
    aggregated_metrics: AggregatedMetrics


@dataclass(frozen=True, slots=True)
class TrainedModel:
    """A single trained-and-evaluated candidate model.

    ``model_name`` is always the composite ``f"{dataset_name}__{model_type}"``
    string (Key Design Decision 3) — never the bare ``ModelType`` value.
    ``Model_Version`` is assigned per this composite name, so training the
    same ``ModelType`` against two different dataset names produces two
    fully independent version sequences.
    """

    model_name: str
    model_type: ModelType
    model_version: int
    training_run_id: int
    dataset_name: str
    dataset_version: int
    artifact_path: str
    per_fold_metrics: tuple[PerFoldMetrics, ...]
    aggregated_metrics: AggregatedMetrics
    feature_importance: dict[str, float]
    reproducibility_metadata: ReproducibilityMetadata
    created_at: datetime
    id: int | None = None


@dataclass(frozen=True, slots=True)
class ModelTypeOutcome:
    """The per-``ModelType`` result of one attempt within a ``TrainingRun``."""

    model_type: ModelType
    trained_model_id: int | None
    error_message: str | None


@dataclass(frozen=True, slots=True)
class TrainingRun:
    """One execution of the training pipeline for a ``TrainingRequest``.

    Reports ``succeeded`` if and only if every requested ``ModelType``
    produced a persisted ``TrainedModel`` (Key Design Decision 8, revised);
    any single failure marks the whole run ``failed`` even though sibling
    ``ModelType``s that trained successfully keep their persisted
    ``TrainedModel`` rows, artifacts, and versions.
    """

    dataset_name: str
    build_run_id: int
    requested_model_types: tuple[ModelType, ...]
    status: TrainingRunStatus
    started_at: datetime
    outcomes: tuple[ModelTypeOutcome, ...] = field(default_factory=tuple)
    completed_at: datetime | None = None
    error_message: str | None = None
    id: int | None = None
