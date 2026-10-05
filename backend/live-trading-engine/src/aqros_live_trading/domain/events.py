from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class LiveOrderSubmitted:
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
class LiveOrderRouted:
    order_id: str
    client_order_id: str
    broker_order_id: str
    broker_name: str
    symbol: str
    side: str
    quantity: str
    correlation_id: str = ""
    timestamp: datetime | None = None


@dataclass(frozen=True)
class LiveExecutionReport:
    order_id: str
    client_order_id: str
    broker_order_id: str
    portfolio_id: str
    symbol: str
    side: str
    status: str
    filled_quantity: str = "0"
    remaining_quantity: str = "0"
    avg_fill_price: str | None = None
    last_fill_price: str | None = None
    cummulative_commission: str = "0"
    trade_id: str | None = None
    reject_reason: str | None = None
    broker_latency_ms: float | None = None
    correlation_id: str = ""
    timestamp: datetime | None = None


@dataclass(frozen=True)
class LiveOrderFilled:
    order_id: str
    client_order_id: str
    broker_order_id: str
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
class LiveOrderCancelled:
    order_id: str
    client_order_id: str
    broker_order_id: str
    portfolio_id: str
    symbol: str
    side: str
    remaining_quantity: str
    correlation_id: str = ""
    timestamp: datetime | None = None


@dataclass(frozen=True)
class LiveOrderRejected:
    order_id: str
    client_order_id: str
    broker_order_id: str
    reject_reason: str
    reject_message: str = ""
    correlation_id: str = ""
    timestamp: datetime | None = None


@dataclass(frozen=True)
class BrokerConnected:
    broker_name: str
    timestamp: datetime | None = None


@dataclass(frozen=True)
class BrokerDisconnected:
    broker_name: str
    reason: str = ""
    timestamp: datetime | None = None


@dataclass(frozen=True)
class BrokerReconnecting:
    broker_name: str
    attempt: int
    delay_seconds: float
    timestamp: datetime | None = None


@dataclass(frozen=True)
class KillSwitchTriggered:
    triggered_by: str
    reason: str
    timestamp: datetime | None = None


@dataclass(frozen=True)
class KillSwitchReset:
    timestamp: datetime | None = None


@dataclass(frozen=True)
class PositionsSynced:
    positions: str
    timestamp: datetime | None = None


@dataclass(frozen=True)
class AccountSynced:
    account_id: str
    buying_power: str
    cash: str
    portfolio_value: str
    timestamp: datetime | None = None
