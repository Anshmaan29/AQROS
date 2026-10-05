from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol, runtime_checkable

from aqros_strategy_core.contracts import OrderSide as SharedOrderSide
from aqros_strategy_core.contracts import OrderType as SharedOrderType


class ConnectionStatus(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    FAILED = "failed"
    KILLED = "killed"


class SessionStatus(StrEnum):
    INACTIVE = "inactive"
    PRE_MARKET = "pre_market"
    REGULAR = "regular"
    AFTER_HOURS = "after_hours"
    CLOSED = "closed"


class KillSwitchStatus(StrEnum):
    ARMED = "armed"
    TRIGGERED = "triggered"
    DISABLED = "disabled"
    RESET = "reset"


class OrderRouteStatus(StrEnum):
    PENDING_ROUTE = "pending_route"
    ROUTED = "routed"
    FAILED = "failed"
    REJECTED = "rejected"


# OrderSide and OrderType come from the shared core so that backtest, paper,
# and live cannot disagree about what an order is (CLAUDE.md §7.1). They were
# previously redefined here, which let the live engine support STOP/STOP_LIMIT
# orders that the shared core could not express — making backtest-vs-live parity
# unprovable for stop orders. Aliased rather than redefined so every consumer of
# this module keeps working unchanged.
OrderSide = SharedOrderSide
OrderType = SharedOrderType


class OrderStatus(StrEnum):
    PENDING = "pending"
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


class TimeInForce(StrEnum):
    DAY = "day"
    GTC = "gtc"
    IOC = "ioc"
    FOK = "fok"
    GTD = "gtd"


@dataclass(frozen=True)
class ExecutionReport:
    order_id: str
    client_order_id: str
    broker_order_id: str
    portfolio_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    status: OrderStatus
    filled_quantity: Decimal
    remaining_quantity: Decimal
    avg_fill_price: Decimal | None = None
    last_fill_price: Decimal | None = None
    cummulative_commission: Decimal = Decimal("0")
    trade_id: str | None = None
    reject_reason: str | None = None
    reject_message: str | None = None
    broker_latency_ms: float | None = None
    timestamp: datetime | None = None


@dataclass(frozen=True)
class BrokerPosition:
    symbol: str
    quantity: Decimal
    market_value: Decimal
    cost_basis: Decimal
    avg_entry_price: Decimal | None = None
    unrealized_pl: Decimal = Decimal("0")
    realized_pl: Decimal = Decimal("0")
    updated_at: datetime | None = None


@dataclass(frozen=True)
class BrokerAccount:
    account_id: str
    buying_power: Decimal
    cash: Decimal
    portfolio_value: Decimal
    currency: str = "USD"
    status: str = "active"
    timestamp: datetime | None = None


@dataclass
class ReconnectionPolicy:
    base_delay: float = 1.0
    max_delay: float = 60.0
    max_attempts: int = 10
    jitter: float = 0.1
    _attempt: int = 0
    _next_delay: float = 0.0

    def reset(self) -> None:
        self._attempt = 0
        self._next_delay = 0.0

    def get_delay(self) -> float:
        if self._attempt >= self.max_attempts:
            return -1.0
        delay = min(self.base_delay * (2**self._attempt), self.max_delay)
        jitter_amount = delay * self.jitter * random.random()
        self._next_delay = delay + jitter_amount
        self._attempt += 1
        return self._next_delay

    @property
    def attempt(self) -> int:
        return self._attempt

    @property
    def is_exhausted(self) -> bool:
        return self._attempt >= self.max_attempts


@dataclass
class ConnectionHealth:
    status: ConnectionStatus = ConnectionStatus.DISCONNECTED
    last_connected_at: datetime | None = None
    last_disconnected_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    last_heartbeat_sent_at: datetime | None = None
    heartbeat_interval: float = 5.0
    heartbeat_timeout: float = 15.0
    consecutive_failures: int = 0
    total_disconnections: int = 0
    total_reconnections: int = 0
    uptime_seconds: float = 0.0

    def is_connected(self) -> bool:
        return self.status == ConnectionStatus.CONNECTED

    def mark_connected(self, now: datetime | None = None) -> None:
        self.status = ConnectionStatus.CONNECTED
        self.last_connected_at = now or datetime.now(UTC)
        self.consecutive_failures = 0

    def mark_disconnected(self, now: datetime | None = None) -> None:
        self.status = ConnectionStatus.DISCONNECTED
        self.last_disconnected_at = now or datetime.now(UTC)
        self.total_disconnections += 1
        self.consecutive_failures += 1

    def mark_heartbeat(self, now: datetime | None = None) -> None:
        self.last_heartbeat_at = now or datetime.now(UTC)

    def mark_heartbeat_sent(self, now: datetime | None = None) -> None:
        self.last_heartbeat_sent_at = now or datetime.now(UTC)

    def is_heartbeat_stale(self, now: datetime | None = None) -> bool:
        if self.last_heartbeat_at is None:
            return True
        elapsed = (now or datetime.now(UTC)) - self.last_heartbeat_at
        return elapsed.total_seconds() > self.heartbeat_timeout


@dataclass
class KillSwitch:
    status: KillSwitchStatus = KillSwitchStatus.ARMED
    triggered_at: datetime | None = None
    triggered_by: str = ""
    reason: str = ""
    auto_trigger_on_disconnect_seconds: float = 0.0
    enabled: bool = True

    def is_triggered(self) -> bool:
        return self.status == KillSwitchStatus.TRIGGERED

    def trigger(self, by: str = "manual", reason: str = "", now: datetime | None = None) -> None:
        """Trigger the kill switch. Always takes effect.

        ``trigger()`` deliberately ignores ``enabled``. ``disable()`` means
        "do not auto-arm after a reset", i.e. it is for maintenance windows —
        it must never be able to disable the emergency stop. An earlier version
        returned early when disabled, which meant an operator who disabled the
        switch once and forgot got a kill switch that silently did nothing
        during a real incident.
        """
        self.status = KillSwitchStatus.TRIGGERED
        self.triggered_at = now or datetime.now(UTC)
        self.triggered_by = by
        self.reason = reason

    def reset(self, now: datetime | None = None) -> None:
        self.status = KillSwitchStatus.RESET
        self.triggered_at = None
        self.triggered_by = ""
        self.reason = ""

    def arm(self) -> None:
        self.status = KillSwitchStatus.ARMED

    def disable(self) -> None:
        """Disable *automatic re-arming* (maintenance window).

        Does not disable ``trigger()`` — see ``trigger`` for why that distinction
        is safety-critical.
        """
        self.status = KillSwitchStatus.DISABLED
        self.enabled = False

    def enable(self) -> None:
        self.enabled = True
        self.status = KillSwitchStatus.ARMED


@dataclass(frozen=True)
class TradingSession:
    date: date
    open: datetime
    close: datetime
    pre_market_open: datetime | None = None
    pre_market_close: datetime | None = None
    after_hours_open: datetime | None = None
    after_hours_close: datetime | None = None
    is_regular_session: bool = True
    status: SessionStatus = SessionStatus.REGULAR

    def is_open(self, now: datetime) -> bool:
        return self.open <= now <= self.close

    def is_pre_market(self, now: datetime) -> bool:
        if self.pre_market_open and self.pre_market_close:
            return self.pre_market_open <= now <= self.pre_market_close
        return False

    def is_after_hours(self, now: datetime) -> bool:
        if self.after_hours_open and self.after_hours_close:
            return self.after_hours_open <= now <= self.after_hours_close
        return False

    def get_session_for(self, now: datetime) -> SessionStatus:
        if self.is_pre_market(now):
            return SessionStatus.PRE_MARKET
        if self.is_open(now):
            return SessionStatus.REGULAR
        if self.is_after_hours(now):
            return SessionStatus.AFTER_HOURS
        return SessionStatus.CLOSED


@dataclass
class LiveOrder:
    order_id: str
    client_order_id: str
    broker_order_id: str | None = None
    portfolio_id: str = ""
    symbol: str = ""
    side: OrderSide = OrderSide.BUY
    order_type: OrderType = OrderType.MARKET
    quantity: Decimal = Decimal("0")
    price: Decimal | None = None
    stop_price: Decimal | None = None
    time_in_force: TimeInForce = TimeInForce.DAY
    status: OrderStatus = OrderStatus.PENDING
    route_status: OrderRouteStatus = OrderRouteStatus.PENDING_ROUTE
    filled_quantity: Decimal = Decimal("0")
    remaining_quantity: Decimal | None = None
    avg_fill_price: Decimal | None = None
    last_fill_price: Decimal | None = None
    total_commission: Decimal = Decimal("0")
    broker_latency_ms: float | None = None
    reject_reason: str | None = None
    reject_message: str | None = None
    strategy: str = "unknown"
    correlation_id: str = ""
    routed_to: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.remaining_quantity is None:
            self.remaining_quantity = self.quantity - self.filled_quantity

    @property
    def is_complete(self) -> bool:
        return self.status in (
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
        )

    @property
    def fill_pct(self) -> float:
        if self.quantity == 0:
            return 0.0
        return float(self.filled_quantity / self.quantity) * 100.0


@dataclass(frozen=True)
class RouteRule:
    symbol_pattern: str = "*"
    order_types: tuple[OrderType, ...] = tuple(OrderType)
    min_quantity: Decimal = Decimal("0")
    max_quantity: Decimal = Decimal("1000000000")
    preferred_broker: str = ""
    fallback_brokers: tuple[str, ...] = ()
    requires_liquidity_check: bool = False
    requires_pre_trade_risk: bool = True
    max_slippage_bps: int | None = None


@runtime_checkable
class BrokerAdapter(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def health(self) -> ConnectionHealth: ...

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def is_connected(self) -> bool: ...

    async def heartbeat(self) -> bool: ...

    async def submit_order(self, order: LiveOrder) -> ExecutionReport: ...

    async def cancel_order(self, broker_order_id: str) -> ExecutionReport: ...

    async def get_order_status(self, broker_order_id: str) -> ExecutionReport: ...

    async def get_positions(self) -> list[BrokerPosition]: ...

    async def get_account(self) -> BrokerAccount: ...

    async def sync_positions(self) -> list[BrokerPosition]: ...

    async def sync_account(self) -> BrokerAccount: ...
