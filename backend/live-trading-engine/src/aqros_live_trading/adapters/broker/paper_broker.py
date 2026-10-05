from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from aqros_live_trading.domain.models import (
    BrokerAccount,
    BrokerPosition,
    ConnectionHealth,
    ExecutionReport,
    LiveOrder,
    OrderSide,
    OrderStatus,
    OrderType,
)


class PaperBrokerAdapter:
    def __init__(
        self,
        heartbeat_interval: float = 5.0,
        heartbeat_timeout: float = 15.0,
    ) -> None:
        self._name = "paper"
        self._health = ConnectionHealth(
            heartbeat_interval=heartbeat_interval,
            heartbeat_timeout=heartbeat_timeout,
        )
        self._orders: dict[str, LiveOrder] = {}
        self._positions: dict[str, BrokerPosition] = {}
        self._account = BrokerAccount(
            account_id="paper-001",
            buying_power=Decimal("1000000.00"),
            cash=Decimal("1000000.00"),
            portfolio_value=Decimal("1000000.00"),
        )
        self._connected = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def health(self) -> ConnectionHealth:
        return self._health

    async def connect(self) -> None:
        now = datetime.now(UTC)
        self._connected = True
        self._health.mark_connected(now)

    async def disconnect(self) -> None:
        now = datetime.now(UTC)
        self._connected = False
        self._health.mark_disconnected(now)

    async def is_connected(self) -> bool:
        return self._connected

    async def heartbeat(self) -> bool:
        if not self._connected:
            return False
        now = datetime.now(UTC)
        self._health.mark_heartbeat(now)
        self._health.mark_heartbeat_sent(now)
        return True

    async def submit_order(self, order: LiveOrder) -> ExecutionReport:
        now = datetime.now(UTC)
        broker_order_id = f"paper-{uuid.uuid4().hex[:12]}"
        update = LiveOrder(
            order_id=order.order_id,
            client_order_id=order.client_order_id,
            broker_order_id=broker_order_id,
            portfolio_id=order.portfolio_id,
            symbol=order.symbol,
            side=order.side,
            order_type=order.order_type,
            quantity=order.quantity,
            price=order.price,
            stop_price=order.stop_price,
            time_in_force=order.time_in_force,
            status=order.status,
            route_status=order.route_status,
            filled_quantity=order.filled_quantity,
            remaining_quantity=order.remaining_quantity or Decimal("0"),
            strategy=order.strategy,
            correlation_id=order.correlation_id,
            created_at=order.created_at,
            updated_at=now,
        )
        self._orders[order.order_id] = update

        fill_price: Decimal | None = None
        filled_qty = order.quantity
        status = OrderStatus.FILLED

        if order.order_type == OrderType.MARKET:
            fill_price = Decimal("100.00")
        elif order.order_type == OrderType.LIMIT and order.price:
            fill_price = order.price
        elif order.order_type == OrderType.LIMIT or order.order_type == OrderType.STOP:
            fill_price = Decimal("100.00")
        elif order.order_type == OrderType.STOP_LIMIT:
            fill_price = order.price or Decimal("100.00")
        else:
            fill_price = Decimal("100.00")

        return ExecutionReport(
            order_id=order.order_id,
            client_order_id=order.client_order_id,
            broker_order_id=broker_order_id,
            portfolio_id=order.portfolio_id,
            symbol=order.symbol,
            side=order.side,
            order_type=order.order_type,
            status=status,
            filled_quantity=filled_qty,
            remaining_quantity=Decimal("0"),
            avg_fill_price=fill_price,
            last_fill_price=fill_price,
            trade_id=f"trade-{uuid.uuid4().hex[:12]}",
            timestamp=now,
        )

    async def cancel_order(self, broker_order_id: str) -> ExecutionReport:
        now = datetime.now(UTC)
        for _oid, order in self._orders.items():
            if order.broker_order_id == broker_order_id:
                return ExecutionReport(
                    order_id=order.order_id,
                    client_order_id=order.client_order_id,
                    broker_order_id=broker_order_id,
                    portfolio_id=order.portfolio_id,
                    symbol=order.symbol,
                    side=order.side,
                    order_type=order.order_type,
                    status=OrderStatus.CANCELLED,
                    filled_quantity=order.filled_quantity,
                    remaining_quantity=order.remaining_quantity or Decimal("0"),
                    timestamp=now,
                )
        return ExecutionReport(
            order_id="",
            client_order_id="",
            broker_order_id=broker_order_id,
            portfolio_id="",
            symbol="",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            status=OrderStatus.REJECTED,
            filled_quantity=Decimal("0"),
            remaining_quantity=Decimal("0"),
            reject_reason="order_not_found",
            reject_message=f"Order {broker_order_id} not found",
            timestamp=now,
        )

    async def get_order_status(self, broker_order_id: str) -> ExecutionReport:
        now = datetime.now(UTC)
        for _oid, order in self._orders.items():
            if order.broker_order_id == broker_order_id:
                return ExecutionReport(
                    order_id=order.order_id,
                    client_order_id=order.client_order_id,
                    broker_order_id=broker_order_id,
                    portfolio_id=order.portfolio_id,
                    symbol=order.symbol,
                    side=order.side,
                    order_type=order.order_type,
                    status=order.status,
                    filled_quantity=order.filled_quantity,
                    remaining_quantity=order.remaining_quantity or Decimal("0"),
                    avg_fill_price=order.avg_fill_price,
                    last_fill_price=order.last_fill_price,
                    timestamp=now,
                )
        return ExecutionReport(
            order_id="",
            client_order_id="",
            broker_order_id=broker_order_id,
            portfolio_id="",
            symbol="",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            status=OrderStatus.REJECTED,
            filled_quantity=Decimal("0"),
            remaining_quantity=Decimal("0"),
            reject_reason="order_not_found",
            reject_message=f"Order {broker_order_id} not found",
            timestamp=now,
        )

    async def get_positions(self) -> list[BrokerPosition]:
        return list(self._positions.values())

    async def get_account(self) -> BrokerAccount:
        return self._account

    async def sync_positions(self) -> list[BrokerPosition]:
        return list(self._positions.values())

    async def sync_account(self) -> BrokerAccount:
        return self._account
