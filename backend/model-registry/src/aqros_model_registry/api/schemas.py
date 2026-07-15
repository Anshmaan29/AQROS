"""Pydantic request/response schemas for the Registry API.

Every response schema carries a ``from_domain(...)`` classmethod converter that
projects a pure domain object (from ``domain/models.py`` or ``domain/lineage.py``)
into its transport shape, mirroring ``aqros_training_pipeline.api.schemas``. The
shared :class:`ErrorResponse` envelope is returned with every non-2xx response
and a typed ``404`` for any missing resource (Requirements 19.10, 20.5).

This module is pure transport: it never imports ``aqros_training_pipeline`` and
never performs I/O. ``model_config = ConfigDict(protected_namespaces=())`` is set
on every schema carrying a ``model_*`` field so Pydantic does not treat those
names as protected.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from aqros_model_registry.domain.lineage import Lineage
from aqros_model_registry.domain.models import (
    AggregatedMetrics,
    Approval,
    ApprovalState,
    AuditEvent,
    DatasetVersionRef,
    LifecycleState,
    MetricsRecord,
    ModelVersion,
    PerFoldMetrics,
    PrincipalKind,
    PromotionHistoryEntry,
    PromotionRequest,
    ReproducibilityMetadata,
    ValidationEvidence,
)


class ErrorResponse(BaseModel):
    """Shared error envelope returned with non-2xx responses (Requirement 19.10)."""

    error: str
    detail: str


# --------------------------------------------------------------------------- #
# Requests                                                                     #
# --------------------------------------------------------------------------- #


class RegisterModelRequest(BaseModel):
    """Request body for ``POST /v1/models`` (Requirements 2.1, 19.1).

    Carries only a reference to the Training Pipeline trained model — the
    composite ``model_name``, the inherited ``version``, and the
    ``training_run_id`` — from which the Registry pulls the full record and
    artifact via the Training Pipeline REST API.
    """

    model_config = ConfigDict(protected_namespaces=())

    model_name: str = Field(min_length=1, max_length=128)
    version: int = Field(gt=0)
    training_run_id: int = Field(gt=0)


class ValidationEvidenceSchema(BaseModel):
    """Validation evidence attached to a ``REGISTERED -> VALIDATED`` transition.

    Opaque proof (e.g. a signed backtest-report reference) required to gate the
    validation transition (Requirement 12). The Registry never computes or
    interprets it; it records it immutably as part of the Lineage.
    """

    kind: str = Field(min_length=1, max_length=64)
    reference: str = Field(min_length=1)


class TransitionRequestSchema(BaseModel):
    """Request body for ``POST .../transition`` (Requirements 13.1, 19.6).

    Carries the target ``to_state``, a human-readable ``justification``, and
    optional ``validation_evidence`` (required only for
    ``REGISTERED -> VALIDATED`` per Requirement 12).
    """

    to_state: LifecycleState
    justification: str = Field(min_length=1)
    validation_evidence: ValidationEvidenceSchema | None = None


class ApprovalRequestSchema(BaseModel):
    """Request body for ``POST .../approve`` and ``.../reject`` (Requirement 14).

    Identifies the acting ``approver``, their ``approver_kind`` (only a human
    principal may satisfy a PRODUCTION gate per Requirements 14.7, 21.2), and an
    optional ``reason`` (recorded as the rejection reason on a reject).
    """

    approver: str = Field(min_length=1)
    approver_kind: PrincipalKind
    reason: str | None = None


class RollbackRequestSchema(BaseModel):
    """Request body for ``POST .../rollback`` (Requirements 15.1, 19.6).

    Carries the ``requester`` identity and a ``justification`` for re-promoting
    a previously-PRODUCTION, currently-DEPRECATED Model_Version to PRODUCTION.
    """

    requester: str = Field(min_length=1)
    justification: str = Field(min_length=1)


# --------------------------------------------------------------------------- #
# Metrics + lineage responses                                                  #
# --------------------------------------------------------------------------- #


class DatasetVersionRefResponse(BaseModel):
    """The dataset-version reference pinned into a Model_Version's lineage."""

    dataset_name: str
    dataset_version: int
    dataset_checksum: str

    @classmethod
    def from_domain(cls, ref: DatasetVersionRef) -> DatasetVersionRefResponse:
        return cls(
            dataset_name=ref.dataset_name,
            dataset_version=ref.dataset_version,
            dataset_checksum=ref.dataset_checksum,
        )


class PerFoldMetricsResponse(BaseModel):
    """Per-fold metrics of a Model_Version (Requirement 10.1)."""

    fold: int
    accuracy: float
    precision: float
    recall: float
    f1_score: float
    roc_auc: float | None
    test_row_count: int

    @classmethod
    def from_domain(cls, metrics: PerFoldMetrics) -> PerFoldMetricsResponse:
        return cls(
            fold=metrics.fold,
            accuracy=metrics.accuracy,
            precision=metrics.precision,
            recall=metrics.recall,
            f1_score=metrics.f1_score,
            roc_auc=metrics.roc_auc,
            test_row_count=metrics.test_row_count,
        )


