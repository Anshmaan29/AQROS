"""Golden-replay gate for the shared strategy/risk core.

CLAUDE.md §5 makes this mandatory: *"A change to the backtest/live shared core
must pass the deterministic golden-replay test."* The Backtesting Engine has its
own golden replay, but nothing guarded the core itself — the code that backtest,
paper, and live all execute.

What this pins down
-------------------
A canonical scenario is replayed through the shared ``Strategy``/``RiskCheck``
contracts, and two things are asserted:

1. **Determinism** — the same input produces byte-identical output every run.
   Non-determinism here would silently make every backtest result unreproducible.
2. **Immutability of intent** — a golden digest over the emitted intents. Any
   change to sizing, rounding, ordering, or the contract's defaults changes the
   digest and fails this test, forcing a deliberate, reviewed update.

The digest is intentionally opaque: a reviewer should have to read the diff to
understand what changed, which slows down an accidental behaviour change and
speeds up a deliberate one.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from aqros_strategy_core import OrderIntent, RiskCheck, RiskDecision, Strategy, StrategyContext
from aqros_strategy_core.contracts import OrderSide, OrderType

# --- Frozen scenario --------------------------------------------------------
# Fixed clock. Any wall-clock read would make this test flaky and would violate
# the no-wall-clock rule in CLAUDE.md §5.
T0 = datetime(2024, 3, 11, 14, 30, tzinfo=UTC)
SYMBOL = "AAPL"


@dataclass
class BuyTheDipStrategy:
    """A deterministic strategy: buy when the feature signal is positive.

    Implements the shared ``Strategy`` Protocol structurally — no inheritance,
    which is the point of using a Protocol.
    """

    threshold: Decimal = Decimal("0.50")
    quantity: Decimal = Decimal("100")

    def on_event(self, context: StrategyContext) -> OrderIntent | None:
        signal = context.features.get("dip_score")
        price = context.market_data.get("close")
        if signal is None or price is None:
            return None
        if Decimal(str(signal)) <= self.threshold:
            return None
        # client_order_id is derived from stable inputs only — never a random
        # uuid or a clock read — so replaying the same event cannot duplicate.
        return OrderIntent(
            client_order_id=f"{SYMBOL}-{context.as_of.date().isoformat()}-dip",
            symbol=SYMBOL,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=self.quantity,
            limit_price=Decimal(str(price)),
            emitted_at=context.as_of,
        )


class MaxNotionalRiskCheck:
    """A minimal ``RiskCheck`` implementation for the replay.

    Note the market-order handling: a MARKET intent carries no ``limit_price``,
    so notional must fall back to the context's reference price. Computing
    notional from ``limit_price or 0`` would price a market order at zero and
    approve an unbounded order — a real bypass, not a hypothetical one.
    """

    max_notional: Decimal = Decimal("100000")

    def check(self, order_intent: OrderIntent, context: StrategyContext) -> RiskDecision:
        if order_intent.limit_price is not None:
            reference_price = order_intent.limit_price
        else:
            # MARKET order: price it against the current close, fail closed if absent.
            close = context.market_data.get("close")
            if close is None:
                return RiskDecision(
                    approved=False, reason="cannot price a market order: no reference price"
                )
            reference_price = Decimal(str(close))

        notional = order_intent.quantity * reference_price
        if notional > self.max_notional:
            return RiskDecision(approved=False, reason=f"notional {notional} exceeds limit")
        return RiskDecision(approved=True, reason=None)


def scenario_contexts() -> list[StrategyContext]:
    """The canonical replay input: nine sessions, one of them not a signal."""
    contexts: list[StrategyContext] = []
    for day in range(9):
        as_of = T0 + timedelta(days=day)
        # Only day 3, 5, and 7 cross the threshold.
        dip_score = Decimal("0.90") if day in (3, 5, 7) else Decimal("0.10")
        close = Decimal("180.00") + Decimal(day)
        contexts.append(
            StrategyContext(
                as_of=as_of,
                market_data={"symbol": SYMBOL, "close": close},
                features={"dip_score": dip_score},
                model_outputs={"model": "test", "version": 1},
            )
        )
    return contexts


def replay(strategy: Strategy, risk: RiskCheck) -> list[str]:
    """Drive the shared contracts and return canonical lines for hashing."""
    lines: list[str] = []
    for context in scenario_contexts():
        intent = strategy.on_event(context)
        if intent is None:
            lines.append(f"{context.as_of.isoformat()}|no-intent")
            continue
        decision = risk.check(intent, context)
        lines.append(
            "|".join(
                [
                    context.as_of.isoformat(),
                    intent.client_order_id,
                    intent.symbol,
                    intent.side.value,
                    intent.order_type.value,
                    str(intent.quantity),
                    str(intent.limit_price),
                    str(decision.approved),
                    decision.reason or "",
                ]
            )
        )
    return lines


def digest(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


# The golden digest. Update ONLY with a deliberate, reviewed behaviour change.
GOLDEN_DIGEST = "d8034237ee260d3f6f0d28e387a397b49f33be22c62f38e48cf8ce681fec8b2d"


class TestStrategySatisfiesProtocol:
    def test_strategy_is_structurally_a_strategy(self) -> None:
        assert isinstance(BuyTheDipStrategy(), Strategy)

    def test_risk_check_is_structurally_a_risk_check(self) -> None:
        assert isinstance(MaxNotionalRiskCheck(), RiskCheck)


class TestDeterminism:
    def test_repeated_replays_are_identical(self) -> None:
        """The foundational reproducibility guarantee."""
        first = replay(BuyTheDipStrategy(), MaxNotionalRiskCheck())
        for _ in range(20):
            assert replay(BuyTheDipStrategy(), MaxNotionalRiskCheck()) == first

    def test_replay_does_not_mutate_the_scenario(self) -> None:
        before = [c.as_of for c in scenario_contexts()]
        replay(BuyTheDipStrategy(), MaxNotionalRiskCheck())
        assert [c.as_of for c in scenario_contexts()] == before

    def test_context_is_immutable(self) -> None:
        """A strategy must not be able to edit its own inputs."""
        import dataclasses

        context = scenario_contexts()[0]
        with pytest.raises(dataclasses.FrozenInstanceError):
            context.as_of = T0 + timedelta(days=99)  # type: ignore[misc]


class TestGoldenDigest:
    def test_output_matches_golden_digest(self) -> None:
        """Guards the shared core's observable behaviour.

        If this fails, the emitted intents or the risk decisions changed. That
        is either a bug or a deliberate change — decide which before updating
        ``GOLDEN_DIGEST``.
        """
        actual = digest(replay(BuyTheDipStrategy(), MaxNotionalRiskCheck()))
        assert actual == GOLDEN_DIGEST, (
            "the shared strategy/risk core changed behaviour. Review the diff "
            "carefully; if the change is intended, update GOLDEN_DIGEST in "
            "libs/aqros-strategy-core/tests/test_golden_replay.py with a commit "
            "message explaining why backtest, paper, and live should now agree "
            "on the new output."
        )

    def test_scenario_emits_the_expected_number_of_intents(self) -> None:
        """A sanity anchor so a silently-empty replay cannot pass."""
        lines = replay(BuyTheDipStrategy(), MaxNotionalRiskCheck())
        intents = [line for line in lines if "|no-intent" not in line]
        assert len(intents) == 3
        assert len(lines) == 9


class TestRiskCheckIsNonBypassable:
    def test_oversized_order_is_rejected(self) -> None:
        intent = OrderIntent(
            client_order_id="big",
            symbol=SYMBOL,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("1000"),
            limit_price=Decimal("200"),
            emitted_at=T0,
        )
        decision = MaxNotionalRiskCheck().check(intent, StrategyContext(as_of=T0))
        assert not decision.approved
        assert "exceeds limit" in (decision.reason or "")

    def test_market_order_is_priced_not_treated_as_zero(self) -> None:
        """Regression: a MARKET order has no limit price.

        Priced at zero it would sail through any notional limit, which is a
        complete bypass of the check for exactly the order type with the most
        execution risk.
        """
        context = StrategyContext(as_of=T0, market_data={"close": Decimal("200")})
        intent = OrderIntent(
            client_order_id="big-market",
            symbol=SYMBOL,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("1000"),
            limit_price=None,
            emitted_at=T0,
        )
        decision = MaxNotionalRiskCheck().check(intent, context)
        assert not decision.approved
        assert "exceeds limit" in (decision.reason or "")

    def test_market_order_fails_closed_without_a_price(self) -> None:
        """No reference price must mean a rejection, never an approval."""
        intent = OrderIntent(
            client_order_id="no-price",
            symbol=SYMBOL,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("1"),
            limit_price=None,
            emitted_at=T0,
        )
        decision = MaxNotionalRiskCheck().check(intent, StrategyContext(as_of=T0))
        assert not decision.approved


class TestOrderIntentContract:
    def test_intent_carries_no_wall_clock(self) -> None:
        """``emitted_at`` must come from the injected context, not ``now()``."""
        context = scenario_contexts()[3]
        intent = BuyTheDipStrategy().on_event(context)
        assert intent is not None
        assert intent.emitted_at == context.as_of

    def test_client_order_id_is_deterministic(self) -> None:
        """A replayed event must not produce a second, different order."""
        context = scenario_contexts()[3]
        first = BuyTheDipStrategy().on_event(context)
        second = BuyTheDipStrategy().on_event(context)
        assert first is not None and second is not None
        assert first.client_order_id == second.client_order_id

    def test_intent_is_frozen(self) -> None:
        import dataclasses

        intent = BuyTheDipStrategy().on_event(scenario_contexts()[3])
        assert intent is not None
        with pytest.raises(dataclasses.FrozenInstanceError):
            intent.quantity = Decimal("1")  # type: ignore[misc]

    def test_stop_price_defaults_to_none_for_limit_orders(self) -> None:
        intent = BuyTheDipStrategy().on_event(scenario_contexts()[3])
        assert intent is not None
        assert intent.stop_price is None
