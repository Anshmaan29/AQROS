"""RuleBasedStrategy — generates signals from configurable rule sets."""

from __future__ import annotations

from typing import Any

from aqros_strategy_engine.domain.models import (
    SignalConfidence,
    SignalReason,
    SignalStrength,
    SignalType,
    StrategyDecision,
    StrategyMetadata,
)
from aqros_strategy_engine.domain.strategy import Strategy


class Rule:
    """A single evaluation rule with a condition and resulting signal."""

    def __init__(
        self,
        name: str,
        description: str,
        signal: SignalType,
        min_prediction: float | None = None,
        max_prediction: float | None = None,
        min_confidence: float | None = None,
        feature_conditions: dict[str, tuple[float, float]] | None = None,
        priority: int = 0,
    ) -> None:
        self.name = name
        self.description = description
        self.signal = signal
        self.min_prediction = min_prediction
        self.max_prediction = max_prediction
        self.min_confidence = min_confidence
        self.feature_conditions = feature_conditions or {}
        self.priority = priority

    def matches(
        self,
        prediction: float | None,
        confidence: float | None,
        features: dict[str, Any],
    ) -> bool:
        if self.min_prediction is not None and (
            prediction is None or prediction < self.min_prediction
        ):
            return False
        if self.max_prediction is not None and (
            prediction is None or prediction > self.max_prediction
        ):
            return False
        if self.min_confidence is not None and (
            confidence is None or confidence < self.min_confidence
        ):
            return False
        for name, (lo, hi) in self.feature_conditions.items():
            val = features.get(name)
            if val is None or not isinstance(val, (int, float)):
                return False
            if val < lo or val > hi:
                return False
        return True


class RuleBasedStrategy(Strategy):
    """Strategy driven by a configurable list of ordered rules."""

    def __init__(self, rules: list[Rule], version: str = "0.1.0") -> None:
        self._rules = sorted(rules, key=lambda r: r.priority, reverse=True)
        self._version = version

    @property
    def name(self) -> str:
        return "rule_based"

    @property
    def version(self) -> str:
        return self._version

    async def evaluate(
        self,
        symbol: str,
        prediction: float | None,
        prediction_confidence: float | None,
        features: dict[str, Any],
        model_version: int | None = None,
    ) -> StrategyDecision:
        for rule in self._rules:
            if rule.matches(prediction, prediction_confidence, features):
                strength = SignalStrength.STRONG if rule.priority > 5 else SignalStrength.MODERATE
                return StrategyDecision(
                    signal=rule.signal,
                    confidence=SignalConfidence(score=prediction_confidence or 0.5),
                    strength=strength,
                    reason=SignalReason.RULE_TRIGGERED,
                    explanation=f"Rule '{rule.name}' triggered: {rule.description}",
                    prediction_value=prediction,
                    model_version=model_version,
                    strategy_version=self._version,
                )

        return StrategyDecision(
            signal=SignalType.HOLD,
            confidence=SignalConfidence(score=prediction_confidence or 0.3),
            strength=SignalStrength.WEAK,
            reason=SignalReason.MODEL_PREDICTION,
            explanation="No rules matched, defaulting to HOLD",
            prediction_value=prediction,
            model_version=model_version,
            strategy_version=self._version,
        )

    def metadata(self) -> StrategyMetadata:
        return StrategyMetadata(
            name="rule_based",
            version=self._version,
            description=f"Rule-based strategy with {len(self._rules)} rules",
            tags=["rule_based", "deterministic"],
        )
