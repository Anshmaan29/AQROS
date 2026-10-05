from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class OrderORM(Base):
    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("client_order_id", name="uq_orders_client_order_id"),)

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
    time_in_force: Mapped[str] = mapped_column(String(16), nullable=False, default="day")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    filled_quantity: Mapped[str] = mapped_column(String(64), nullable=False, default="0")
    filled_value: Mapped[str] = mapped_column(String(64), nullable=False, default="0")
    total_commission: Mapped[str] = mapped_column(String(64), nullable=False, default="0")
    avg_fill_price: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_fill_price: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reject_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    strategy: Mapped[str] = mapped_column(String(128), nullable=False, default="unknown")
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FillORM(Base):
    __tablename__ = "fills"
    __table_args__ = (UniqueConstraint("trade_id", name="uq_fills_trade_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trade_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    order_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    quantity: Mapped[str] = mapped_column(String(64), nullable=False)
    price: Mapped[str] = mapped_column(String(64), nullable=False)
    commission: Mapped[str] = mapped_column(String(64), nullable=False, default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
