from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class OrderSubmitted:
    order_id: str
    client_order_id: str
    portfolio_id: str
    symbol: str
    side: str
    order_type: str
    quantity: str
    price: str | None = None
    stop_price: str | None = None
    time_in_force: str = "day"
    strategy: str = "unknown"
    correlation_id: str = ""
    timestamp: datetime | None = None


@dataclass(frozen=True)
class OrderAccepted:
    order_id: str
    client_order_id: str
    portfolio_id: str
    symbol: str
    side: str
    order_type: str
    quantity: str
    correlation_id: str = ""
    timestamp: datetime | None = None


@dataclass(frozen=True)
class OrderRejected:
    order_id: str
    client_order_id: str
    reject_reason: str
    reject_message: str = ""
    correlation_id: str = ""
    timestamp: datetime | None = None


@dataclass(frozen=True)
class OrderCancelled:
    order_id: str
    client_order_id: str
    portfolio_id: str
    symbol: str
    side: str
    remaining_quantity: str
    correlation_id: str = ""
    timestamp: datetime | None = None


@dataclass(frozen=True)
class OrderExpired:
    order_id: str
    client_order_id: str
    portfolio_id: str
    symbol: str
    side: str
    filled_quantity: str
    correlation_id: str = ""
    timestamp: datetime | None = None


@dataclass(frozen=True)
class OrderFilled:
    order_id: str
    client_order_id: str
    portfolio_id: str
    symbol: str
    side: str
    fill_quantity: str
    fill_price: str
    fill_value: str
    cumulative_quantity: str
    remaining_quantity: str
    trade_id: str
    correlation_id: str = ""
    timestamp: datetime | None = None


@dataclass(frozen=True)
class OrderPartiallyFilled:
    order_id: str
    client_order_id: str
    portfolio_id: str
    symbol: str
    side: str
    fill_quantity: str
    fill_price: str
    fill_value: str
    cumulative_quantity: str
    remaining_quantity: str
    trade_id: str
    correlation_id: str = ""
    timestamp: datetime | None = None
