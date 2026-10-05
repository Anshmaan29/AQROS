from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class OrderCreateRequest(BaseModel):
    client_order_id: str = Field(..., description="Client-generated unique order identifier.")
    portfolio_id: str = Field(..., description="Portfolio identifier.")
    symbol: str = Field(..., description="Ticker symbol.")
    side: str = Field(..., description="Order side: buy or sell.")
    order_type: str = Field(..., description="Order type: market, limit, stop, or stop_limit.")
    quantity: Decimal = Field(..., gt=0, description="Order quantity.")
    price: Decimal | None = Field(
        default=None, gt=0, description="Limit price (required for limit/stop-limit)."
    )
    stop_price: Decimal | None = Field(
        default=None, gt=0, description="Stop price (required for stop/stop-limit)."
    )
    time_in_force: str = Field(
        default="day", description="Time in force: day, gtc, ioc, fok, or gtd."
    )
    strategy: str = Field(default="unknown", description="Strategy name.")
    correlation_id: str = Field(default="", description="Correlation ID for tracing.")


class LiveOrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    order_id: str
    client_order_id: str
    broker_order_id: str | None = None
    portfolio_id: str
    symbol: str
    side: str
    order_type: str
    quantity: Decimal
    price: Decimal | None = None
    stop_price: Decimal | None = None
    time_in_force: str = "day"
    status: str = "pending"
    route_status: str = "pending_route"
    filled_quantity: Decimal = Decimal("0")
    remaining_quantity: Decimal = Decimal("0")
    avg_fill_price: Decimal | None = None
    last_fill_price: Decimal | None = None
    total_commission: Decimal = Decimal("0")
    broker_latency_ms: float | None = None
    reject_reason: str | None = None
    reject_message: str | None = None
    strategy: str = "unknown"
    correlation_id: str = ""
    routed_to: str = ""
    fill_pct: float = 0.0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class OrderListResponse(BaseModel):
    orders: list[LiveOrderResponse]
    total: int = 0


class CancelRequest(BaseModel):
    reason: str | None = Field(default=None, description="Optional cancellation reason.")


class PositionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    symbol: str
    quantity: Decimal
    market_value: Decimal
    cost_basis: Decimal
    avg_entry_price: Decimal | None = None
    unrealized_pl: Decimal = Decimal("0")
    realized_pl: Decimal = Decimal("0")
    updated_at: datetime | None = None


class AccountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    account_id: str
    buying_power: Decimal
    cash: Decimal
    portfolio_value: Decimal
    currency: str = "USD"
    status: str = "active"
    timestamp: datetime | None = None


class ConnectionHealthResponse(BaseModel):
    status: str = "disconnected"
    last_connected_at: datetime | None = None
    last_disconnected_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    consecutive_failures: int = 0
    total_disconnections: int = 0
    total_reconnections: int = 0


class KillSwitchResponse(BaseModel):
    status: str = "armed"
    triggered_at: datetime | None = None
    triggered_by: str = ""
    reason: str = ""
    enabled: bool = True


class TradingSessionResponse(BaseModel):
    date: str
    open: datetime | None = None
    close: datetime | None = None
    pre_market_open: datetime | None = None
    pre_market_close: datetime | None = None
    after_hours_open: datetime | None = None
    after_hours_close: datetime | None = None
    status: str = "closed"


class BrokerInfoResponse(BaseModel):
    name: str
    connected: bool
    health: ConnectionHealthResponse
    kill_switch: KillSwitchResponse


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "live-trading-engine"
    version: str = "0.1.0"
