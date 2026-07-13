"""SQLAlchemy 2.0 ORM models — the persistence schema.

Table/column naming follows CLAUDE.md §10 (snake_case, plural table names,
every time-sensitive table carries ``event_time``/``knowledge_time``). These
models are private to the adapters layer; nothing outside ``adapters/``
should import them directly — repositories map rows to/from the domain types
in ``domain/models.py``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for all feature-store ORM models."""


class FeatureDefinitionORM(Base):
    """A registered, versioned feature definition (the catalog)."""

    __tablename__ = "feature_definitions"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_feature_definitions_name_version"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    # Parameters are a small, fixed set of numeric knobs (window sizes,
    # thresholds); stored as JSON text rather than a JSONB column to avoid a
    # Postgres-specific type in a table that otherwise has none, keeping the
    # schema portable to SQLite for fast unit tests.
    parameters_json: Mapped[str] = mapped_column(Text, nullable=False)
    min_bars_required: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FeatureValueORM(Base):
    """A single computed feature value, bitemporal (``event_time`` + ``knowledge_time``)."""

    __tablename__ = "feature_values"
    __table_args__ = (
        UniqueConstraint(
            "symbol",
            "feature_name",
            "feature_version",
            "event_time",
            name="uq_feature_values_identity",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    feature_name: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    feature_version: Mapped[int] = mapped_column(Integer, nullable=False)
    event_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, nullable=False
    )
    value: Mapped[float] = mapped_column(Float, nullable=False)
    knowledge_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    computation_run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("feature_computation_runs.id"), nullable=True
    )


class FeatureComputationRunORM(Base):
    """Audit record of one feature-engineering pipeline execution."""

    __tablename__ = "feature_computation_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    bars_read: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    features_computed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    features_persisted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    features_rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Stored as newline-joined text rather than a Postgres array type, for the
    # same SQLite-portability reason as `parameters_json` above.
    rejection_reasons_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
