"""Pure deterministic simulation loop for historical strategy replay."""

from __future__ import annotations

import random
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal

from aqros_backtesting_engine.domain.commission import CommissionModel
from aqros_backtesting_engine.domain.corporate_actions import apply_corporate_action
from aqros_backtesting_engine.domain.fills import FillModel
from aqros_backtesting_engine.domain.latency import LatencyModel
from aqros_backtesting_engine.domain.models import (
    Bar,
    CashLedger,
    CorporateAction,
    EquityPoint,
    Event,
    EventKind,
    Fill,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    SimulatedOrder,
    TradeLogEntry,
)
from aqros_backtesting_engine.domain.portfolio import (
    apply_fill,
    apply_fill_to_cash,
    portfolio_value,
    would_exceed_buying_power,
)
from aqros_backtesting_engine.domain.slippage import SlippageModel
from aqros_strategy_core import RiskCheck, Strategy, StrategyContext
from aqros_strategy_core.contracts import OrderIntent

__all__ = ["SimulationEngine", "SimulationOutcome"]


@dataclass(frozen=True, slots=True)
class SimulationOutcome:
    """The immutable state produced by one simulation replay."""

    trade_log: tuple[TradeLogEntry, ...]
    equity_curve: tuple[EquityPoint, ...]
    final_positions: tuple[Position, ...]
    final_cash: CashLedger


@dataclass(frozen=True, slots=True)
class _PendingOrder:
    order: SimulatedOrder


