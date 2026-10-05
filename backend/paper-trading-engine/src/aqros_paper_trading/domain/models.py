from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from aqros_strategy_core.contracts import OrderSide as SharedOrderSide
from aqros_strategy_core.contracts import OrderType as SharedOrderType

# OrderSide and OrderType come from the shared core so that backtest, paper, and
# live cannot disagree about what an order is (CLAUDE.md §7.1). They were
# previously redefined here, which allowed this engine and the backtest engine
# to drift apart. Aliased rather than redefined so existing imports of this
# module keep working.
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


class RejectReason(StrEnum):
    INVALID_QUANTITY = "invalid_quantity"
    INVALID_PRICE = "invalid_price"
    INVALID_SIDE = "invalid_side"
    INVALID_ORDER_TYPE = "invalid_order_type"
    SYMBOL_NOT_FOUND = "symbol_not_found"
    MARKET_CLOSED = "market_closed"
    EXCEEDS_MAX_ORDER_VALUE = "exceeds_max_order_value"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class MarketDataSnapshot:
    symbol: str
    bid: Decimal
    ask: Decimal
    last: Decimal
    volume: float
    bid_size: float
    ask_size: float
    timestamp: datetime | None = None


@dataclass
class SlippageConfig:
    model: str = "linear"
    linear_slippage_pct: float = 0.001
    sqrt_slippage_pct: float = 0.0005
    base_slippage_pct: float = 0.0001
    min_slippage_pct: float = 0.00001
    max_slippage_pct: float = 0.01

    def calculate(self, order_quantity: Decimal, market_volume: float, price: Decimal) -> Decimal:
        participation = float(order_quantity) / max(market_volume, 1.0)
        if self.model == "linear":
            slippage = self.base_slippage_pct + self.linear_slippage_pct * min(participation, 1.0)
        elif self.model == "sqrt":
            slippage = self.base_slippage_pct + self.sqrt_slippage_pct * math.sqrt(
                min(participation, 1.0)
            )
        else:
            slippage = self.base_slippage_pct
        slippage = max(self.min_slippage_pct, min(slippage, self.max_slippage_pct))
        return price * Decimal(str(slippage))


@dataclass
class CommissionConfig:
    model: str = "per_share"
    per_share_rate: Decimal = Decimal("0.005")
    pct_rate: Decimal = Decimal("0.001")
    min_commission: Decimal = Decimal("1.00")
    max_commission: Decimal = Decimal("100.00")

    def calculate(self, quantity: Decimal, price: Decimal) -> Decimal:
        if self.model == "per_share":
            commission = quantity * self.per_share_rate
        elif self.model == "pct":
            commission = quantity * price * self.pct_rate
        elif self.model == "fixed":
            commission = self.min_commission
        elif self.model == "tiered":
            val = quantity * price
            if val < Decimal("10000"):
                rate = Decimal("0.001")
            elif val < Decimal("100000"):
                rate = Decimal("0.0008")
            else:
                rate = Decimal("0.0005")
            commission = val * rate
        else:
            commission = quantity * price * self.pct_rate
        commission = max(commission, self.min_commission)
        commission = min(commission, self.max_commission)
        return commission.quantize(Decimal("0.01"))


@dataclass
class LatencyConfig:
    base_delay_ms: float = 50.0
    jitter_ms: float = 25.0
    min_delay_ms: float = 10.0

    def sample_delay(self) -> timedelta:
        delay = self.base_delay_ms + random.uniform(-self.jitter_ms, self.jitter_ms)
        delay = max(self.min_delay_ms, delay)
        return timedelta(milliseconds=delay)


@dataclass
class FillResult:
    trade_id: str
    order_id: str
    symbol: str
    side: OrderSide
    fill_quantity: Decimal
    fill_price: Decimal
    commission: Decimal
    slippage: Decimal
    is_partial: bool
    timestamp: datetime | None = None