class AggregatedMetricsResponse(BaseModel):
    """Cross-fold aggregated metrics of a Model_Version (Requirement 10.1)."""

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

    @classmethod
    def from_domain(cls, metrics: AggregatedMetrics) -> AggregatedMetricsResponse:
        return cls(**{k: getattr(metrics, k) for k in cls.model_fields})


class MetricsResponse(BaseModel):
    """A Model_Version's full Metrics_Record (Requirements 10.2, 19.4)."""

    model_config = ConfigDict(protected_namespaces=())

    model_name: str
    version: int
    per_fold: list[PerFoldMetricsResponse]
    aggregated: AggregatedMetricsResponse
    feature_importance: dict[str, float]

    @classmethod
    def from_domain(cls, model_name: str, version: int, metrics: MetricsRecord) -> MetricsResponse:
        return cls(
            model_name=model_name,
            version=version,
            per_fold=[PerFoldMetricsResponse.from_domain(m) for m in metrics.per_fold],
            aggregated=AggregatedMetricsResponse.from_domain(metrics.aggregated),
            feature_importance=dict(metrics.feature_importance),
        )


class ReproducibilityMetadataResponse(BaseModel):
    """Immutable reproducibility metadata of a Model_Version (Requirements 9.1, 19.3)."""

    model_config = ConfigDict(protected_namespaces=())

    model_version: int
    dataset_name: str
    dataset_version: int
    dataset_checksum: str
    feature_versions: dict[str, int]
    git_commit: str | None
    training_run_id: int
    trained_at: datetime
    hyperparameters: dict[str, object]
    aggregated_metrics: AggregatedMetricsResponse

    @classmethod
    def from_domain(cls, metadata: ReproducibilityMetadata) -> ReproducibilityMetadataResponse:
        return cls(
            model_version=metadata.model_version,
            dataset_name=metadata.dataset_name,
            dataset_version=metadata.dataset_version,
            dataset_checksum=metadata.dataset_checksum,
            feature_versions=dict(metadata.feature_versions),
            git_commit=metadata.git_commit,
            training_run_id=metadata.training_run_id,
            trained_at=metadata.trained_at,
            hyperparameters=dict(metadata.hyperparameters),
            aggregated_metrics=AggregatedMetricsResponse.from_domain(metadata.aggregated_metrics),
        )


class LineageResponse(BaseModel):
    """The full provenance chain of a Model_Version (Requirements 9.2, 19.3)."""

    model_config = ConfigDict(protected_namespaces=())

    model_name: str
    model_version: int
    dataset_version: DatasetVersionRefResponse
    feature_versions: dict[str, int]
    git_commit: str | None
    training_run_id: int
    trained_at: datetime

    @classmethod
    def from_domain(cls, lineage: Lineage) -> LineageResponse:
        return cls(
            model_name=lineage.model_name,
            model_version=lineage.model_version,
            dataset_version=DatasetVersionRefResponse.from_domain(lineage.dataset_version),
            feature_versions=dict(lineage.feature_versions),
            git_commit=lineage.git_commit,
            training_run_id=lineage.training_run_id,
            trained_at=lineage.trained_at,
        )


# --------------------------------------------------------------------------- #
# Model version response                                                       #
# --------------------------------------------------------------------------- #


class ValidationEvidenceResponse(BaseModel):
    """The immutable validation evidence recorded on a Model_Version (Requirement 12.3)."""

    kind: str
    reference: str
    attached_at: datetime

    @classmethod
    def from_domain(cls, evidence: ValidationEvidence) -> ValidationEvidenceResponse:
        return cls(
            kind=evidence.kind,
            reference=evidence.reference,
            attached_at=evidence.attached_at,
        )


class ModelVersionResponse(BaseModel):
    """A Model_Version's full metadata, Lineage, and Reproducibility_Metadata.

    Serves the detail endpoint (Requirement 19.3) and the list endpoint
    (Requirement 19.2). Identity fields are recorded once and never mutated;
    only ``lifecycle_state``, ``approval_state`` and ``validation_evidence``
    ever change (Requirements 3.4, 3.5).
    """

    model_config = ConfigDict(protected_namespaces=())

    id: int | None
    model_name: str
    model_type: str
    version: int
    training_run_id: int
    dataset_version: DatasetVersionRefResponse
    feature_versions: dict[str, int]
    metrics: MetricsResponse
    artifact_path: str
    artifact_checksum: str
    checksum_algorithm: str
    git_commit: str | None
    reproducibility_metadata: ReproducibilityMetadataResponse
    lifecycle_state: LifecycleState
    approval_state: ApprovalState
    validation_evidence: ValidationEvidenceResponse | None
    created_at: datetime

    @classmethod
    def from_domain(cls, model_version: ModelVersion) -> ModelVersionResponse:
        return cls(
            id=model_version.id,
            model_name=model_version.model_name,
            model_type=model_version.model_type,
            version=model_version.version,
            training_run_id=model_version.training_run_id,
            dataset_version=DatasetVersionRefResponse.from_domain(model_version.dataset_version),
            feature_versions=dict(model_version.feature_versions),
            metrics=MetricsResponse.from_domain(
                model_version.model_name,
                model_version.version,
                model_version.metrics,
            ),
            artifact_path=model_version.artifact_path,
            artifact_checksum=model_version.artifact_checksum,
            checksum_algorithm=model_version.checksum_algorithm,
            git_commit=model_version.git_commit,
            reproducibility_metadata=ReproducibilityMetadataResponse.from_domain(
                model_version.reproducibility_metadata
            ),
            lifecycle_state=model_version.lifecycle_state,
            approval_state=model_version.approval_state,
            validation_evidence=(
                ValidationEvidenceResponse.from_domain(model_version.validation_evidence)
                if model_version.validation_evidence is not None
                else None
            ),
            created_at=model_version.created_at,
        )


