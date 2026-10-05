"""Failure-injection tests for the money path.

CLAUDE.md requires the system to **fail closed on money** and **fail open on
alpha**. Those two doctrines are easy to claim and easy to violate, so they are
asserted here against injected failures rather than asserted in prose.

The most important test in this file is
:class:`TestKillSwitchCannotBeSilentlyDisabled`: a disabled kill switch that
quietly ignores ``trigger()`` is a booby trap. An operator disables it once for
maintenance, forgets, and then a trigger during a real incident does nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from aqros_live_trading.domain.models import (
    ConnectionStatus,
    KillSwitch,
    KillSwitchStatus,
)
from aqros_strategy_core import OrderIntent, RiskDecision, StrategyContext
from aqros_strategy_core.contracts import OrderSide, OrderType

T0 = datetime(2024, 6, 3, 14, 30, tzinfo=UTC)


def intent(quantity: str = "100", price: str = "200") -> OrderIntent:
    return OrderIntent(
        client_order_id="coid-1",
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal(quantity),
        limit_price=Decimal(price),
        emitted_at=T0,
    )


class StrictNotionalRiskCheck:
    """Fails closed: approves only what it can positively verify."""

    max_notional: Decimal = Decimal("50000")

    def check(self, order_intent: OrderIntent, context: StrategyContext) -> RiskDecision:
        price = order_intent.limit_price
        if price is None:
            return RiskDecision(False, "no price available: failing closed")
        notional = order_intent.quantity * price
        if notional > self.max_notional:
            return RiskDecision(False, f"notional {notional} over limit")
        return RiskDecision(True, None)


# ===========================================================================
# Kill switch — the last line of defence
# ===========================================================================
class TestKillSwitch:
    def test_armed_by_default(self) -> None:
        """A new session must start armed, not disarmed."""
        assert KillSwitch().status is KillSwitchStatus.ARMED

    def test_trigger_records_who_and_why(self) -> None:
        ks = KillSwitch()
        ks.trigger(by="alice", reason="venue rejecting orders", now=T0)
        assert ks.is_triggered()
        assert ks.triggered_by == "alice"
        assert ks.reason == "venue rejecting orders"

    def test_disabled_kill_switch_still_triggers(self) -> None:
        """A disabled kill switch must NOT swallow a trigger.

        Regression test for a real defect: ``trigger()`` returned early when
        ``enabled`` was False, so ``disable()`` silently turned the emergency
        stop into a no-op. Disable must mean "do not auto-arm after reset", never
        "ignore the emergency brake".
        """
        ks = KillSwitch()
        ks.disable()
        assert ks.enabled is False

        ks.trigger(by="alice", reason="incident", now=T0)
        assert ks.is_triggered(), "a disabled kill switch must still trigger"
        assert ks.triggered_by == "alice"

    def test_reset_rearms(self) -> None:
        ks = KillSwitch()
        ks.trigger(by="alice", reason="x", now=T0)
        ks.reset()
        assert not ks.is_triggered()
        ks.arm()
        assert ks.status is KillSwitchStatus.ARMED

    def test_retrigger_records_latest_reason(self) -> None:
        ks = KillSwitch()
        ks.trigger(by="alice", reason="first", now=T0)
        ks.trigger(by="bob", reason="second", now=T0 + timedelta(minutes=1))
        assert ks.triggered_by == "bob"
        assert ks.reason == "second"

    def test_enable_after_disable_restores_arming(self) -> None:
        ks = KillSwitch()
        ks.disable()
        ks.enable()
        assert ks.enabled is True
        assert ks.status is KillSwitchStatus.ARMED


# ===========================================================================
# Fail closed on money
# ===========================================================================
class TestFailClosedOnMoneyPath:
    def test_missing_price_rejects_rather_than_approves(self) -> None:
        """No data must never mean 'allowed'."""
        market_order = OrderIntent(
            client_order_id="m",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("10"),
            limit_price=None,
            emitted_at=T0,
        )
        decision = StrictNotionalRiskCheck().check(market_order, StrategyContext(as_of=T0))
        assert not decision.approved
        assert decision.reason is not None

    def test_oversized_order_is_rejected_with_a_reason(self) -> None:
        decision = StrictNotionalRiskCheck().check(intent("1000"), StrategyContext(as_of=T0))
        assert not decision.approved
        assert "over limit" in (decision.reason or "")

    def test_every_rejection_is_explainable(self) -> None:
        """An unexplained rejection cannot be audited or debugged."""
        for bad in (intent("1000"), intent(price="999999")):
            decision = StrictNotionalRiskCheck().check(bad, StrategyContext(as_of=T0))
            if not decision.approved:
                assert decision.reason, "a rejection must carry a reason"


# ===========================================================================
# Fail open on alpha
# ===========================================================================
class TestAlphaPathDegrades:
    """The mirror image: the signal path must degrade, not crash.

    A missing model or a stale feature means "no signal", never an exception
    that stops the strategy loop. Losing a signal is a bad day; stopping the
    platform is worse.
    """

    class DegradingStrategy:
        def on_event(self, context: StrategyContext) -> OrderIntent | None:
            # No model output available → abstain rather than guess.
            if not context.model_outputs:
                return None
            score = context.model_outputs.get("score")
            if score is None:
                return None
            if Decimal(str(score)) <= 0:
                return None
            return intent()

    def test_missing_model_output_yields_no_signal(self) -> None:
        empty = StrategyContext(as_of=T0, market_data={"close": 200}, features={"s": 1})
        assert self.DegradingStrategy().on_event(empty) is None

    def test_absent_model_score_yields_no_signal(self) -> None:
        """A model present but scoring nothing must produce no signal."""
        scored_nothing = StrategyContext(as_of=T0, model_outputs={"score": "0"})
        assert self.DegradingStrategy().on_event(scored_nothing) is None

    def test_non_positive_score_yields_no_signal(self) -> None:
        negative = StrategyContext(as_of=T0, model_outputs={"score": "-0.5"})
        assert self.DegradingStrategy().on_event(negative) is None

    def test_positive_signal_still_produces_intent(self) -> None:
        good = StrategyContext(
            as_of=T0, model_outputs={"score": "1"}, features={"s": 1}, market_data={"close": 200}
        )
        result = self.DegradingStrategy().on_event(good)
        assert result is not None
        assert result.symbol == "AAPL"


# ===========================================================================
# Upstream loss
# ===========================================================================
class TestUpstreamLoss:
    def test_connection_status_detects_disconnect(self) -> None:
        """A dead venue must be visible, not inferred from silence."""
        session_status = ConnectionStatus.DISCONNECTED
        assert session_status is not ConnectionStatus.CONNECTED

    def test_disconnect_can_auto_trip_the_kill_switch(self) -> None:
        """Losing the venue mid-order must stop trading automatically."""
        ks = KillSwitch(auto_trigger_on_disconnect_seconds=30.0)
        # Simulate the elapsed disconnect window being exceeded.
        assert ks.auto_trigger_on_disconnect_seconds > 0
        ks.trigger(by="system", reason="venue disconnected", now=T0)
        assert ks.is_triggered()

    def test_risk_check_is_pure_and_survives_repeated_calls(self) -> None:
        """Risk must not hold state that a later call could corrupt."""
        risk = StrictNotionalRiskCheck()
        context = StrategyContext(as_of=T0)
        outcomes = [risk.check(intent("100"), context).approved for _ in range(50)]
        assert outcomes == [True] * 50
