from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class PortfolioORM(Base):
    __tablename__ = "portfolios"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    portfolio_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    cash_total: Mapped[str] = mapped_column(String(64), nullable=False)
    cash_reserved: Mapped[str] = mapped_column(String(64), nullable=False)
    total_fees: Mapped[str] = mapped_column(String(64), nullable=False)
    total_pnl_realized: Mapped[str] = mapped_column(String(64), nullable=False)
    daily_pnl: Mapped[str] = mapped_column(String(64), nullable=False)
    peak_equity: Mapped[str] = mapped_column(String(64), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PositionORM(Base):
    __tablename__ = "positions"
    __table_args__ = (
        UniqueConstraint("portfolio_id", "symbol", "status", name="uq_portfolio_symbol_status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    position_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    portfolio_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity: Mapped[str] = mapped_column(String(64), nullable=False)
    avg_entry_price: Mapped[str] = mapped_column(String(64), nullable=False)
    current_price: Mapped[str] = mapped_column(String(64), nullable=False)
    stop_loss: Mapped[str | None] = mapped_column(String(64), nullable=True)
    take_profit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    sector: Mapped[str | None] = mapped_column(String(64), nullable=True)
    beta: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    volatility: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    avg_daily_volume: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    realized_pnl: Mapped[str] = mapped_column(String(64), nullable=False)
    fees_paid: Mapped[str] = mapped_column(String(64), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PnLRecordORM(Base):
    __tablename__ = "pnl_records"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    portfolio_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    total_equity: Mapped[str] = mapped_column(String(64), nullable=False)
    unrealized_pnl: Mapped[str] = mapped_column(String(64), nullable=False)
    realized_pnl: Mapped[str] = mapped_column(String(64), nullable=False)
    daily_pnl: Mapped[str] = mapped_column(String(64), nullable=False)
    daily_return_pct: Mapped[float] = mapped_column(Float, nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EquityCurvePointORM(Base):
    __tablename__ = "equity_curve_points"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    portfolio_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    equity: Mapped[str] = mapped_column(String(64), nullable=False)
    cash: Mapped[str] = mapped_column(String(64), nullable=False)
    market_value: Mapped[str] = mapped_column(String(64), nullable=False)
    daily_pnl: Mapped[str] = mapped_column(String(64), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