@dataclass
class SimulatedOrder:
    order_id: str
    client_order_id: str
    portfolio_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    price: Decimal | None = None
    stop_price: Decimal | None = None
    filled_quantity: Decimal = Decimal("0")
    filled_value: Decimal = Decimal("0")
    total_commission: Decimal = Decimal("0")
    status: OrderStatus = OrderStatus.PENDING
    avg_fill_price: Decimal | None = None
    last_fill_price: Decimal | None = None
    reject_reason: RejectReason | None = None
    reject_message: str | None = None
    strategy: str = "unknown"
    correlation_id: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None
    fills: list[FillResult] = field(default_factory=list)

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

    def accept(self, now: datetime | None = None) -> None:
        self.status = OrderStatus.OPEN
        self.updated_at = now or datetime.now()

    def reject(self, reason: RejectReason, message: str = "", now: datetime | None = None) -> None:
        self.status = OrderStatus.REJECTED
        self.reject_reason = reason
        self.reject_message = message
        self.updated_at = now or datetime.now()

    def cancel(self, now: datetime | None = None) -> None:
        self.status = OrderStatus.CANCELLED
        self.updated_at = now or datetime.now()

    def apply_fill(self, result: FillResult) -> None:
        if self.is_complete:
            raise ValueError(f"Cannot fill completed order {self.order_id}")
        new_filled = self.filled_quantity + result.fill_quantity
        if new_filled > self.quantity:
            raise ValueError(
                f"Fill quantity {result.fill_quantity} exceeds remaining {self.remaining_quantity}"
            )
        self.fills.append(result)
        self.filled_quantity = new_filled
        self.filled_value += result.fill_quantity * result.fill_price
        self.total_commission += result.commission
        self.last_fill_price = result.fill_price
        if self.filled_quantity > 0:
            self.avg_fill_price = self.filled_value / self.filled_quantity
        if self.filled_quantity >= self.quantity:
            self.status = OrderStatus.FILLED
        else:
            self.status = OrderStatus.PARTIALLY_FILLED
        self.updated_at = datetime.now()


class FillIdGenerator(Protocol):
    def __call__(self) -> str: ...


def default_fill_id_generator() -> str:
    import uuid

    return str(uuid.uuid4())


@dataclass
class OrderQueue:
    orders: list[SimulatedOrder] = field(default_factory=list)

    def add(self, order: SimulatedOrder) -> None:
        self.orders.append(order)

    def remove(self, order_id: str) -> bool:
        for i, o in enumerate(self.orders):
            if o.order_id == order_id:
                self.orders.pop(i)
                return True
        return False

    def find(self, order_id: str) -> SimulatedOrder | None:
        for o in self.orders:
            if o.order_id == order_id:
                return o
        return None

    def find_by_symbol(self, symbol: str, side: OrderSide | None = None) -> list[SimulatedOrder]:
        result = [o for o in self.orders if o.symbol == symbol]
        if side is not None:
            result = [o for o in result if o.side == side]
        return result

    def find_crossable(self, symbol: str, side: OrderSide, price: Decimal) -> list[SimulatedOrder]:
        crossable: list[SimulatedOrder] = []
        for o in self.orders:
            if o.symbol != symbol or o.is_complete:
                continue
            if o.side == OrderSide.BUY and side == OrderSide.SELL:
                if o.price is not None and price <= o.price:
                    crossable.append(o)
            elif (
                o.side == OrderSide.SELL
                and side == OrderSide.BUY
                and o.price is not None
                and price >= o.price
            ):
                crossable.append(o)
        return sorted(crossable, key=lambda o: o.created_at or datetime.min)

    def __len__(self) -> int:
        return len(self.orders)


@dataclass
class ExchangeState:
    symbol: str
    last_price: Decimal
    bid: Decimal
    ask: Decimal
    volume: float
    bid_size: float
    ask_size: float
    order_queue: OrderQueue = field(default_factory=OrderQueue)
    last_update: datetime | None = None


