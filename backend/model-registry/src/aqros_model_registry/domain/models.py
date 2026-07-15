"""Pure domain model for the Model Registry service.

Frozen/slots dataclasses and StrEnums only — no I/O, no framework
dependencies. This mirrors ``aqros_training_pipeline.domain.models`` exactly
in spirit: everything here is data, and every side effect (HTTP calls,
database access, filesystem access) is pushed behind the ports defined in
``domain/ports.py``.

``PerFoldMetrics``, ``AggregatedMetrics``, ``MetricsRecord``,
``ReproducibilityMetadata`` and ``TrainedModelRecord`` are local, decoupled
copies of the Training Pipeline's own shapes, populated only from its
published REST API responses (CLAUDE.md §7.9) — never imported from
``aqros_training_pipeline`` itself, exactly as ``aqros_training_pipeline``
duplicates ``DatasetManifest`` rather than importing it from
``aqros_dataset_builder``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class LifecycleState(StrEnum):
    """The forward-only lifecycle states of a ``ModelVersion`` (Requirement 11)."""

    REGISTERED = "registered"
    VALIDATED = "validated"
    STAGING = "staging"
    PRODUCTION = "production"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


class ApprovalState(StrEnum):
    """The approval state of a governed transition (Requirements 12-14)."""

    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class PrincipalKind(StrEnum):
    """Distinguishes human from automated principals (Requirements 14.7, 21.2)."""

    HUMAN = "human"
    AUTOMATED = "automated"


@dataclass(frozen=True, slots=True)
class PerFoldMetrics:
    """Local, decoupled copy of the Training Pipeline's per-fold metrics shape."""

    fold: int
    accuracy: float
    precision: float
    recall: float
    f1_score: float
    roc_auc: float | None
    test_row_count: int


@dataclass(frozen=True, slots=True)
class AggregatedMetrics:
    """Local, decoupled copy of the Training Pipeline's aggregated metrics shape."""

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
class MetricsRecord:
    """The complete metrics payload recorded for a ``ModelVersion`` (Requirement 10)."""

    per_fold: tuple[PerFoldMetrics, ...]
    aggregated: AggregatedMetrics
    feature_importance: dict[str, float]


@dataclass(frozen=True, slots=True)
class DatasetVersionRef:
    """The dataset-version reference pinned into a ``ModelVersion``'s lineage."""

    dataset_name: str
    dataset_version: int
    dataset_checksum: str


@dataclass(frozen=True, slots=True)
class ReproducibilityMetadata:
    """Everything needed to reproduce a ``ModelVersion`` (Requirements 9, 10)."""

    model_version: int
    dataset_name: str
    dataset_version: int
    dataset_checksum: str
    feature_versions: dict[str, int]
    git_commit: str | None  # None => explicitly absent (Requirement 6.3)
    training_run_id: int
    trained_at: datetime
    hyperparameters: dict[str, object]
    aggregated_metrics: AggregatedMetrics


@dataclass(frozen=True, slots=True)
class ValidationEvidence:
    """Opaque evidence attached to gate REGISTERED->VALIDATED (Requirement 12)."""

    kind: str  # e.g. "backtest_report"
    reference: str  # URI / dossier id (opaque; Registry never computes it)
    attached_at: datetime


@dataclass(frozen=True, slots=True)
class TrainedModelRecord:
    """Local, decoupled copy of the Training Pipeline's trained-model record.

    Populated exclusively from the Training Pipeline's published REST API —
    never imported from ``aqros_training_pipeline`` (CLAUDE.md §7.9).
    """

    model_name: str
    model_type: str
    model_version: int
    training_run_id: int
    dataset_name: str
    dataset_version: int
    dataset_checksum: str
    checksum_algorithm: str
    artifact_checksum: str
    feature_versions: dict[str, int]
    per_fold_metrics: tuple[PerFoldMetrics, ...]
    aggregated_metrics: AggregatedMetrics
    feature_importance: dict[str, float]
    git_commit: str | None
    trained_at: datetime
    hyperparameters: dict[str, object]


@dataclass(frozen=True, slots=True)
class ModelVersion:
    """An immutable, fully-lineaged record of one trained model (Requirement 3).

    ``model_name`` is always the composite ``f"{dataset_name}__{model_type}"``
    string, inherited verbatim from the Training Pipeline (Key Design Decision
    4). Identity fields are written once and never modified; only
    ``lifecycle_state``, ``approval_state`` and ``validation_evidence`` are
    mutable (Requirements 3.4, 3.5, 22.3).
    """

    model_name: str  # == f"{dataset_name}__{model_type}" (inherited)
    model_type: str
    version: int
    training_run_id: int
    dataset_version: DatasetVersionRef
    feature_versions: dict[str, int]
    metrics: MetricsRecord
    artifact_path: str
    artifact_checksum: str
    checksum_algorithm: str
    git_commit: str | None
    reproducibility_metadata: ReproducibilityMetadata
    lifecycle_state: LifecycleState
    approval_state: ApprovalState
    validation_evidence: ValidationEvidence | None
    created_at: datetime
    id: int | None = None


@dataclass(frozen=True, slots=True)
class Approval:
    """A single approver's decision toward a ``PromotionRequest`` (Requirement 14)."""

    approver: str
    approver_kind: PrincipalKind
    decision: str  # "approve" | "reject"
    reason: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PromotionRequest:
    """A request to move a ``ModelVersion`` between lifecycle states (Requirement 13)."""

    model_name: str
    version: int
    from_state: LifecycleState
    to_state: LifecycleState
    requester: str
    justification: str
    approval_state: ApprovalState
    approvals: tuple[Approval, ...] = field(default_factory=tuple)
    is_rollback: bool = False
    created_at: datetime | None = None
    id: int | None = None


@dataclass(frozen=True, slots=True)
class PromotionHistoryEntry:
    """An append-only record of one applied transition or rollback (Requirement 17)."""

    model_name: str
    version: int
    from_state: LifecycleState
    to_state: LifecycleState
    requester: str
    approvers: tuple[str, ...]
    justification: str
    is_rollback: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """An append-only audit record of a privileged action (Requirement 18)."""

    action: str  # registered | transition_requested | approved | rejected | rolled_back | artifact_served
    actor: str
    model_name: str | None
    version: int | None
    before_state: str | None
    after_state: str | None
    justification: str | None
    correlation_id: str
    created_at: datetime
