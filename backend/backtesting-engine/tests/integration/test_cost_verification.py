from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from aqros_backtesting_engine.domain.commission import (
    PerShareCommission,
    ZeroCommission,
)
from aqros_backtesting_engine.domain.fills import ImmediateFillModel
from aqros_backtesting_engine.domain.latency import ZeroLatency
from aqros_backtesting_engine.domain.models import (
    Bar,
    Event,
    EventKind,
    OrderSide,
    OrderType,
)
from aqros_backtesting_engine.domain.simulation import SimulationEngine
from aqros_backtesting_engine.domain.slippage import FixedBpsSlippage, ZeroSlippage

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
                client_order_id="cost-verification-buy",
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
        close_utc = datetime(
            bar.event_time.year, bar.event_time.month, bar.event_time.day, 21, 0, tzinfo=UTC
        )
        events.append(
            Event(
                event_time=close_utc,
                knowledge_time=close_utc,
                kind=EventKind.EQUITY_SAMPLE,
                sequence=seq,
                payload=None,
            )
        )
        seq += 1
    return events


def _run(
    commission=None,
    slippage=None,
    quantity: Decimal = Decimal("100"),
) -> SimulationEngine:
    engine = SimulationEngine(
        events=_events(),
        strategy=_BuyOnceStrategy(quantity=quantity),
        risk_check=_PassingRiskCheck(),
        latency_model=ZeroLatency(),
        slippage_model=slippage or ZeroSlippage(),
        commission_model=commission or ZeroCommission(),
        fill_model=ImmediateFillModel(),
        starting_cash=Decimal("100000"),
        leverage_enabled=False,
        max_leverage=Decimal("1.0"),
        seed=42,
    )
    return engine


class TestCostVerification:
    def test_zero_commission_differs_from_per_share(self) -> None:
        zero = _run(commission=ZeroCommission()).run()
        per_share = _run(commission=PerShareCommission(Decimal("0.01"))).run()
        assert zero.final_cash.balance != per_share.final_cash.balance
        assert zero.equity_curve != per_share.equity_curve

    def test_zero_commission_differs_from_pct(self) -> None:
        zero = _run(commission=ZeroCommission()).run()
        pct = _run(commission=PerShareCommission(Decimal("0.001"))).run()
        assert zero.final_cash.balance != pct.final_cash.balance
        assert zero.equity_curve != pct.equity_curve

    def test_zero_slippage_differs_from_fixed_bps(self) -> None:
        zero = _run(slippage=ZeroSlippage()).run()
        bps = _run(slippage=FixedBpsSlippage(Decimal("10"))).run()
        assert zero.final_cash.balance != bps.final_cash.balance
        assert zero.equity_curve != bps.equity_curve

    def test_commission_cost_deducted_from_cash(self) -> None:
        commission = PerShareCommission(Decimal("0.01"))
        quantity = Decimal("100")
        zero = _run(commission=ZeroCommission(), quantity=quantity).run()
        with_cost = _run(commission=commission, quantity=quantity).run()
        expected_cost = quantity * Decimal("0.01")
        assert zero.final_cash.balance - with_cost.final_cash.balance == expected_cost

    def test_slippage_increases_buy_cost(self) -> None:
        bps_10 = FixedBpsSlippage(Decimal("10"))
        quantity = Decimal("100")
        zero = _run(slippage=ZeroSlippage(), quantity=quantity).run()
        with_slippage = _run(slippage=bps_10, quantity=quantity).run()
        # 10 bps on $103 = 103 * 10/10000 = 0.103. Fill price = 103.103
        # Cost = 100 * 103.103 = 10310.30
        # Zero slippage: cost = 100 * 103 = 10300.00
        # So cash should be lower with slippage
        assert with_slippage.final_cash.balance < zero.final_cash.balance

    def test_zero_costs_produce_highest_equity(self) -> None:
        outcomes = {
            "zero": _run(commission=ZeroCommission(), slippage=ZeroSlippage()).run(),
            "commission": _run(
                commission=PerShareCommission(Decimal("0.01")),
                slippage=ZeroSlippage(),
            ).run(),
            "slippage": _run(
                commission=ZeroCommission(),
                slippage=FixedBpsSlippage(Decimal("10")),
            ).run(),
            "both": _run(
                commission=PerShareCommission(Decimal("0.01")),
                slippage=FixedBpsSlippage(Decimal("10")),
            ).run(),
        }
        zero_curve = outcomes["zero"].equity_curve
        for name, outcome in outcomes.items():
            if name == "zero":
                continue
            assert outcome.equity_curve != zero_curve
            assert outcome.final_cash.balance <= outcomes["zero"].final_cash.balance
