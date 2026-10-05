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
    strategy: str = Field(default="unknown", description="Strategy name.")
    correlation_id: str = Field(default="", description="Correlation ID for tracing.")


class FillResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    trade_id: str
    fill_quantity: Decimal
    fill_price: Decimal
    commission: Decimal = Decimal("0")
    slippage: Decimal = Decimal("0")
    is_partial: bool = False
    timestamp: datetime | None = None


class PaperOrderResponse(BaseModel):
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
    status: str = "pending"
    filled_quantity: Decimal = Decimal("0")
    filled_value: Decimal = Decimal("0")
    total_commission: Decimal = Decimal("0")
    avg_fill_price: Decimal | None = None
    last_fill_price: Decimal | None = None
    remaining_quantity: Decimal = Decimal("0")
    reject_reason: str | None = None
    reject_message: str | None = None
    strategy: str = "unknown"
    correlation_id: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None
    fills: list[FillResultResponse] = Field(default_factory=list)


class MarketDataRequest(BaseModel):
    symbol: str = Field(..., description="Ticker symbol.")
    bid: Decimal = Field(..., gt=0, description="Bid price.")
    ask: Decimal = Field(..., gt=0, description="Ask price.")
    last: Decimal = Field(..., gt=0, description="Last trade price.")
    volume: float = Field(default=0.0, ge=0, description="Trading volume.")
    bid_size: float = Field(default=0.0, ge=0, description="Bid size.")
    ask_size: float = Field(default=0.0, ge=0, description="Ask size.")


class MarketDataResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    symbol: str
    bid: Decimal
    ask: Decimal
    last: Decimal
    volume: float
    bid_size: float
    ask_size: float
    timestamp: datetime | None = None


class OrderListResponse(BaseModel):
    orders: list[PaperOrderResponse]
    total: int = 0


class SimulationConfigResponse(BaseModel):
    slippage_model: str = "linear"
    commission_model: str = "per_share"
    base_delay_ms: float = 50.0
    per_share_rate: str = "0.005"
    min_commission: str = "1.00"


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "paper-trading-engine"
    version: str = "0.1.0"
