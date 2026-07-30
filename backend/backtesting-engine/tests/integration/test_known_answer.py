from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from aqros_backtesting_engine.domain.commission import ZeroCommission
from aqros_backtesting_engine.domain.fills import ImmediateFillModel
from aqros_backtesting_engine.domain.latency import ZeroLatency
from aqros_backtesting_engine.domain.models import (
    Bar,
    EquityPoint,
    Event,
    EventKind,
    OrderSide,
    OrderType,
)
from aqros_backtesting_engine.domain.simulation import SimulationEngine
from aqros_backtesting_engine.domain.slippage import ZeroSlippage

from aqros_strategy_core import StrategyContext
from aqros_strategy_core.contracts import OrderIntent
from aqros_strategy_core.risk import RiskDecision


class _BuyOnceStrategy:
    def __init__(self, quantity: Decimal = Decimal("10")):
        self._quantity = quantity
        self._emitted = False

    def on_event(self, context: StrategyContext) -> list[OrderIntent]:
        if self._emitted:
            return []
        self._emitted = True
        symbol = next(iter(context.market_data), "UNKNOWN")
        return [
            OrderIntent(
                client_order_id="known-answer-buy",
                symbol=symbol,
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=self._quantity,
                limit_price=None,
                emitted_at=context.as_of,
            )
        ]


class _PassingRiskCheck:
    def check(self, order_intent: OrderIntent, context: StrategyContext) -> RiskDecision:
        return RiskDecision(approved=True, reason=None)


_BARS = [
    Bar(
        symbol="AAPL",
        event_time=datetime(2024, 1, 2, 14, 30, tzinfo=UTC),
        knowledge_time=datetime(2024, 1, 2, 14, 30, tzinfo=UTC),
        open=Decimal("100"),
        high=Decimal("105"),
        low=Decimal("99"),
        close=Decimal("103"),
        volume=Decimal("1000000"),
    ),
    Bar(
        symbol="AAPL",
        event_time=datetime(2024, 1, 3, 14, 30, tzinfo=UTC),
        knowledge_time=datetime(2024, 1, 3, 14, 30, tzinfo=UTC),
        open=Decimal("103"),
        high=Decimal("108"),
        low=Decimal("102"),
        close=Decimal("106"),
        volume=Decimal("1200000"),
    ),
    Bar(
        symbol="AAPL",
        event_time=datetime(2024, 1, 4, 14, 30, tzinfo=UTC),
        knowledge_time=datetime(2024, 1, 4, 14, 30, tzinfo=UTC),
        open=Decimal("106"),
        high=Decimal("107"),
        low=Decimal("104"),
        close=Decimal("105"),
        volume=Decimal("1100000"),
    ),
]


def _events() -> list[Event]:
    seq = 0
    events: list[Event] = []
    for bar in _BARS:
        events.append(
            Event(
                event_time=bar.event_time,
                knowledge_time=bar.knowledge_time,
                kind=EventKind.MARKET_BAR,
                sequence=seq,
                payload=bar,
            )
        )
        seq += 1
        close = datetime(
            bar.event_time.year, bar.event_time.month, bar.event_time.day, 21, 0, tzinfo=UTC
        )
        events.append(
            Event(
                event_time=close,
                knowledge_time=close,
                kind=EventKind.EQUITY_SAMPLE,
                sequence=seq,
                payload=None,
            )
        )
        seq += 1
    return events


def _run_engine(strategy: _BuyOnceStrategy) -> list[EquityPoint]:
    engine = SimulationEngine(
        events=_events(),
        strategy=strategy,
        risk_check=_PassingRiskCheck(),
        latency_model=ZeroLatency(),
        slippage_model=ZeroSlippage(),
        commission_model=ZeroCommission(),
        fill_model=ImmediateFillModel(),
        starting_cash=Decimal("100000"),
        leverage_enabled=False,
        max_leverage=Decimal("1.0"),
        seed=42,
    )
    return list(engine.run().equity_curve)


class TestKnownAnswer:
    def test_flat_equity_without_trades(self) -> None:
        strategy = _BuyOnceStrategy(quantity=Decimal("0"))
        curve = _run_engine(strategy)
        close1 = datetime(2024, 1, 2, 21, 0, tzinfo=UTC)
        close2 = datetime(2024, 1, 3, 21, 0, tzinfo=UTC)
        close3 = datetime(2024, 1, 4, 21, 0, tzinfo=UTC)
        assert curve[0] == EquityPoint(close1, Decimal("100000"))
        assert curve[1] == EquityPoint(close2, Decimal("100000"))
        assert curve[2] == EquityPoint(close3, Decimal("100000"))

    def test_buy_10_shares_produces_expected_equity(self) -> None:
        strategy = _BuyOnceStrategy(quantity=Decimal("10"))
        curve = _run_engine(strategy)

        day1_close = datetime(2024, 1, 2, 21, 0, tzinfo=UTC)
        day2_close = datetime(2024, 1, 3, 21, 0, tzinfo=UTC)
        day3_close = datetime(2024, 1, 4, 21, 0, tzinfo=UTC)

        assert len(curve) == 3

        # Day 1: no trade yet, equity = starting cash
        assert curve[0] == EquityPoint(day1_close, Decimal("100000"))

        # Day 2: order was filled on bar2 open at 103, cash spent = 10 * 103 = 1030
        # cash = 100000 - 1030 = 98970, position = 10 @ 103
        # current_prices[AAPL] = bar2.close = 106
        # equity = 98970 + 10 * 106 = 98970 + 1060 = 100030
        assert curve[1] == EquityPoint(day2_close, Decimal("100030"))

        # Day 3: no new trades, price = bar3.close = 105
        # equity = 98970 + 10 * 105 = 98970 + 1050 = 100020
        assert curve[2] == EquityPoint(day3_close, Decimal("100020"))

    def test_buy_100_shares_produces_expected_equity(self) -> None:
        strategy = _BuyOnceStrategy(quantity=Decimal("100"))
        curve = _run_engine(strategy)

        day2_close = datetime(2024, 1, 3, 21, 0, tzinfo=UTC)
        day3_close = datetime(2024, 1, 4, 21, 0, tzinfo=UTC)

        cash_after_fill = Decimal("100000") - Decimal("100") * Decimal("103")
        day2_equity = cash_after_fill + Decimal("100") * Decimal("106")
        day3_equity = cash_after_fill + Decimal("100") * Decimal("105")

        assert len(curve) == 3
        assert curve[1] == EquityPoint(day2_close, day2_equity)
        assert curve[2] == EquityPoint(day3_close, day3_equity)

    def test_deterministic_equity_curve(self) -> None:
        curve1 = _run_engine(_BuyOnceStrategy(quantity=Decimal("10")))
        curve2 = _run_engine(_BuyOnceStrategy(quantity=Decimal("10")))
        assert curve1 == curve2
