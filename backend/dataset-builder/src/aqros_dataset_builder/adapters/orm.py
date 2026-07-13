"""SQLAlchemy 2.0 ORM models — the persistence schema.

Table/column naming follows CLAUDE.md §10 (snake_case, plural table names).
These models are private to the adapters layer; nothing outside
``adapters/`` should import them directly — repositories map rows to/from
the domain types in ``domain/models.py``.

Only *metadata* is persisted here (definitions + build-run audit trail) —
the generated dataset rows themselves live in Parquet files, per
CLAUDE.md's `datasets/` folder doctrine ("dataset/feature/label
DEFINITIONS... not raw data").
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for all dataset-builder ORM models."""


class DatasetDefinitionORM(Base):
    """A registered, versioned, immutable dataset definition."""

    __tablename__ = "dataset_definitions"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_dataset_definitions_name_version"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    # Symbols and feature names are small, fixed lists; stored as JSON text
    # rather than a Postgres array type to stay portable to SQLite for fast
    # unit tests (same reasoning as feature-store's parameters_json).
    symbols_json: Mapped[str] = mapped_column(Text, nullable=False)
    feature_names_json: Mapped[str] = mapped_column(Text, nullable=False)
    label_type: Mapped[str] = mapped_column(String(32), nullable=False)
    horizon: Mapped[str] = mapped_column(String(8), nullable=False)
    split_strategy: Mapped[str] = mapped_column(String(32), nullable=False)
    split_params_json: Mapped[str] = mapped_column(Text, nullable=False)
    start_date: Mapped[str] = mapped_column(String(10), nullable=False)
    end_date: Mapped[str] = mapped_column(String(10), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DatasetBuildRunORM(Base):
    """Audit record of one dataset-generation pipeline execution."""

    __tablename__ = "dataset_build_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    dataset_name: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    dataset_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    bars_read: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_generated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejection_reasons_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    leakage_audit_passed: Mapped[bool | None] = mapped_column(nullable=True)
    leakage_audit_findings_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    label_balance_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    row_counts_by_role_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    quality_report_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    parquet_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    manifest_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
