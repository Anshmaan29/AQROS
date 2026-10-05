from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


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


class RejectReason(StrEnum):
    INVALID_SYMBOL = "invalid_symbol"
    INVALID_QUANTITY = "invalid_quantity"
    INVALID_PRICE = "invalid_price"
    INVALID_SIDE = "invalid_side"
    INVALID_ORDER_TYPE = "invalid_order_type"
    INVALID_TIME_IN_FORCE = "invalid_time_in_force"
    DUPLICATE_CLIENT_ORDER_ID = "duplicate_client_order_id"
    INSUFFICIENT_CASH = "insufficient_cash"
    INSUFFICIENT_POSITION = "insufficient_position"
    PORTFOLIO_NOT_FOUND = "portfolio_not_found"
    INSTRUMENT_NOT_TRADABLE = "instrument_not_tradable"
    EXCEEDS_MAX_ORDER_VALUE = "exceeds_max_order_value"
    EXCEEDS_MAX_ORDER_QUANTITY = "exceeds_max_order_quantity"
    MARKET_CLOSED = "market_closed"
    UNKNOWN = "unknown"


_ALLOWED_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.PENDING: {OrderStatus.OPEN, OrderStatus.REJECTED, OrderStatus.CANCELLED},
    OrderStatus.OPEN: {
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.EXPIRED,
    },
    OrderStatus.PARTIALLY_FILLED: {
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.EXPIRED,
    },
    OrderStatus.FILLED: set(),
    OrderStatus.CANCELLED: set(),
    OrderStatus.REJECTED: set(),
    OrderStatus.EXPIRED: set(),
}


@dataclass(frozen=True)
class Fill:
    trade_id: str
    order_id: str
    quantity: Decimal
    price: Decimal
    commission: Decimal = Decimal(0)
    created_at: datetime | None = None


@dataclass
class Order:
    order_id: str
    client_order_id: str
    portfolio_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    price: Decimal | None = None
    stop_price: Decimal | None = None
    time_in_force: TimeInForce = TimeInForce.DAY
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: Decimal = Decimal(0)
    filled_value: Decimal = Decimal(0)
    total_commission: Decimal = Decimal(0)
    avg_fill_price: Decimal | None = None
    last_fill_price: Decimal | None = None
    reject_reason: RejectReason | None = None
    reject_message: str | None = None
    expires_at: datetime | None = None
    strategy: str = "unknown"
    correlation_id: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None
    fills: list[Fill] = field(default_factory=list)

    @property
    def remaining_quantity(self) -> Decimal:
        return self.quantity - self.filled_quantity

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

    def can_transition_to(self, new_status: OrderStatus) -> bool:
        return new_status in _ALLOWED_TRANSITIONS.get(self.status, set())

    def transition_to(self, new_status: OrderStatus) -> None:
        if not self.can_transition_to(new_status):
            current = self.status.value
            msg = f"Cannot transition order {self.order_id} from {current} to {new_status.value}"
            raise ValueError(msg)
        self.status = new_status
        self.updated_at = datetime.now(tz=None)

    def accept(self, now: datetime | None = None) -> None:
        self.transition_to(OrderStatus.OPEN)
        self.updated_at = now or datetime.now(tz=None)

    def reject(self, reason: RejectReason, message: str = "", now: datetime | None = None) -> None:
        self.transition_to(OrderStatus.REJECTED)
        self.reject_reason = reason
        self.reject_message = message
        self.updated_at = now or datetime.now(tz=None)

    def cancel(self, now: datetime | None = None) -> None:
        self.transition_to(OrderStatus.CANCELLED)
        self.updated_at = now or datetime.now(tz=None)

    def expire(self, now: datetime | None = None) -> None:
        self.transition_to(OrderStatus.EXPIRED)
        self.updated_at = now or datetime.now(tz=None)

    def apply_fill(self, fill: Fill) -> None:
        if self.is_complete:
            msg = f"Cannot fill completed order {self.order_id}"
            raise ValueError(msg)
        new_filled = self.filled_quantity + fill.quantity
        if new_filled > self.quantity:
            msg = f"Fill quantity {fill.quantity} exceeds remaining {self.remaining_quantity} for order {self.order_id}"
            raise ValueError(msg)
        self.fills.append(fill)
        self.filled_quantity = new_filled
        self.filled_value += fill.quantity * fill.price
        self.total_commission += fill.commission
        self.last_fill_price = fill.price
        if self.filled_quantity > 0:
            self.avg_fill_price = self.filled_value / self.filled_quantity
        if self.filled_quantity >= self.quantity:
            self.transition_to(OrderStatus.FILLED)
            if self.time_in_force in (TimeInForce.IOC, TimeInForce.FOK):
                self.transition_to(OrderStatus.FILLED)
        else:
            self.transition_to(OrderStatus.PARTIALLY_FILLED)
        self.updated_at = datetime.now(tz=None)

    def should_expire(self, now: datetime) -> bool:
        if self.status not in (OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED):
            return False
        return self.expires_at is not None and now >= self.expires_at

    def has_expired(self, now: datetime) -> bool:
        if (
            self.status in (OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED)
            and self.expires_at is not None
        ):
            return now >= self.expires_at
        return False


