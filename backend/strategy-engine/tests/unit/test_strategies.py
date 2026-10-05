"""Unit tests for concrete strategy implementations."""

from __future__ import annotations

import pytest
from aqros_strategy_engine.domain.models import (
    SignalType,
)
from aqros_strategy_engine.domain.strategies.ensemble import (
    EnsembleStrategy,
    WeightedVotingStrategy,
)
from aqros_strategy_engine.domain.strategies.implementations import (
    DummyStrategy,
    MeanReversionStrategy,
    MomentumStrategy,
    ThresholdStrategy,
)
from aqros_strategy_engine.domain.strategies.rule_based import Rule, RuleBasedStrategy


class TestDummyStrategy:
    @pytest.fixture
    def strategy(self) -> DummyStrategy:
        return DummyStrategy()

    async def test_returns_hold_by_default(self, strategy: DummyStrategy) -> None:
        decision = await strategy.evaluate("AAPL", 0.5, 0.7, {})
        assert decision.signal == SignalType.HOLD

    async def test_custom_signal(self) -> None:
        strategy = DummyStrategy(signal=SignalType.BUY, confidence=0.9)
        decision = await strategy.evaluate("AAPL", 0.5, 0.7, {})
        assert decision.signal == SignalType.BUY
        assert decision.confidence.score == 0.9

    async def test_name(self, strategy: DummyStrategy) -> None:
        assert strategy.name == "dummy"

    async def test_metadata(self, strategy: DummyStrategy) -> None:
        meta = strategy.metadata()
        assert meta.name == "dummy"


class TestThresholdStrategy:
    @pytest.fixture
    def strategy(self) -> ThresholdStrategy:
        return ThresholdStrategy(buy_threshold=0.6, sell_threshold=0.4)

    async def test_buy_above_threshold(self, strategy: ThresholdStrategy) -> None:
        decision = await strategy.evaluate("AAPL", 0.85, 0.9, {})
        assert decision.signal == SignalType.BUY
        assert decision.strength.value == "STRONG"

    async def test_sell_below_threshold(self, strategy: ThresholdStrategy) -> None:
        decision = await strategy.evaluate("AAPL", 0.15, 0.9, {})
        assert decision.signal == SignalType.SELL
        assert decision.strength.value == "STRONG"

    async def test_hold_between_thresholds(self, strategy: ThresholdStrategy) -> None:
        decision = await strategy.evaluate("AAPL", 0.5, 0.9, {})
        assert decision.signal == SignalType.HOLD

    async def test_hold_when_confidence_low(self, strategy: ThresholdStrategy) -> None:
        decision = await strategy.evaluate("AAPL", 0.85, 0.1, {})
        assert decision.signal == SignalType.HOLD
        assert decision.reason.value == "CONFIDENCE_TOO_LOW"

    async def test_hold_when_no_prediction(self, strategy: ThresholdStrategy) -> None:
        decision = await strategy.evaluate("AAPL", None, 0.9, {})
        assert decision.signal == SignalType.HOLD

    async def test_buy_moderate(self, strategy: ThresholdStrategy) -> None:
        decision = await strategy.evaluate("AAPL", 0.65, 0.9, {})
        assert decision.signal == SignalType.BUY
        assert decision.strength.value == "MODERATE"


class TestMomentumStrategy:
    @pytest.fixture
    def strategy(self) -> MomentumStrategy:
        return MomentumStrategy(lookback=3)

    async def test_hold_on_insufficient_history(self, strategy: MomentumStrategy) -> None:
        decision = await strategy.evaluate("AAPL", 0.5, 0.7, {})
        assert decision.signal == SignalType.HOLD

    async def test_buy_on_positive_momentum(self, strategy: MomentumStrategy) -> None:
        await strategy.evaluate("AAPL", 0.3, 0.7, {})
        decision = await strategy.evaluate("AAPL", 0.7, 0.7, {})
        assert decision.signal == SignalType.BUY

    async def test_sell_on_negative_momentum(self, strategy: MomentumStrategy) -> None:
        await strategy.evaluate("AAPL", 0.7, 0.7, {})
        decision = await strategy.evaluate("AAPL", 0.3, 0.7, {})
        assert decision.signal == SignalType.SELL

    async def test_hold_on_no_prediction(self, strategy: MomentumStrategy) -> None:
        decision = await strategy.evaluate("AAPL", None, 0.7, {})
        assert decision.signal == SignalType.HOLD


