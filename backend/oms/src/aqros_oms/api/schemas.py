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
    expires_at: datetime | None = Field(
        default=None, description="Expiry timestamp (used with gtd)."
    )
    strategy: str = Field(default="unknown", description="Strategy name.")
    correlation_id: str = Field(default="", description="Correlation ID for tracing.")


class FillResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    trade_id: str
    quantity: Decimal
    price: Decimal
    commission: Decimal = Decimal(0)
    created_at: datetime | None = None


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    order_id: str
    client_order_id: str
    portfolio_id: str
    symbol: str
    side: str
    order_type: str
    quantity: Decimal
    price: Decimal | None = None
    stop_price: Decimal | None = None
    time_in_force: str = "day"
    status: str = "pending"
    filled_quantity: Decimal = Decimal(0)
    filled_value: Decimal = Decimal(0)
    total_commission: Decimal = Decimal(0)
    avg_fill_price: Decimal | None = None
    last_fill_price: Decimal | None = None
    remaining_quantity: Decimal = Decimal(0)
    fill_pct: float = 0.0
    reject_reason: str | None = None
    reject_message: str | None = None
    expires_at: datetime | None = None
    strategy: str = "unknown"
    correlation_id: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None
    fills: list[FillResponse] = Field(default_factory=list)


class FillRequest(BaseModel):
    trade_id: str = Field(..., description="Unique trade/fill identifier.")
    quantity: Decimal = Field(..., gt=0, description="Fill quantity.")
    price: Decimal = Field(..., gt=0, description="Fill price.")
    commission: Decimal = Field(default=Decimal(0), ge=0, description="Fill commission.")


class CancelRequest(BaseModel):
    reason: str | None = Field(default=None, description="Optional cancellation reason.")


class OrderListResponse(BaseModel):
    orders: list[OrderResponse]
    total: int = 0


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "oms"
    version: str = "0.1.0"