# --------------------------------------------------------------------------- #
# Governance / history responses                                               #
# --------------------------------------------------------------------------- #


class PromotionHistoryResponse(BaseModel):
    """One append-only Promotion_History entry (Requirements 17.1, 17.3, 19.8)."""

    model_config = ConfigDict(protected_namespaces=())

    model_name: str
    version: int
    from_state: LifecycleState
    to_state: LifecycleState
    requester: str
    approvers: list[str]
    justification: str
    is_rollback: bool
    created_at: datetime

    @classmethod
    def from_domain(cls, entry: PromotionHistoryEntry) -> PromotionHistoryResponse:
        return cls(
            model_name=entry.model_name,
            version=entry.version,
            from_state=entry.from_state,
            to_state=entry.to_state,
            requester=entry.requester,
            approvers=list(entry.approvers),
            justification=entry.justification,
            is_rollback=entry.is_rollback,
            created_at=entry.created_at,
        )


class ApprovalResponse(BaseModel):
    """One approver's recorded decision toward a ``Promotion_Request`` (Requirement 14)."""

    approver: str
    approver_kind: PrincipalKind
    decision: str
    reason: str | None
    created_at: datetime

    @classmethod
    def from_domain(cls, approval: Approval) -> ApprovalResponse:
        return cls(
            approver=approval.approver,
            approver_kind=approval.approver_kind,
            decision=approval.decision,
            reason=approval.reason,
            created_at=approval.created_at,
        )


class PromotionRequestResponse(BaseModel):
    """A pending or settled ``Promotion_Request`` (Requirements 13.1, 19.6).

    Returned by the transition/approve/reject/rollback endpoints whenever the
    targeted transition is *not* applied immediately — i.e. while the request
    remains ``PENDING`` awaiting one authorized approval or Four_Eyes
    (Requirements 13.2, 14.1, 14.3, 14.4, 15.2). Once the gate is satisfied the
    same endpoints instead return the promoted :class:`ModelVersionResponse`.
    """

    model_config = ConfigDict(protected_namespaces=())

    id: int | None
    model_name: str
    version: int
    from_state: LifecycleState
    to_state: LifecycleState
    requester: str
    justification: str
    approval_state: ApprovalState
    approvals: list[ApprovalResponse]
    is_rollback: bool
    created_at: datetime | None

    @classmethod
    def from_domain(cls, request: PromotionRequest) -> PromotionRequestResponse:
        return cls(
            id=request.id,
            model_name=request.model_name,
            version=request.version,
            from_state=request.from_state,
            to_state=request.to_state,
            requester=request.requester,
            justification=request.justification,
            approval_state=request.approval_state,
            approvals=[ApprovalResponse.from_domain(a) for a in request.approvals],
            is_rollback=request.is_rollback,
            created_at=request.created_at,
        )


class AuditEventResponse(BaseModel):
    """One append-only Audit_History event (Requirements 18.1, 18.3, 19.8)."""

    model_config = ConfigDict(protected_namespaces=())

    action: str
    actor: str
    model_name: str | None
    version: int | None
    before_state: str | None
    after_state: str | None
    justification: str | None
    correlation_id: str
    created_at: datetime

    @classmethod
    def from_domain(cls, event: AuditEvent) -> AuditEventResponse:
        return cls(
            action=event.action,
            actor=event.actor,
            model_name=event.model_name,
            version=event.version,
            before_state=event.before_state,
            after_state=event.after_state,
            justification=event.justification,
            correlation_id=event.correlation_id,
            created_at=event.created_at,
        )


class ProductionResolutionResponse(BaseModel):
    """Resolution of a Registered_Model's current Production_Model (Requirements 16.3, 16.4).

    ``exists`` is ``True`` with the champion in ``production`` when a PRODUCTION
    Model_Version exists; otherwise ``exists`` is ``False`` and ``production`` is
    ``None``, reporting that no Production_Model exists for the family
    (Requirement 16.4).
    """

    model_config = ConfigDict(protected_namespaces=())

    model_name: str
    exists: bool
    production: ModelVersionResponse | None

    @classmethod
    def from_domain(
        cls, model_name: str, production: ModelVersion | None
    ) -> ProductionResolutionResponse:
        return cls(
            model_name=model_name,
            exists=production is not None,
            production=(
                ModelVersionResponse.from_domain(production) if production is not None else None
            ),
        )
