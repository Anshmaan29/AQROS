"""SQLAlchemy 2.0 ORM models — the persistence schema (design.md Section 7).

Table/column naming follows CLAUDE.md §10 (snake_case, plural table names).
Private to the adapters layer — repositories map rows to/from the domain
types in ``domain/models.py``. Only metadata is persisted here; the
``Model_Artifact`` bytes live in the ``ArtifactStore``, not Postgres.

The schema mirrors the style of ``aqros_training_pipeline.adapters.orm``
(``DeclarativeBase`` + ``Mapped``/``mapped_column``) — ``aqros_training_pipeline``
is never imported (CLAUDE.md §7.9). ``Base.metadata`` is exposed so alembic and
the repositories can reference ``adapters.orm.Base.metadata``.

Key invariants encoded at the database level:

* ``UniqueConstraint(registered_model_id, version)`` on ``model_versions`` — the
  version-uniqueness backstop under concurrent writers (Requirements 3.2, 22.2).
* The partial unique index ``uq_one_production_per_model`` =
  ``UNIQUE (registered_model_id) WHERE lifecycle_state = 'production'`` — the
  single-PRODUCTION invariant, physically impossible to violate even under
  concurrent promotions (Requirements 16.1, 23.3).
* ``UniqueConstraint(model_version_id, idempotency_key)`` on
  ``promotion_requests`` — governance idempotency (Requirement 19.11).

``model_versions`` identity columns (version, checksums, dataset/feature refs,
metrics, reproducibility, artifact_path) are written once and never updated;
only ``lifecycle_state``, ``approval_state`` and ``validation_evidence_json`` are
mutable (Requirements 3.4, 3.5, 22.3). ``promotion_history`` and ``audit_events``
have no update/delete code path (append-only; Requirements 17.2, 18.2, 22.4).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for all model-registry ORM models."""


class RegisteredModelORM(Base):
    """A logical model identity grouping its immutable ``ModelVersion`` rows."""

    __tablename__ = "registered_models"
    __table_args__ = (UniqueConstraint("model_name", name="uq_registered_models_model_name"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    model_name: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    model_type: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ModelVersionORM(Base):
    """An immutable, fully-lineaged record of one trained model (Requirement 3).

    Identity columns are write-once; only ``lifecycle_state``,
    ``approval_state`` and ``validation_evidence_json`` are ever updated
    (Requirements 3.4, 3.5, 22.3).
    """

    __tablename__ = "model_versions"
    __table_args__ = (
        UniqueConstraint("registered_model_id", "version", name="uq_model_versions_model_version"),
        Index("ix_model_versions_model_name", "model_name"),
        # Single-PRODUCTION invariant: at most one PRODUCTION version per
        # registered model, enforced as a Postgres partial unique index
        # (Requirements 16.1, 23.3).
        Index(
            "uq_one_production_per_model",
            "registered_model_id",
            unique=True,
            postgresql_where=text("lifecycle_state = 'production'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    registered_model_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("registered_models.id"), nullable=False
    )
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    training_run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    dataset_name: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset_version: Mapped[int] = mapped_column(Integer, nullable=False)
    dataset_checksum: Mapped[str] = mapped_column(String(128), nullable=False)
    feature_versions_json: Mapped[str] = mapped_column(Text, nullable=False)
    metrics_json: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_path: Mapped[str] = mapped_column(String(512), nullable=False)
    artifact_checksum: Mapped[str] = mapped_column(String(128), nullable=False)
    checksum_algorithm: Mapped[str] = mapped_column(String(32), nullable=False)
    git_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reproducibility_metadata_json: Mapped[str] = mapped_column(Text, nullable=False)
    # Mutable columns (the only ones ever updated).
    lifecycle_state: Mapped[str] = mapped_column(String(16), nullable=False)
    approval_state: Mapped[str] = mapped_column(String(16), nullable=False)
    validation_evidence_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PromotionRequestORM(Base):
    """A request to move a ``ModelVersion`` between lifecycle states (Requirement 13)."""

    __tablename__ = "promotion_requests"
    __table_args__ = (
        UniqueConstraint(
            "model_version_id",
            "idempotency_key",
            name="uq_promotion_requests_version_idempotency_key",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    model_version_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("model_versions.id"), nullable=False
    )
    from_state: Mapped[str] = mapped_column(String(16), nullable=False)
    to_state: Mapped[str] = mapped_column(String(16), nullable=False)
    requester: Mapped[str] = mapped_column(String(128), nullable=False)
    justification: Mapped[str] = mapped_column(Text, nullable=False)
    approval_state: Mapped[str] = mapped_column(String(16), nullable=False)
    is_rollback: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ApprovalORM(Base):
    """A single approver's decision toward a ``PromotionRequest`` (Requirement 14)."""

    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    promotion_request_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("promotion_requests.id"), nullable=False
    )
    approver: Mapped[str] = mapped_column(String(128), nullable=False)
    approver_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PromotionHistoryORM(Base):
    """An append-only record of one applied transition or rollback (Requirement 17)."""

    __tablename__ = "promotion_history"
    __table_args__ = (Index("ix_promotion_history_model_version_id", "model_version_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    model_version_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("model_versions.id"), nullable=False
    )
    from_state: Mapped[str] = mapped_column(String(16), nullable=False)
    to_state: Mapped[str] = mapped_column(String(16), nullable=False)
    requester: Mapped[str] = mapped_column(String(128), nullable=False)
    approvers_json: Mapped[str] = mapped_column(Text, nullable=False)
    justification: Mapped[str] = mapped_column(Text, nullable=False)
    is_rollback: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditEventORM(Base):
    """An append-only audit record of a privileged action (Requirement 18)."""

    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_correlation_id", "correlation_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    before_state: Mapped[str | None] = mapped_column(String(16), nullable=True)
    after_state: Mapped[str | None] = mapped_column(String(16), nullable=True)
    justification: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
