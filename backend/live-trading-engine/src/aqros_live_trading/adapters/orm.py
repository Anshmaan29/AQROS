from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class LiveOrderORM(Base):
    __tablename__ = "live_orders"
    __table_args__ = (UniqueConstraint("client_order_id", name="uq_live_orders_client_order_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    client_order_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    portfolio_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    order_type: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity: Mapped[str] = mapped_column(String(64), nullable=False)
    price: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stop_price: Mapped[str | None] = mapped_column(String(64), nullable=True)
    time_in_force: Mapped[str] = mapped_column(String(16), nullable=False, default="day")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    route_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending_route")
    filled_quantity: Mapped[str] = mapped_column(String(64), nullable=False, default="0")
    remaining_quantity: Mapped[str] = mapped_column(String(64), nullable=False, default="0")
    avg_fill_price: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_fill_price: Mapped[str | None] = mapped_column(String(64), nullable=True)
    total_commission: Mapped[str] = mapped_column(String(64), nullable=False, default="0")
    broker_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reject_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    strategy: Mapped[str] = mapped_column(String(128), nullable=False, default="unknown")
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    routed_to: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BrokerConnectionORM(Base):
    __tablename__ = "broker_connections"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    broker_name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="disconnected")
    last_connected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_disconnected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    consecutive_failures: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_disconnections: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_reconnections: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    kill_switch_status: Mapped[str] = mapped_column(String(32), nullable=False, default="armed")
    kill_switch_triggered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    kill_switch_triggered_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    kill_switch_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PositionSyncORM(Base):
    __tablename__ = "position_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    portfolio_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    quantity: Mapped[str] = mapped_column(String(64), nullable=False)
    market_value: Mapped[str] = mapped_column(String(64), nullable=False)
    cost_basis: Mapped[str] = mapped_column(String(64), nullable=False)
    avg_entry_price: Mapped[str | None] = mapped_column(String(64), nullable=True)
    unrealized_pl: Mapped[str] = mapped_column(String(64), nullable=False, default="0")
    realized_pl: Mapped[str] = mapped_column(String(64), nullable=False, default="0")
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
