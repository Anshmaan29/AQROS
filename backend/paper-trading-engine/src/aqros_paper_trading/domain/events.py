from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class PaperOrderSubmitted:
    order_id: str
    client_order_id: str
    portfolio_id: str
    symbol: str
    side: str
    order_type: str
    quantity: str
    price: str | None = None
    stop_price: str | None = None
    strategy: str = "unknown"
    correlation_id: str = ""
    timestamp: datetime | None = None


@dataclass(frozen=True)
class PaperOrderAccepted:
    order_id: str
    client_order_id: str
    symbol: str
    side: str
    quantity: str
    correlation_id: str = ""
    timestamp: datetime | None = None


@dataclass(frozen=True)
class PaperOrderFilled:
    order_id: str
    client_order_id: str
    portfolio_id: str
    symbol: str
    side: str
    fill_quantity: str
    fill_price: str
    fill_value: str
    commission: str
    slippage: str
    cumulative_quantity: str
    remaining_quantity: str
    trade_id: str
    is_partial: bool = False
    correlation_id: str = ""
    timestamp: datetime | None = None


@dataclass(frozen=True)
class PaperOrderRejected:
    order_id: str
    client_order_id: str
    reject_reason: str
    reject_message: str = ""
    correlation_id: str = ""
    timestamp: datetime | None = None


@dataclass(frozen=True)
class PaperOrderCancelled:
    order_id: str
    client_order_id: str
    symbol: str
    side: str
    remaining_quantity: str
    correlation_id: str = ""
    timestamp: datetime | None = None


@dataclass(frozen=True)
class MarketDataReceived:
    symbol: str
    bid: str
    ask: str
    last: str
    volume: float
    correlation_id: str = ""
    timestamp: datetime | None = None


@dataclass(frozen=True)
class PaperTradeSettled:
    trade_id: str
    order_id: str
    portfolio_id: str
    symbol: str
    side: str
    quantity: str
    price: str
    commission: str
    correlation_id: str = ""
    timestamp: datetime | None = None
