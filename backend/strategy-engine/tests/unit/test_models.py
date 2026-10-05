"""Unit tests for domain models."""

from __future__ import annotations

from aqros_strategy_engine.domain.models import (
    SignalConfidence,
    SignalReason,
    SignalStrength,
    SignalType,
    StrategyDecision,
    StrategyResult,
)


class TestSignalType:
    def test_values(self) -> None:
        assert SignalType.BUY.value == "BUY"
        assert SignalType.SELL.value == "SELL"
        assert SignalType.HOLD.value == "HOLD"
        assert SignalType.EXIT.value == "EXIT"
        assert SignalType.REDUCE.value == "REDUCE"
        assert SignalType.INCREASE.value == "INCREASE"


class TestSignalConfidence:
    def test_default_calibrated(self) -> None:
        conf = SignalConfidence(score=0.5)
        assert conf.score == 0.5
        assert not conf.calibrated

    def test_calibrated(self) -> None:
        conf = SignalConfidence(score=0.8, calibrated=True)
        assert conf.calibrated


class TestStrategyDecision:
    def test_creates_decision(self) -> None:
        decision = StrategyDecision(
            signal=SignalType.BUY,
            confidence=SignalConfidence(score=0.7),
            strength=SignalStrength.STRONG,
            reason=SignalReason.THRESHOLD_BREACH,
            explanation="test",
        )
        assert decision.signal == SignalType.BUY
        assert decision.confidence.score == 0.7

    def test_defaults(self) -> None:
        decision = StrategyDecision(
            signal=SignalType.HOLD,
            confidence=SignalConfidence(score=0.0),
            strength=SignalStrength.WEAK,
            reason=SignalReason.MODEL_PREDICTION,
        )
        assert decision.explanation == ""
        assert decision.prediction_value is None
        assert decision.model_version is None


class TestStrategyResult:
    def test_creates_result(self) -> None:
        decision = StrategyDecision(
            signal=SignalType.BUY,
            confidence=SignalConfidence(score=0.7),
            strength=SignalStrength.STRONG,
            reason=SignalReason.THRESHOLD_BREACH,
        )
        result = StrategyResult(symbol="AAPL", decision=decision, latency_ms=5.0)
        assert result.symbol == "AAPL"
        assert result.decision.signal == SignalType.BUY
        assert result.latency_ms == 5.0
        assert result.errors == []
