"""SQLAlchemy implementations of the domain repository ports.

Each repository maps ORM rows to/from the pure domain types in
``domain/models.py`` — business logic never sees an ORM row or session.
Mirrors ``aqros_training_pipeline.adapters.repository``.

Nested value objects (feature versions, metrics, reproducibility metadata,
validation evidence, promotion approvers) are serialized to JSON text columns,
keeping the schema portable and the domain layer free of ORM concerns — the
same tradeoff the Training Pipeline makes with its ``*_json`` columns.

Identity fields on ``ModelVersion`` are write-once: ``create_model_version``
inserts via ``add()`` + ``flush()`` and never updates a row; only
``set_lifecycle_state`` and ``attach_validation_evidence`` mutate a persisted
row, and each touches only its own mutable column(s) (Requirements 3.4, 3.5,
22.2, 22.3). ``PromotionHistoryEntry`` and ``AuditEvent`` rows are append-only —
there is no update/delete code path (Requirements 17.2, 18.2, 22.4). No
repository ever calls ``commit()``; the request-scoped session owns the
transaction.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aqros_model_registry.adapters.orm import (
    ApprovalORM,
    AuditEventORM,
    ModelVersionORM,
    PromotionHistoryORM,
    PromotionRequestORM,
    RegisteredModelORM,
)
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
from aqros_model_registry.domain.ports import (
    AuditRepository,
    ModelVersionRepository,
    PromotionRepository,
)

# ---------------------------------------------------------------------------
# JSON (de)serialization helpers for nested value objects
# ---------------------------------------------------------------------------


def _per_fold_from_dict(data: dict[str, Any]) -> PerFoldMetrics:
    return PerFoldMetrics(
        fold=int(data["fold"]),
        accuracy=float(data["accuracy"]),
        precision=float(data["precision"]),
        recall=float(data["recall"]),
        f1_score=float(data["f1_score"]),
        roc_auc=None if data["roc_auc"] is None else float(data["roc_auc"]),
        test_row_count=int(data["test_row_count"]),
    )


def _aggregated_from_dict(data: dict[str, Any]) -> AggregatedMetrics:
    def opt(key: str) -> float | None:
        value = data[key]
        return None if value is None else float(value)

    return AggregatedMetrics(
        accuracy_mean=float(data["accuracy_mean"]),
        accuracy_std=float(data["accuracy_std"]),
        precision_mean=float(data["precision_mean"]),
        precision_std=float(data["precision_std"]),
        recall_mean=float(data["recall_mean"]),
        recall_std=float(data["recall_std"]),
        f1_mean=float(data["f1_mean"]),
        f1_std=float(data["f1_std"]),
        roc_auc_mean=opt("roc_auc_mean"),
        roc_auc_std=opt("roc_auc_std"),
        evaluated_fold_count=int(data["evaluated_fold_count"]),
        roc_auc_evaluated_fold_count=int(data["roc_auc_evaluated_fold_count"]),
    )


def _metrics_from_dict(data: dict[str, Any]) -> MetricsRecord:
    return MetricsRecord(
        per_fold=tuple(_per_fold_from_dict(item) for item in data["per_fold"]),
        aggregated=_aggregated_from_dict(data["aggregated"]),
        feature_importance={str(k): float(v) for k, v in dict(data["feature_importance"]).items()},
    )


def _metadata_from_dict(data: dict[str, Any]) -> ReproducibilityMetadata:
    return ReproducibilityMetadata(
        model_version=int(data["model_version"]),
        dataset_name=str(data["dataset_name"]),
        dataset_version=int(data["dataset_version"]),
        dataset_checksum=str(data["dataset_checksum"]),
        feature_versions={str(k): int(v) for k, v in dict(data["feature_versions"]).items()},
        git_commit=None if data["git_commit"] is None else str(data["git_commit"]),
        training_run_id=int(data["training_run_id"]),
        trained_at=datetime.fromisoformat(str(data["trained_at"])),
        hyperparameters=dict(data["hyperparameters"]),
        aggregated_metrics=_aggregated_from_dict(data["aggregated_metrics"]),
    )


def _evidence_from_dict(data: dict[str, Any]) -> ValidationEvidence:
    return ValidationEvidence(
        kind=str(data["kind"]),
        reference=str(data["reference"]),
        attached_at=datetime.fromisoformat(str(data["attached_at"])),
    )


def _to_domain_model_version(row: ModelVersionORM) -> ModelVersion:
    feature_versions = {
        str(k): int(v) for k, v in dict(json.loads(row.feature_versions_json)).items()
    }
    metrics = _metrics_from_dict(json.loads(row.metrics_json))
    metadata = _metadata_from_dict(json.loads(row.reproducibility_metadata_json))
    validation_evidence = (
        None
        if row.validation_evidence_json is None
        else _evidence_from_dict(json.loads(row.validation_evidence_json))
    )
    return ModelVersion(
        model_name=row.model_name,
        # model_name is the composite f"{dataset_name}__{model_type}" inherited
        # verbatim from the Training Pipeline (design Key Decision 4), so the
        # model_type is its final segment.
        model_type=row.model_name.rsplit("__", 1)[-1],
        version=row.version,
        training_run_id=row.training_run_id,
        dataset_version=DatasetVersionRef(
            dataset_name=row.dataset_name,
            dataset_version=row.dataset_version,
            dataset_checksum=row.dataset_checksum,
        ),
        feature_versions=feature_versions,
        metrics=metrics,
        artifact_path=row.artifact_path,
        artifact_checksum=row.artifact_checksum,
        checksum_algorithm=row.checksum_algorithm,
        git_commit=row.git_commit,
        reproducibility_metadata=metadata,
        lifecycle_state=LifecycleState(row.lifecycle_state),
        approval_state=ApprovalState(row.approval_state),
        validation_evidence=validation_evidence,
        created_at=row.created_at,
        id=row.id,
    )


# ---------------------------------------------------------------------------
# Serialization helpers (domain value objects -> JSON text columns)
# ---------------------------------------------------------------------------


def _dump_json(value: Any) -> str:
    """Serialize a domain value object (or plain mapping) to JSON text.

    ``default=str`` lets ``datetime`` fields round-trip through
    ``fromisoformat`` on the way back in the ``_*_from_dict`` helpers above.
    """

    return json.dumps(value, default=str)


def _derive_idempotency_key(request: PromotionRequest) -> str:
    """Derive a deterministic idempotency key for a ``PromotionRequest``.

    The ``PromotionRequest`` domain dataclass carries no idempotency key, yet
    the ``promotion_requests`` table enforces ``UNIQUE (model_version_id,
    idempotency_key)`` (Requirement 19.11). We derive a stable key from the
    request's own transition fields so that a replayed request for the same
    ``(model_version, from_state, to_state, requester, is_rollback)`` collapses
    onto the same row rather than creating a duplicate governance record.
    """

    return (
        f"{request.from_state.value}:{request.to_state.value}:"
        f"{request.requester}:{int(request.is_rollback)}"
    )


# ---------------------------------------------------------------------------
# Cross-table lookup helpers (composite key <-> surrogate id)
# ---------------------------------------------------------------------------


async def _resolve_model_version_id(session: AsyncSession, model_name: str, version: int) -> int:
    """Resolve the surrogate ``model_versions.id`` for a composite key.

    Promotion and history rows reference a ``ModelVersion`` by its surrogate
    id, while the domain speaks in ``(model_name, version)`` — this bridges the
    two. Raises ``ValueError`` if no such version has been persisted.
    """

    stmt = select(ModelVersionORM.id).where(
        ModelVersionORM.model_name == model_name,
        ModelVersionORM.version == version,
    )
    result = (await session.execute(stmt)).scalar_one_or_none()
    if result is None:
        raise ValueError(f"No ModelVersion persisted for ({model_name!r}, {version})")
    return int(result)


async def _lookup_version_ref(session: AsyncSession, model_version_id: int) -> tuple[str, int]:
    """Return the ``(model_name, version)`` composite key for a surrogate id."""

    stmt = select(ModelVersionORM.model_name, ModelVersionORM.version).where(
        ModelVersionORM.id == model_version_id
    )
    row = (await session.execute(stmt)).one()
    return str(row[0]), int(row[1])


# ---------------------------------------------------------------------------
# Promotion / audit domain reconstruction helpers
# ---------------------------------------------------------------------------


def _to_domain_approval(row: ApprovalORM) -> Approval:
    return Approval(
        approver=row.approver,
        approver_kind=PrincipalKind(row.approver_kind),
        decision=row.decision,
        reason=row.reason,
        created_at=row.created_at,
    )


def _to_domain_promotion_request(
    row: PromotionRequestORM,
    model_name: str,
    version: int,
    approvals: tuple[Approval, ...],
) -> PromotionRequest:
    return PromotionRequest(
        model_name=model_name,
        version=version,
        from_state=LifecycleState(row.from_state),
        to_state=LifecycleState(row.to_state),
        requester=row.requester,
        justification=row.justification,
        approval_state=ApprovalState(row.approval_state),
        approvals=approvals,
        is_rollback=row.is_rollback,
        created_at=row.created_at,
        id=row.id,
    )


def _to_domain_history(
    row: PromotionHistoryORM, model_name: str, version: int
) -> PromotionHistoryEntry:
    approvers = tuple(str(a) for a in json.loads(row.approvers_json))
    return PromotionHistoryEntry(
        model_name=model_name,
        version=version,
        from_state=LifecycleState(row.from_state),
        to_state=LifecycleState(row.to_state),
        requester=row.requester,
        approvers=approvers,
        justification=row.justification,
        is_rollback=row.is_rollback,
        created_at=row.created_at,
    )


def _to_domain_audit(row: AuditEventORM) -> AuditEvent:
    return AuditEvent(
        action=row.action,
        actor=row.actor,
        model_name=row.model_name,
        version=row.version,
        before_state=row.before_state,
        after_state=row.after_state,
        justification=row.justification,
        correlation_id=row.correlation_id,
        created_at=row.created_at,
    )


# ---------------------------------------------------------------------------
# ModelVersion repository
# ---------------------------------------------------------------------------


class SqlAlchemyModelVersionRepository(ModelVersionRepository):
    """Postgres-backed ``ModelVersion`` repository.

    Identity fields are write-once: ``create_model_version`` inserts via
    ``add()`` + ``flush()`` and never updates a row. Only
    ``set_lifecycle_state`` and ``attach_validation_evidence`` mutate a
    persisted row, and each touches only its own mutable column(s)
    (Requirements 3.4, 3.5, 22.2, 22.3). Never commits — the request-scoped
    session owns the transaction.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _ensure_registered_model(self, model_version: ModelVersion) -> int:
        """Look up (or create) the ``RegisteredModelORM`` for a ``model_name``.

        A ``ModelVersion`` belongs to a logical ``Registered_Model`` identity;
        the first version registered for a name lazily creates that identity.
        Returns the surrogate ``registered_models.id``.
        """

        stmt = select(RegisteredModelORM).where(
            RegisteredModelORM.model_name == model_version.model_name
        )
        existing = (await self._session.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            return existing.id
        registered = RegisteredModelORM(
            model_name=model_version.model_name,
            model_type=model_version.model_type,
            created_at=model_version.created_at,
        )
        self._session.add(registered)
        await self._session.flush()
        return registered.id

    async def create_model_version(self, model_version: ModelVersion) -> ModelVersion:
        registered_model_id = await self._ensure_registered_model(model_version)
        validation_evidence_json = (
            None
            if model_version.validation_evidence is None
            else _dump_json(asdict(model_version.validation_evidence))
        )
        orm_row = ModelVersionORM(
            registered_model_id=registered_model_id,
            model_name=model_version.model_name,
            version=model_version.version,
            training_run_id=model_version.training_run_id,
            dataset_name=model_version.dataset_version.dataset_name,
            dataset_version=model_version.dataset_version.dataset_version,
            dataset_checksum=model_version.dataset_version.dataset_checksum,
            feature_versions_json=_dump_json(model_version.feature_versions),
            metrics_json=_dump_json(asdict(model_version.metrics)),
            artifact_path=model_version.artifact_path,
            artifact_checksum=model_version.artifact_checksum,
            checksum_algorithm=model_version.checksum_algorithm,
            git_commit=model_version.git_commit,
            reproducibility_metadata_json=_dump_json(
                asdict(model_version.reproducibility_metadata)
            ),
            lifecycle_state=model_version.lifecycle_state.value,
            approval_state=model_version.approval_state.value,
            validation_evidence_json=validation_evidence_json,
            created_at=model_version.created_at,
        )
        self._session.add(orm_row)
        await self._session.flush()
        return _to_domain_model_version(orm_row)

    async def get(self, model_name: str, version: int) -> ModelVersion | None:
        stmt = select(ModelVersionORM).where(
            ModelVersionORM.model_name == model_name,
            ModelVersionORM.version == version,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_domain_model_version(row) if row is not None else None

    async def list(
        self,
        *,
        model_name: str | None = None,
        lifecycle_state: LifecycleState | None = None,
    ) -> list[ModelVersion]:
        stmt = select(ModelVersionORM)
        if model_name is not None:
            stmt = stmt.where(ModelVersionORM.model_name == model_name)
        if lifecycle_state is not None:
            stmt = stmt.where(ModelVersionORM.lifecycle_state == lifecycle_state.value)
        stmt = stmt.order_by(ModelVersionORM.model_name.asc(), ModelVersionORM.version.asc())
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_domain_model_version(row) for row in rows]

    async def _require_version_orm(self, model_name: str, version: int) -> ModelVersionORM:
        stmt = select(ModelVersionORM).where(
            ModelVersionORM.model_name == model_name,
            ModelVersionORM.version == version,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise ValueError(f"No ModelVersion persisted for ({model_name!r}, {version})")
        return row

    async def set_lifecycle_state(
        self, model_name: str, version: int, state: LifecycleState
    ) -> ModelVersion:
        orm_row = await self._require_version_orm(model_name, version)
        # Mutate ONLY the lifecycle_state column — identity fields are write-once.
        orm_row.lifecycle_state = state.value
        await self._session.flush()
        return _to_domain_model_version(orm_row)

    async def get_latest_version(self, model_name: str) -> int | None:
        stmt = select(func.max(ModelVersionORM.version)).where(
            ModelVersionORM.model_name == model_name
        )
        result = (await self._session.execute(stmt)).scalar_one_or_none()
        return None if result is None else int(result)

    async def resolve_production(self, model_name: str) -> ModelVersion | None:
        stmt = select(ModelVersionORM).where(
            ModelVersionORM.model_name == model_name,
            ModelVersionORM.lifecycle_state == LifecycleState.PRODUCTION.value,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_domain_model_version(row) if row is not None else None

    async def attach_validation_evidence(
        self, model_name: str, version: int, evidence: ValidationEvidence
    ) -> ModelVersion:
        orm_row = await self._require_version_orm(model_name, version)
        # Mutate ONLY the validation_evidence_json column (Requirement 22.3).
        orm_row.validation_evidence_json = _dump_json(asdict(evidence))
        await self._session.flush()
        return _to_domain_model_version(orm_row)


# ---------------------------------------------------------------------------
# Promotion repository
# ---------------------------------------------------------------------------


class SqlAlchemyPromotionRepository(PromotionRepository):
    """Postgres-backed promotion-request, approval, and history repository.

    ``PromotionHistoryEntry`` rows are append-only — there is no update or
    delete code path (Requirements 17.1, 17.2, 22.4). Never commits.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _load_approvals(self, request_id: int) -> tuple[Approval, ...]:
        stmt = (
            select(ApprovalORM)
            .where(ApprovalORM.promotion_request_id == request_id)
            .order_by(ApprovalORM.created_at.asc(), ApprovalORM.id.asc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return tuple(_to_domain_approval(row) for row in rows)

    async def _reconstruct_request(self, orm_row: PromotionRequestORM) -> PromotionRequest:
        model_name, version = await _lookup_version_ref(self._session, orm_row.model_version_id)
        approvals = await self._load_approvals(orm_row.id)
        return _to_domain_promotion_request(orm_row, model_name, version, approvals)

    async def _require_request_orm(self, request_id: int) -> PromotionRequestORM:
        stmt = select(PromotionRequestORM).where(PromotionRequestORM.id == request_id)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise ValueError(f"No PromotionRequest persisted for id {request_id}")
        return row

    async def create_request(self, request: PromotionRequest) -> PromotionRequest:
        if request.created_at is None:
            raise ValueError("PromotionRequest.created_at must be set before persistence")
        model_version_id = await _resolve_model_version_id(
            self._session, request.model_name, request.version
        )
        orm_row = PromotionRequestORM(
            model_version_id=model_version_id,
            from_state=request.from_state.value,
            to_state=request.to_state.value,
            requester=request.requester,
            justification=request.justification,
            approval_state=request.approval_state.value,
            is_rollback=request.is_rollback,
            idempotency_key=_derive_idempotency_key(request),
            created_at=request.created_at,
        )
        self._session.add(orm_row)
        await self._session.flush()
        return await self._reconstruct_request(orm_row)

    async def add_approval(self, request_id: int, approval: Approval) -> PromotionRequest:
        orm_approval = ApprovalORM(
            promotion_request_id=request_id,
            approver=approval.approver,
            approver_kind=approval.approver_kind.value,
            decision=approval.decision,
            reason=approval.reason,
            created_at=approval.created_at,
        )
        self._session.add(orm_approval)
        await self._session.flush()
        orm_row = await self._require_request_orm(request_id)
        return await self._reconstruct_request(orm_row)

    async def set_request_state(self, request_id: int, state: ApprovalState) -> PromotionRequest:
        orm_row = await self._require_request_orm(request_id)
        orm_row.approval_state = state.value
        await self._session.flush()
        return await self._reconstruct_request(orm_row)

    async def get_request(self, request_id: int) -> PromotionRequest | None:
        stmt = select(PromotionRequestORM).where(PromotionRequestORM.id == request_id)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return await self._reconstruct_request(row)

    async def append_history(self, entry: PromotionHistoryEntry) -> PromotionHistoryEntry:
        model_version_id = await _resolve_model_version_id(
            self._session, entry.model_name, entry.version
        )
        orm_row = PromotionHistoryORM(
            model_version_id=model_version_id,
            from_state=entry.from_state.value,
            to_state=entry.to_state.value,
            requester=entry.requester,
            approvers_json=_dump_json(list(entry.approvers)),
            justification=entry.justification,
            is_rollback=entry.is_rollback,
            created_at=entry.created_at,
        )
        self._session.add(orm_row)
        await self._session.flush()
        return _to_domain_history(orm_row, entry.model_name, entry.version)

    async def list_history(self, model_name: str, version: int) -> list[PromotionHistoryEntry]:
        model_version_id = await _resolve_model_version_id(self._session, model_name, version)
        stmt = (
            select(PromotionHistoryORM)
            .where(PromotionHistoryORM.model_version_id == model_version_id)
            .order_by(PromotionHistoryORM.created_at.asc(), PromotionHistoryORM.id.asc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_domain_history(row, model_name, version) for row in rows]


# ---------------------------------------------------------------------------
# Audit repository
# ---------------------------------------------------------------------------


class SqlAlchemyAuditRepository(AuditRepository):
    """Postgres-backed, append-only audit-trail repository.

    ``append`` is the only write operation — ``Audit_Event`` rows are never
    updated or deleted (Requirements 18.1, 18.2, 22.4). Never commits.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(self, event: AuditEvent) -> AuditEvent:
        orm_row = AuditEventORM(
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
        self._session.add(orm_row)
        await self._session.flush()
        return _to_domain_audit(orm_row)

    async def list(
        self,
        *,
        model_name: str | None = None,
        correlation_id: str | None = None,
    ) -> list[AuditEvent]:
        stmt = select(AuditEventORM)
        if model_name is not None:
            stmt = stmt.where(AuditEventORM.model_name == model_name)
        if correlation_id is not None:
            stmt = stmt.where(AuditEventORM.correlation_id == correlation_id)
        stmt = stmt.order_by(AuditEventORM.created_at.asc(), AuditEventORM.id.asc())
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_domain_audit(row) for row in rows]
