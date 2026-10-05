from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class PaperOrderORM(Base):
    __tablename__ = "paper_orders"
    __table_args__ = (UniqueConstraint("client_order_id", name="uq_paper_orders_client_order_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    client_order_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    portfolio_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    order_type: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity: Mapped[str] = mapped_column(String(64), nullable=False)
    price: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stop_price: Mapped[str | None] = mapped_column(String(64), nullable=True)
    filled_quantity: Mapped[str] = mapped_column(String(64), nullable=False, default="0")
    filled_value: Mapped[str] = mapped_column(String(64), nullable=False, default="0")
    total_commission: Mapped[str] = mapped_column(String(64), nullable=False, default="0")
    avg_fill_price: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_fill_price: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    reject_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reject_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    strategy: Mapped[str] = mapped_column(String(128), nullable=False, default="unknown")
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PaperFillORM(Base):
    __tablename__ = "paper_fills"
    __table_args__ = (UniqueConstraint("trade_id", name="uq_paper_fills_trade_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trade_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    order_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    fill_quantity: Mapped[str] = mapped_column(String(64), nullable=False)
    fill_price: Mapped[str] = mapped_column(String(64), nullable=False)
    commission: Mapped[str] = mapped_column(String(64), nullable=False, default="0")
    slippage: Mapped[str] = mapped_column(String(64), nullable=False, default="0")
    is_partial: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MarketDataSnapshotORM(Base):
    __tablename__ = "market_data_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    bid: Mapped[str] = mapped_column(String(64), nullable=False)
    ask: Mapped[str] = mapped_column(String(64), nullable=False)
    last: Mapped[str] = mapped_column(String(64), nullable=False)
    volume: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    bid_size: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    ask_size: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