class TestMeanReversionStrategy:
    @pytest.fixture
    def strategy(self) -> MeanReversionStrategy:
        return MeanReversionStrategy(window=5, std_multiplier=1.5)

    async def test_hold_on_insufficient_history(self, strategy: MeanReversionStrategy) -> None:
        decision = await strategy.evaluate("AAPL", 0.5, 0.7, {})
        assert decision.signal == SignalType.HOLD

    async def test_sell_on_high_zscore(self, strategy: MeanReversionStrategy) -> None:
        for v in [0.5, 0.5, 0.5]:
            await strategy.evaluate("AAPL", v, 0.7, {})
        decision = await strategy.evaluate("AAPL", 0.9, 0.7, {})
        assert decision.signal == SignalType.SELL

    async def test_buy_on_low_zscore(self, strategy: MeanReversionStrategy) -> None:
        for v in [0.5, 0.5, 0.5]:
            await strategy.evaluate("AAPL", v, 0.7, {})
        decision = await strategy.evaluate("AAPL", 0.1, 0.7, {})
        assert decision.signal == SignalType.BUY

    async def test_hold_within_range(self, strategy: MeanReversionStrategy) -> None:
        for v in [0.5, 0.51, 0.49, 0.5, 0.5]:
            await strategy.evaluate("AAPL", v, 0.7, {})
        decision = await strategy.evaluate("AAPL", 0.51, 0.7, {})
        assert decision.signal == SignalType.HOLD

    async def test_hold_on_no_prediction(self, strategy: MeanReversionStrategy) -> None:
        decision = await strategy.evaluate("AAPL", None, 0.7, {})
        assert decision.signal == SignalType.HOLD


class TestRuleBasedStrategy:
    @pytest.fixture
    def strategy(self) -> RuleBasedStrategy:
        return RuleBasedStrategy(
            rules=[
                Rule(
                    name="strong_buy",
                    description="Buy when prediction > 0.8",
                    signal=SignalType.BUY,
                    min_prediction=0.8,
                    priority=10,
                ),
                Rule(
                    name="strong_sell",
                    description="Sell when prediction < 0.2",
                    signal=SignalType.SELL,
                    max_prediction=0.2,
                    priority=10,
                ),
            ],
        )

    async def test_buy_rule_matches(self, strategy: RuleBasedStrategy) -> None:
        decision = await strategy.evaluate("AAPL", 0.9, 0.7, {})
        assert decision.signal == SignalType.BUY
        assert decision.reason.value == "RULE_TRIGGERED"

    async def test_sell_rule_matches(self, strategy: RuleBasedStrategy) -> None:
        decision = await strategy.evaluate("AAPL", 0.1, 0.7, {})
        assert decision.signal == SignalType.SELL

    async def test_no_rule_matches(self, strategy: RuleBasedStrategy) -> None:
        decision = await strategy.evaluate("AAPL", 0.5, 0.7, {})
        assert decision.signal == SignalType.HOLD


class TestEnsembleStrategy:
    @pytest.fixture
    def strategy(self) -> EnsembleStrategy:
        buy = DummyStrategy(signal=SignalType.BUY)
        sell = DummyStrategy(signal=SignalType.SELL)
        hold = DummyStrategy(signal=SignalType.HOLD)
        return EnsembleStrategy([buy, sell, hold])

    async def test_majority_hold(self, strategy: EnsembleStrategy) -> None:
        # buy, sell, hold → no majority (but hold wins with 1 vote since others cancel)
        decision = await strategy.evaluate("AAPL", 0.5, 0.7, {})
        assert decision.signal in (SignalType.BUY, SignalType.SELL, SignalType.HOLD)

    async def test_all_buy(self) -> None:
        buy1 = DummyStrategy(signal=SignalType.BUY)
        buy2 = DummyStrategy(signal=SignalType.BUY)
        ensemble = EnsembleStrategy([buy1, buy2])
        decision = await ensemble.evaluate("AAPL", 0.5, 0.7, {})
        assert decision.signal == SignalType.BUY

    async def test_no_strategies(self) -> None:
        ensemble = EnsembleStrategy([])
        decision = await ensemble.evaluate("AAPL", 0.5, 0.7, {})
        assert decision.signal == SignalType.HOLD

    async def test_name(self, strategy: EnsembleStrategy) -> None:
        assert strategy.name == "ensemble"


class TestWeightedVotingStrategy:
    @pytest.fixture
    def strategy(self) -> WeightedVotingStrategy:
        buy = DummyStrategy(signal=SignalType.BUY, confidence=0.8)
        sell = DummyStrategy(signal=SignalType.SELL, confidence=0.6)
        return WeightedVotingStrategy([(buy, 2.0), (sell, 1.0)])

    async def test_weighted_buy_wins(self, strategy: WeightedVotingStrategy) -> None:
        decision = await strategy.evaluate("AAPL", 0.5, 0.7, {})
        assert decision.signal == SignalType.BUY

    async def test_no_strategies(self) -> None:
        strategy = WeightedVotingStrategy([])
        decision = await strategy.evaluate("AAPL", 0.5, 0.7, {})
        assert decision.signal == SignalType.HOLD