class MatchingEngine:
    def __init__(
        self,
        slippage_config: SlippageConfig,
        commission_config: CommissionConfig,
        fill_id_generator: FillIdGenerator | None = None,
    ) -> None:
        self._slippage = slippage_config
        self._commission = commission_config
        self._fill_id_gen = fill_id_generator or default_fill_id_generator

    def match_market(
        self,
        order: SimulatedOrder,
        market: MarketDataSnapshot,
    ) -> list[FillResult]:
        results: list[FillResult] = []
        remaining = order.remaining_quantity
        if remaining <= 0:
            return results

        is_buy = order.side == OrderSide.BUY
        base_price = market.ask if is_buy else market.bid

        slippage = self._slippage.calculate(remaining, market.volume, base_price)
        # Slippage is always adverse: it may only push the fill worse than the
        # touch price, never better, or the simulation would manufacture alpha.
        fill_price = base_price + slippage if is_buy else base_price - slippage
        fill_price = max(fill_price, Decimal("0.0001"))

        if (is_buy and fill_price < base_price) or (not is_buy and fill_price > base_price):
            fill_price = base_price

        fill_qty = remaining
        commission = self._commission.calculate(fill_qty, fill_price)

        result = FillResult(
            trade_id=self._fill_id_gen(),
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            fill_quantity=fill_qty,
            fill_price=fill_price,
            commission=commission,
            slippage=slippage,
            is_partial=False,
        )
        results.append(result)
        return results

    def match_limit(
        self,
        order: SimulatedOrder,
        market: MarketDataSnapshot,
        order_queue: OrderQueue,
    ) -> list[FillResult]:
        results: list[FillResult] = []
        if order.price is None:
            return results

        remaining = order.remaining_quantity
        if remaining <= 0:
            return results

        if order.side == OrderSide.BUY:
            if order.price >= market.ask:
                slippage = self._slippage.calculate(
                    remaining, market.volume, Decimal(str(market.ask))
                )
                fill_price_with_slippage = Decimal(str(market.ask)) + slippage
                effective_price = min(order.price, fill_price_with_slippage)
                fill_qty = remaining
                commission = self._commission.calculate(fill_qty, effective_price)
                result = FillResult(
                    trade_id=self._fill_id_gen(),
                    order_id=order.order_id,
                    symbol=order.symbol,
                    side=order.side,
                    fill_quantity=fill_qty,
                    fill_price=effective_price,
                    commission=commission,
                    slippage=slippage,
                    is_partial=False,
                )
                results.append(result)
            else:
                order_queue.add(order)
        else:
            if order.price <= market.bid:
                slippage = self._slippage.calculate(
                    remaining, market.volume, Decimal(str(market.bid))
                )
                fill_price_with_slippage = Decimal(str(market.bid)) - slippage
                effective_price = max(order.price, fill_price_with_slippage)
                fill_qty = remaining
                commission = self._commission.calculate(fill_qty, effective_price)
                result = FillResult(
                    trade_id=self._fill_id_gen(),
                    order_id=order.order_id,
                    symbol=order.symbol,
                    side=order.side,
                    fill_quantity=fill_qty,
                    fill_price=effective_price,
                    commission=commission,
                    slippage=slippage,
                    is_partial=False,
                )
                results.append(result)
            else:
                order_queue.add(order)
        return results

    def match_stop(
        self,
        order: SimulatedOrder,
        market: MarketDataSnapshot,
    ) -> list[FillResult]:
        if order.stop_price is None:
            return []
        triggered = False
        if (order.side == OrderSide.BUY and market.last >= order.stop_price) or (
            order.side == OrderSide.SELL and market.last <= order.stop_price
        ):
            triggered = True
        if not triggered:
            return []
        if order.order_type == OrderType.STOP:
            market_order = SimulatedOrder(
                order_id=order.order_id,
                client_order_id=order.client_order_id,
                portfolio_id=order.portfolio_id,
                symbol=order.symbol,
                side=order.side,
                order_type=OrderType.MARKET,
                quantity=order.remaining_quantity,
                filled_quantity=order.filled_quantity,
                strategy=order.strategy,
                correlation_id=order.correlation_id,
            )
            return self.match_market(market_order, market)
        return []

    def match_stop_limit(
        self,
        order: SimulatedOrder,
        market: MarketDataSnapshot,
        order_queue: OrderQueue,
    ) -> list[FillResult]:
        if order.stop_price is None:
            return []
        triggered = False
        if (order.side == OrderSide.BUY and market.last >= order.stop_price) or (
            order.side == OrderSide.SELL and market.last <= order.stop_price
        ):
            triggered = True
        if not triggered:
            return []
        limit_order = SimulatedOrder(
            order_id=order.order_id,
            client_order_id=order.client_order_id,
            portfolio_id=order.portfolio_id,
            symbol=order.symbol,
            side=order.side,
            order_type=OrderType.LIMIT,
            quantity=order.remaining_quantity,
            price=order.price,
            filled_quantity=order.filled_quantity,
            strategy=order.strategy,
            correlation_id=order.correlation_id,
        )
        return self.match_limit(limit_order, market, order_queue)

    def process(
        self,
        order: SimulatedOrder,
        market: MarketDataSnapshot,
        order_queue: OrderQueue,
    ) -> list[FillResult]:
        if order.order_type == OrderType.MARKET:
            return self.match_market(order, market)
        elif order.order_type == OrderType.LIMIT:
            return self.match_limit(order, market, order_queue)
        elif order.order_type == OrderType.STOP:
            return self.match_stop(order, market)
        elif order.order_type == OrderType.STOP_LIMIT:
            return self.match_stop_limit(order, market, order_queue)
        return []


