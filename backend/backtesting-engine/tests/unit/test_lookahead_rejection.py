from __future__ import annotations

import random
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from aqros_backtesting_engine.domain.commission import ZeroCommission
from aqros_backtesting_engine.domain.fills import ImmediateFillModel
from aqros_backtesting_engine.domain.latency import ZeroLatency
from aqros_backtesting_engine.domain.lookahead import LookAheadViolationError, assert_knowable
from aqros_backtesting_engine.domain.models import (
    Bar,
    Event,
    EventKind,
    OrderSide,
    OrderType,
    SimulatedOrder,
)
from aqros_backtesting_engine.domain.simulation import SimulationEngine
from aqros_backtesting_engine.domain.slippage import ZeroSlippage

from aqros_strategy_core import StrategyContext
from aqros_strategy_core.contracts import OrderIntent
from aqros_strategy_core.risk import RiskDecision

# ---------------------------------------------------------------------------
# assert_knowable — pure function unit tests
# ---------------------------------------------------------------------------


class TestAssertKnowable:
    def test_passes_when_knowledge_time_before_clock(self) -> None:
        assert_knowable(
            datetime(2024, 1, 2, 14, 30, tzinfo=UTC),
            datetime(2024, 1, 2, 14, 31, tzinfo=UTC),
        )

    def test_passes_when_equal(self) -> None:
        t = datetime(2024, 1, 2, 14, 30, tzinfo=UTC)
        assert_knowable(t, t)

    def test_raises_when_knowledge_time_after_clock(self) -> None:
        with pytest.raises(LookAheadViolationError) as exc:
            assert_knowable(
                datetime(2024, 1, 2, 14, 31, tzinfo=UTC),
                datetime(2024, 1, 2, 14, 30, tzinfo=UTC),
            )
        assert "Look-ahead violation" in str(exc.value)

    def test_includes_context_in_message(self) -> None:
        with pytest.raises(LookAheadViolationError) as exc:
            assert_knowable(
                datetime(2024, 1, 2, 14, 31, tzinfo=UTC),
                datetime(2024, 1, 2, 14, 30, tzinfo=UTC),
                context="AAPL bar",
            )
        assert "AAPL bar" in str(exc.value)

    def test_error_carries_attributes(self) -> None:
        kt = datetime(2024, 1, 2, 14, 31, tzinfo=UTC)
        cl = datetime(2024, 1, 2, 14, 30, tzinfo=UTC)
        with pytest.raises(LookAheadViolationError) as exc:
            assert_knowable(kt, cl, context="ctx")
        assert exc.value.knowledge_time == kt
        assert exc.value.clock == cl
        assert exc.value.context == "ctx"


# ---------------------------------------------------------------------------
# FillModel-level lookahead guard
# ---------------------------------------------------------------------------


class TestFillModelLookahead:
    def test_fill_raises_on_lookahead_bar(self) -> None:
        model = ImmediateFillModel()
        order = SimulatedOrder(
            client_order_id="o1",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("10"),
            limit_price=None,
            emitted_at=datetime(2024, 1, 2, 14, 30, tzinfo=UTC),
            eligible_at=datetime(2024, 1, 2, 14, 30, tzinfo=UTC),
            status=None,
            reject_reason=None,
        )
        bar = Bar(
            symbol="AAPL",
            event_time=datetime(2024, 1, 2, 14, 30, tzinfo=UTC),
            knowledge_time=datetime(2024, 1, 2, 21, 0, tzinfo=UTC),
            open=Decimal("100"),
            high=Decimal("105"),
            low=Decimal("99"),
            close=Decimal("103"),
            volume=Decimal("1000000"),
        )
        clock = datetime(2024, 1, 2, 14, 30, tzinfo=UTC)
        with pytest.raises(LookAheadViolationError):
            model.fill(order, bar, clock, ZeroSlippage(), ZeroCommission(), random.Random(0))


# ---------------------------------------------------------------------------
# Engine-level lookahead rejection
# ---------------------------------------------------------------------------


class _EmittingStrategy:
    def __init__(self) -> None:
        self._emitted = False

    def on_event(self, context: StrategyContext) -> list[OrderIntent]:
        if not self._emitted:
            self._emitted = True
            return [
                OrderIntent(
                    client_order_id="engine-lookahead-test",
                    symbol="AAPL",
                    side=OrderSide.BUY,
                    order_type=OrderType.MARKET,
                    quantity=Decimal("10"),
                    limit_price=None,
                    emitted_at=context.as_of,
                )
            ]
        return []


class _PassingRiskCheck:
    def check(self, order_intent: OrderIntent, context: StrategyContext) -> RiskDecision:
        return RiskDecision(approved=True, reason=None)


class TestEngineLookaheadRejection:
    def test_engine_raises_on_lookahead(self) -> None:
        bar1 = Bar(
            symbol="AAPL",
            event_time=datetime(2024, 1, 2, 14, 30, tzinfo=UTC),
            knowledge_time=datetime(2024, 1, 2, 14, 30, tzinfo=UTC),
            open=Decimal("100"),
            high=Decimal("105"),
            low=Decimal("99"),
            close=Decimal("103"),
            volume=Decimal("1000000"),
        )
        bar2 = Bar(
            symbol="AAPL",
            event_time=datetime(2024, 1, 3, 14, 30, tzinfo=UTC),
            knowledge_time=datetime(2024, 1, 3, 21, 0, tzinfo=UTC),
            open=Decimal("103"),
            high=Decimal("107"),
            low=Decimal("101"),
            close=Decimal("106"),
            volume=Decimal("1200000"),
        )
        events = [
            Event(
                event_time=bar1.event_time,
                knowledge_time=bar1.knowledge_time,
                kind=EventKind.MARKET_BAR,
                sequence=0,
                payload=bar1,
            ),
            Event(
                event_time=bar2.event_time,
                knowledge_time=bar2.knowledge_time,
                kind=EventKind.MARKET_BAR,
                sequence=1,
                payload=bar2,
            ),
        ]
        engine = SimulationEngine(
            events=events,
            strategy=_EmittingStrategy(),
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
        with pytest.raises(LookAheadViolationError):
            engine.run()

    def test_engine_ok_with_no_lookahead(self) -> None:
        bar1 = Bar(
            symbol="AAPL",
            event_time=datetime(2024, 1, 2, 14, 30, tzinfo=UTC),
            knowledge_time=datetime(2024, 1, 2, 14, 30, tzinfo=UTC),
            open=Decimal("100"),
            high=Decimal("105"),
            low=Decimal("99"),
            close=Decimal("103"),
            volume=Decimal("1000000"),
        )
        bar2 = Bar(
            symbol="AAPL",
            event_time=datetime(2024, 1, 3, 14, 30, tzinfo=UTC),
            knowledge_time=datetime(2024, 1, 3, 14, 30, tzinfo=UTC),
            open=Decimal("103"),
            high=Decimal("107"),
            low=Decimal("101"),
            close=Decimal("106"),
            volume=Decimal("1200000"),
        )
        events = [
            Event(
                event_time=bar1.event_time,
                knowledge_time=bar1.knowledge_time,
                kind=EventKind.MARKET_BAR,
                sequence=0,
                payload=bar1,
            ),
            Event(
                event_time=bar2.event_time,
                knowledge_time=bar2.knowledge_time,
                kind=EventKind.MARKET_BAR,
                sequence=1,
                payload=bar2,
            ),
        ]
        engine = SimulationEngine(
            events=events,
            strategy=_EmittingStrategy(),
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
        result = engine.run()
        assert result.final_cash.balance < Decimal("100000")
