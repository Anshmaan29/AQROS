from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class RiskDecisionORM(Base):
    __tablename__ = "risk_decisions"
    __table_args__ = (UniqueConstraint("signal_id", name="uq_risk_decisions_signal_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    signal_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    strategy: Mapped[str] = mapped_column(String(128), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False)
    reasons_json: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    requested_quantity: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_quantity: Mapped[str] = mapped_column(String(64), nullable=False)
    stop_loss: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    portfolio_equity_at_check: Mapped[str] = mapped_column(String(64), nullable=False)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RiskLimitORM(Base):
    __tablename__ = "risk_limits"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String(32), nullable=False, default="global")
    scope_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    limit_type: Mapped[str] = mapped_column(String(64), nullable=False)
    limit_value: Mapped[float] = mapped_column(Float, nullable=False)
    is_kernel: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class KillSwitchEventORM(Base):
    __tablename__ = "kill_switch_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    armed_by: Mapped[str] = mapped_column(String(128), nullable=False)
    armed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resumed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExposureSnapshotORM(Base):
    __tablename__ = "exposure_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False)
    total_equity: Mapped[str] = mapped_column(String(64), nullable=False)
    gross_exposure: Mapped[str] = mapped_column(String(64), nullable=False)
    net_exposure: Mapped[str] = mapped_column(String(64), nullable=False)
    cash: Mapped[str] = mapped_column(String(64), nullable=False)
    leverage: Mapped[float] = mapped_column(Float, nullable=False)
    position_count: Mapped[int] = mapped_column(Integer, nullable=False)
    var_95: Mapped[float] = mapped_column(Float, nullable=False)
    var_99: Mapped[float] = mapped_column(Float, nullable=False)
    daily_pnl: Mapped[str] = mapped_column(String(64), nullable=False)
    max_drawdown_pct: Mapped[float] = mapped_column(Float, nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