def validate_order(
    *,
    client_order_id: str,
    portfolio_id: str,
    symbol: str,
    side: OrderSide,
    order_type: OrderType,
    quantity: Decimal,
    price: Decimal | None = None,
    stop_price: Decimal | None = None,
    time_in_force: TimeInForce = TimeInForce.DAY,
    max_quantity: Decimal | None = None,
    max_value: Decimal | None = None,
    now: datetime | None = None,
) -> list[tuple[RejectReason, str]]:
    errors: list[tuple[RejectReason, str]] = []

    if not client_order_id.strip():
        errors.append((RejectReason.INVALID_SYMBOL, "Client order ID must not be empty"))

    if not portfolio_id.strip():
        errors.append((RejectReason.PORTFOLIO_NOT_FOUND, "Portfolio ID must not be empty"))

    if not symbol.strip():
        errors.append((RejectReason.INVALID_SYMBOL, "Symbol must not be empty"))

    if quantity <= Decimal(0):
        errors.append((RejectReason.INVALID_QUANTITY, "Quantity must be positive"))

    if order_type == OrderType.MARKET and quantity <= Decimal(0):
        errors.append((RejectReason.INVALID_QUANTITY, "Market order quantity must be positive"))

    if order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT) and (
        price is None or price <= Decimal(0)
    ):
        errors.append(
            (RejectReason.INVALID_PRICE, "Limit price must be positive for limit/stop-limit orders")
        )

    if order_type in (OrderType.STOP, OrderType.STOP_LIMIT) and (
        stop_price is None or stop_price <= Decimal(0)
    ):
        errors.append(
            (RejectReason.INVALID_PRICE, "Stop price must be positive for stop/stop-limit orders")
        )

    if (
        order_type == OrderType.STOP_LIMIT
        and stop_price is not None
        and price is not None
        and stop_price >= price
    ):
        errors.append(
            (
                RejectReason.INVALID_PRICE,
                "Stop price must be below limit price for stop-limit orders",
            )
        )

    if max_quantity is not None and quantity > max_quantity:
        errors.append(
            (
                RejectReason.EXCEEDS_MAX_ORDER_QUANTITY,
                f"Quantity {quantity} exceeds max {max_quantity}",
            )
        )

    if max_value is not None and price is not None and (quantity * price) > max_value:
        errors.append(
            (
                RejectReason.EXCEEDS_MAX_ORDER_VALUE,
                f"Order value {quantity * price} exceeds max {max_value}",
            )
        )

    if max_value is not None and order_type == OrderType.MARKET and price is None:
        pass

    return errors


def calculate_order_expiry(
    time_in_force: TimeInForce, now: datetime, gtd_expiry: datetime | None = None
) -> datetime | None:
    if time_in_force == TimeInForce.GTD:
        return gtd_expiry
    if time_in_force == TimeInForce.DAY:
        return now.replace(hour=23, minute=59, second=59, microsecond=999999)
    if time_in_force == TimeInForce.GTC:
        return None
    if time_in_force in (TimeInForce.IOC, TimeInForce.FOK):
        return now + timedelta(seconds=1)
    return None