class SimulatedExchange:
    def __init__(
        self,
        matching_engine: MatchingEngine,
        latency_config: LatencyConfig | None = None,
    ) -> None:
        self._matching = matching_engine
        self._latency = latency_config or LatencyConfig()
        self._markets: dict[str, ExchangeState] = {}
        self._order_queue: OrderQueue = OrderQueue()

    @property
    def order_queue(self) -> OrderQueue:
        return self._order_queue

    def update_market(self, snapshot: MarketDataSnapshot) -> None:
        if snapshot.symbol not in self._markets:
            self._markets[snapshot.symbol] = ExchangeState(
                symbol=snapshot.symbol,
                last_price=snapshot.last,
                bid=snapshot.bid,
                ask=snapshot.ask,
                volume=snapshot.volume,
                bid_size=snapshot.bid_size,
                ask_size=snapshot.ask_size,
                last_update=snapshot.timestamp,
            )
        else:
            state = self._markets[snapshot.symbol]
            state.last_price = snapshot.last
            state.bid = snapshot.bid
            state.ask = snapshot.ask
            state.volume = snapshot.volume
            state.bid_size = snapshot.bid_size
            state.ask_size = snapshot.ask_size
            state.last_update = snapshot.timestamp

    def get_market(self, symbol: str) -> MarketDataSnapshot | None:
        state = self._markets.get(symbol)
        if state is None:
            return None
        return MarketDataSnapshot(
            symbol=state.symbol,
            bid=state.bid,
            ask=state.ask,
            last=state.last_price,
            volume=state.volume,
            bid_size=state.bid_size,
            ask_size=state.ask_size,
            timestamp=state.last_update,
        )

    def place_order(self, order: SimulatedOrder) -> list[FillResult]:
        market = self.get_market(order.symbol)
        if market is None:
            order.reject(RejectReason.SYMBOL_NOT_FOUND, f"No market data for {order.symbol}")
            return []

        order.accept()
        results = self._matching.process(order, market, self._order_queue)

        for result in results:
            order.apply_fill(result)

        return results

    def cancel_order(self, order_id: str) -> bool:
        return self._order_queue.remove(order_id)

    def get_simulated_latency(self) -> timedelta:
        return self._latency.sample_delay()

    def process_resting_orders(self, snapshot: MarketDataSnapshot) -> list[FillResult]:
        all_results: list[FillResult] = []
        crossable_buys = self._order_queue.find_crossable(
            snapshot.symbol, OrderSide.BUY, snapshot.ask
        )
        crossable_sells = self._order_queue.find_crossable(
            snapshot.symbol, OrderSide.SELL, snapshot.bid
        )

        for resting in crossable_buys:
            if resting.is_complete:
                continue
            fill_price = min(resting.price or Decimal("inf"), snapshot.ask)
            fill_qty = resting.remaining_quantity
            slippage = self._matching._slippage.calculate(fill_qty, snapshot.volume, snapshot.ask)
            effective_price = fill_price + slippage
            effective_price = min(effective_price, Decimal(str(snapshot.ask)))
            commission = self._matching._commission.calculate(fill_qty, effective_price)
            result = FillResult(
                trade_id=default_fill_id_generator(),
                order_id=resting.order_id,
                symbol=snapshot.symbol,
                side=OrderSide.BUY,
                fill_quantity=fill_qty,
                fill_price=effective_price,
                commission=commission,
                slippage=slippage,
                is_partial=False,
            )
            resting.apply_fill(result)
            all_results.append(result)
            self._order_queue.remove(resting.order_id)

        for resting in crossable_sells:
            if resting.is_complete:
                continue
            fill_price = max(resting.price or Decimal("0"), snapshot.bid)
            fill_qty = resting.remaining_quantity
            slippage = self._matching._slippage.calculate(fill_qty, snapshot.volume, snapshot.bid)
            effective_price = fill_price - slippage
            effective_price = max(effective_price, Decimal(str(snapshot.bid)))
            commission = self._matching._commission.calculate(fill_qty, effective_price)
            result = FillResult(
                trade_id=default_fill_id_generator(),
                order_id=resting.order_id,
                symbol=snapshot.symbol,
                side=OrderSide.SELL,
                fill_quantity=fill_qty,
                fill_price=effective_price,
                commission=commission,
                slippage=slippage,
                is_partial=False,
            )
            resting.apply_fill(result)
            all_results.append(result)
            self._order_queue.remove(resting.order_id)

        return all_results

    def list_orders(
        self,
        portfolio_id: str | None = None,
        symbol: str | None = None,
        status: OrderStatus | None = None,
    ) -> list[SimulatedOrder]:
        results: list[SimulatedOrder] = []
        for o in self._order_queue.orders:
            if portfolio_id is not None and o.portfolio_id != portfolio_id:
                continue
            if symbol is not None and o.symbol != symbol:
                continue
            if status is not None and o.status != status:
                continue
            results.append(o)
        return results

    def list_markets(self) -> list[str]:
        return list(self._markets.keys())
