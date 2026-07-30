"""SQLAlchemy 2.0 ORM models for backtest run persistence.

The ORM is deliberately limited to persistence concerns. Domain dataclasses are
translated by the repository layer; no upstream service package is imported
here. Trade-log and equity-point rows are append-only by repository contract,
and a result row is written once per run.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for all backtesting-engine ORM models."""


class BacktestRunORM(Base):
    """Stable run identity and lifecycle metadata.

    ``run_uuid`` is immutable and unique for the lifetime of a run. The
    repository only advances status and records terminal metadata; it does not
    alter the run identity or its configuration/manifest snapshots.
    """

    __tablename__ = "backtest_runs"
    __table_args__ = (
        UniqueConstraint("run_uuid", name="uq_backtest_runs_run_uuid"),
        Index("ix_backtest_runs_run_uuid", "run_uuid"),
        Index("ix_backtest_runs_status", "status"),
        Index("ix_backtest_runs_strategy_id", "strategy_id"),
        Index("ix_backtest_runs_model_name", "model_name"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_uuid: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    strategy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    model_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    config_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    manifest_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class TradeLogEntryORM(Base):
    """One append-only order/fill outcome in a backtest run."""

    __tablename__ = "trade_log_entries"
    __table_args__ = (Index("ix_trade_log_entries_run_uuid", "run_uuid"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_uuid: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("backtest_runs.run_uuid"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    client_order_id: Mapped[str] = mapped_column(String(128), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    order_type: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    price: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
    commission: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    clock_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class EquityPointORM(Base):
    """One append-only point in a run's equity curve."""

    __tablename__ = "equity_points"
    __table_args__ = (Index("ix_equity_points_run_uuid", "run_uuid"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_uuid: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("backtest_runs.run_uuid"), nullable=False
    )
    clock_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    total_value: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)


class BacktestResultORM(Base):
    """Write-once aggregate result for a completed backtest run.

    The unique run UUID permits at most one result representation per run.
    Repository operations intentionally provide no update or delete path.
    """

    __tablename__ = "backtest_results"
    __table_args__ = (
        UniqueConstraint("run_uuid", name="uq_backtest_results_run_uuid"),
        Index("ix_backtest_results_run_uuid", "run_uuid"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_uuid: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("backtest_runs.run_uuid"), nullable=False
    )
    performance_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    risk_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    drawdown_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    benchmark_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    final_portfolio_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    written_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