class SimulationEngine:
    """Replay sorted events through strategy, risk, execution, and portfolio state."""

    def __init__(
        self,
        events: Iterable[Event],
        strategy: Strategy,
        risk_check: RiskCheck,
        latency_model: LatencyModel,
        slippage_model: SlippageModel,
        commission_model: CommissionModel,
        fill_model: FillModel,
        starting_cash: Decimal,
        leverage_enabled: bool,
        max_leverage: Decimal,
        seed: int,
        features_by_symbol: Mapping[str, Mapping[str, Mapping[datetime, float]]] | None = None,
    ) -> None:
        self._events = tuple(sorted(events, key=lambda event: event.ordering_key))
        self._strategy = strategy
        self._risk_check = risk_check
        self._latency_model = latency_model
        self._slippage_model = slippage_model
        self._commission_model = commission_model
        self._fill_model = fill_model
        self._starting_cash = starting_cash
        self._leverage_enabled = leverage_enabled
        self._max_leverage = max_leverage
        self._seed = seed
        self._features_by_symbol = features_by_symbol or {}

    def run(self) -> SimulationOutcome:
        """Run the event stream once using one seeded random source."""
        rng = random.Random(self._seed)
        positions: dict[str, Position] = {}
        cash = CashLedger(self._starting_cash, self._starting_cash)
        current_prices: dict[str, Decimal] = {}
        current_bars: dict[str, Bar] = {}
        pending: list[_PendingOrder] = []
        seen_order_ids: set[str] = set()
        trade_log: list[TradeLogEntry] = []
        equity_curve: list[EquityPoint] = []
        clock: datetime | None = None
        log_sequence = 0

        def require_clock() -> datetime:
            if clock is None:
                raise RuntimeError("simulation clock has not advanced")
            return clock

        def append_log(
            order: SimulatedOrder,
            outcome: OrderStatus,
            quantity: Decimal,
            price: Decimal | None,
            commission: Decimal,
            reason: str | None = None,
        ) -> None:
            nonlocal log_sequence
            trade_log.append(
                TradeLogEntry(
                    sequence=log_sequence,
                    client_order_id=order.client_order_id,
                    symbol=order.symbol,
                    side=order.side,
                    order_type=order.order_type,
                    quantity=quantity,
                    price=price,
                    commission=commission,
                    clock_time=require_clock(),
                    outcome=outcome,
                    reason=reason,
                )
            )
            log_sequence += 1

        def apply_fill_state(fill: Fill) -> bool:
            nonlocal cash
            if would_exceed_buying_power(
                fill,
                cash,
                positions,
                current_prices,
                self._leverage_enabled,
                self._max_leverage,
            ):
                return False
            positions[fill.symbol] = apply_fill(positions.get(fill.symbol), fill)
            cash = apply_fill_to_cash(cash, fill)
            return True

        def attempt_pending(bar: Bar) -> None:
            nonlocal pending
            remaining: list[_PendingOrder] = []
            for pending_order in pending:
                order = pending_order.order
                if order.symbol != bar.symbol:
                    remaining.append(pending_order)
                    continue
                fill = self._fill_model.fill(
                    order,
                    bar,
                    require_clock(),
                    self._slippage_model,
                    self._commission_model,
                    rng,
                )
                if fill is None:
                    remaining.append(pending_order)
                    continue
                if not apply_fill_state(fill):
                    append_log(
                        order,
                        OrderStatus.UNFILLED,
                        fill.quantity,
                        fill.price,
                        fill.commission,
                        "buying power exceeded",
                    )
                    remaining.append(pending_order)
                    continue
                remaining_quantity = order.quantity - fill.quantity
                outcome = (
                    OrderStatus.FILLED
                    if remaining_quantity <= Decimal(0)
                    else OrderStatus.PARTIALLY_FILLED
                )
                append_log(order, outcome, fill.quantity, fill.price, fill.commission)
                if remaining_quantity > Decimal(0):
                    remaining.append(
                        _PendingOrder(
                            replace(
                                order,
                                quantity=remaining_quantity,
                                status=OrderStatus.PARTIALLY_FILLED,
                            )
                        )
                    )
            pending = remaining

        for event in self._events:
            if clock is not None and event.event_time < clock:
                raise ValueError("events must be processed in non-decreasing event time order")
            clock = event.event_time

            if event.kind is EventKind.CORPORATE_ACTION:
                action = event.payload
                if not isinstance(action, CorporateAction):
                    raise TypeError("corporate-action event payload must be CorporateAction")
                position = positions.get(action.symbol)
                new_position, cash, description = apply_corporate_action(
                    action, position, cash, require_clock()
                )
                if position is not None:
                    positions.pop(action.symbol, None)
                if new_position is not None and new_position.quantity != 0:
                    positions[new_position.symbol] = new_position
                if action.successor_symbol is not None:
                    if action.symbol in current_prices:
                        current_prices[action.successor_symbol] = current_prices.pop(action.symbol)
                    if action.symbol in current_bars:
                        current_bars[action.successor_symbol] = replace(
                            current_bars.pop(action.symbol), symbol=action.successor_symbol
                        )
                if description is not None:
                    action_order = SimulatedOrder(
                        client_order_id=f"corporate-action-{log_sequence}",
                        symbol=action.symbol,
                        side=OrderSide.BUY,
                        order_type=OrderType.MARKET,
                        quantity=Decimal(0),
                        limit_price=None,
                        emitted_at=require_clock(),
                        eligible_at=require_clock(),
                        status=OrderStatus.FILLED,
                        reject_reason=None,
                    )
                    append_log(
                        action_order, OrderStatus.FILLED, Decimal(0), None, Decimal(0), description
                    )

            elif event.kind is EventKind.MARKET_BAR:
                bar = event.payload
                if not isinstance(bar, Bar):
                    raise TypeError("market-bar event payload must be Bar")
                current_prices[bar.symbol] = bar.close
                current_bars[bar.symbol] = bar
                attempt_pending(bar)
                features_for_bar: dict[str, object] = {}
                symbol_features = self._features_by_symbol.get(bar.symbol)
                if symbol_features is not None:
                    for feature_name, time_values in symbol_features.items():
                        value = time_values.get(bar.event_time)
                        if value is not None:
                            features_for_bar[feature_name] = value
                context = StrategyContext(
                    as_of=require_clock(),
                    market_data={bar.symbol: bar},
                    features=features_for_bar,
                    model_outputs={},
                )
                for intent in self._strategy.on_event(context):
                    self._accept_intent(intent, context, pending, seen_order_ids, append_log, rng)

            elif event.kind is EventKind.ORDER_ELIGIBLE:
                continue

            elif event.kind is EventKind.EQUITY_SAMPLE:
                equity_curve.append(
                    EquityPoint(
                        clock_time=require_clock(),
                        total_value=portfolio_value(cash, positions, current_prices),
                    )
                )

        final_positions = tuple(positions[symbol] for symbol in sorted(positions))
        return SimulationOutcome(
            trade_log=tuple(trade_log),
            equity_curve=tuple(equity_curve),
            final_positions=final_positions,
            final_cash=cash,
        )

    def _accept_intent(
        self,
        intent: OrderIntent,
        context: StrategyContext,
        pending: list[_PendingOrder],
        seen_order_ids: set[str],
        append_log: Callable[..., None],
        rng: random.Random,
    ) -> None:
        if intent.client_order_id in seen_order_ids:
            return
        seen_order_ids.add(intent.client_order_id)
        side = OrderSide(intent.side.value)
        order_type = OrderType(intent.order_type.value)
        order = SimulatedOrder(
            client_order_id=intent.client_order_id,
            symbol=intent.symbol,
            side=side,
            order_type=order_type,
            quantity=intent.quantity,
            limit_price=intent.limit_price,
            emitted_at=intent.emitted_at,
            eligible_at=self._latency_model.eligible_time(intent.emitted_at, rng),
            status=OrderStatus.PENDING,
            reject_reason=None,
        )
        decision = self._risk_check.check(intent, context)
        if not decision.approved:
            rejected = replace(order, status=OrderStatus.REJECTED, reject_reason=decision.reason)
            append_log(
                rejected, OrderStatus.REJECTED, intent.quantity, None, Decimal(0), decision.reason
            )
            return
        append_log(order, OrderStatus.PENDING, intent.quantity, None, Decimal(0))
        pending.append(_PendingOrder(order))
