"""SQLAlchemy 2.0 ORM models — the persistence schema (design.md Section 7).

Table/column naming follows CLAUDE.md §10 (snake_case, plural table names).
Private to the adapters layer — repositories map rows to/from the domain
types in ``domain/models.py``. Only metadata is persisted here; the
``Model_Artifact`` bytes live in the ``ArtifactStore``, not Postgres.

``trained_models`` carries a ``UniqueConstraint(model_name, model_version)``
— the database-level backstop behind Requirement 8.4's uniqueness guarantee
under concurrent writers — and rows are write-once (never updated),
satisfying Requirement 8.3's immutability.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for all training-pipeline ORM models."""


class TrainingRunORM(Base):
    """Audit record of one training-pipeline execution."""

    __tablename__ = "training_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    dataset_name: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    build_run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    model_types_json: Mapped[str] = mapped_column(Text, nullable=False)
    hyperparameters_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcomes_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class TrainedModelORM(Base):
    """A single trained-and-evaluated candidate model (write-once, immutable)."""

    __tablename__ = "trained_models"
    __table_args__ = (
        UniqueConstraint("model_name", "model_version", name="uq_trained_models_name_version"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    model_name: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    model_type: Mapped[str] = mapped_column(String(32), nullable=False)
    model_version: Mapped[int] = mapped_column(Integer, nullable=False)
    training_run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("training_runs.id"), nullable=False
    )
    dataset_name: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset_version: Mapped[int] = mapped_column(Integer, nullable=False)
    build_run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    artifact_path: Mapped[str] = mapped_column(String(512), nullable=False)
    per_fold_metrics_json: Mapped[str] = mapped_column(Text, nullable=False)
    aggregated_metrics_json: Mapped[str] = mapped_column(Text, nullable=False)
    feature_importance_json: Mapped[str] = mapped_column(Text, nullable=False)
    reproducibility_metadata_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
