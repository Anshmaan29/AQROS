"""Concrete strategy implementations."""

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


class DummyStrategy(Strategy):
    """Predictable dummy strategy for testing — always returns a configurable signal."""

    def __init__(
        self,
        signal: SignalType = SignalType.HOLD,
        confidence: float = 0.5,
        version: str = "0.1.0",
    ) -> None:
        self._signal = signal
        self._confidence = confidence
        self._version = version

    @property
    def name(self) -> str:
        return "dummy"

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
        return StrategyDecision(
            signal=self._signal,
            confidence=SignalConfidence(score=self._confidence),
            strength=SignalStrength.MODERATE,
            reason=SignalReason.MODEL_PREDICTION,
            explanation="Dummy strategy: fixed signal output",
            prediction_value=prediction,
            model_version=model_version,
            strategy_version=self._version,
        )

    def metadata(self) -> StrategyMetadata:
        return StrategyMetadata(
            name="dummy",
            version=self._version,
            description="Predictable dummy strategy for testing",
            model_family="dummy",
            tags=["test", "dummy"],
        )


class ThresholdStrategy(Strategy):
    """Generates signals when prediction crosses configurable thresholds."""

    def __init__(
        self,
        buy_threshold: float = 0.6,
        sell_threshold: float = 0.4,
        version: str = "0.1.0",
    ) -> None:
        self._buy_threshold = buy_threshold
        self._sell_threshold = sell_threshold
        self._version = version

    @property
    def name(self) -> str:
        return "threshold"

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
        if prediction is None:
            return StrategyDecision(
                signal=SignalType.HOLD,
                confidence=SignalConfidence(score=0.0),
                strength=SignalStrength.WEAK,
                reason=SignalReason.CONFIDENCE_TOO_LOW,
                explanation="No prediction available, defaulting to HOLD",
                model_version=model_version,
                strategy_version=self._version,
            )

        conf = prediction_confidence or 0.5
        if conf < 0.3:
            return StrategyDecision(
                signal=SignalType.HOLD,
                confidence=SignalConfidence(score=conf),
                strength=SignalStrength.WEAK,
                reason=SignalReason.CONFIDENCE_TOO_LOW,
                explanation=f"Confidence {conf:.3f} below minimum threshold 0.3",
                prediction_value=prediction,
                model_version=model_version,
                strategy_version=self._version,
            )

        if prediction >= self._buy_threshold:
            strength = SignalStrength.STRONG if prediction >= 0.8 else SignalStrength.MODERATE
            return StrategyDecision(
                signal=SignalType.BUY,
                confidence=SignalConfidence(score=conf),
                strength=strength,
                reason=SignalReason.THRESHOLD_BREACH,
                explanation=f"Prediction {prediction:.4f} >= buy threshold {self._buy_threshold}",
                prediction_value=prediction,
                model_version=model_version,
                strategy_version=self._version,
            )

        if prediction <= self._sell_threshold:
            strength = SignalStrength.STRONG if prediction <= 0.2 else SignalStrength.MODERATE
            return StrategyDecision(
                signal=SignalType.SELL,
                confidence=SignalConfidence(score=conf),
                strength=strength,
                reason=SignalReason.THRESHOLD_BREACH,
                explanation=f"Prediction {prediction:.4f} <= sell threshold {self._sell_threshold}",
                prediction_value=prediction,
                model_version=model_version,
                strategy_version=self._version,
            )

        return StrategyDecision(
            signal=SignalType.HOLD,
            confidence=SignalConfidence(score=conf),
            strength=SignalStrength.WEAK,
            reason=SignalReason.MODEL_PREDICTION,
            explanation=f"Prediction {prediction:.4f} between thresholds, holding",
            prediction_value=prediction,
            model_version=model_version,
            strategy_version=self._version,
        )

    def metadata(self) -> StrategyMetadata:
        return StrategyMetadata(
            name="threshold",
            version=self._version,
            description="Threshold-based strategy: BUY when prediction >= buy_threshold, SELL when <= sell_threshold",
            model_family="regression",
            tags=["threshold", "baseline"],
        )


class MomentumStrategy(Strategy):
    """Generates signals based on prediction momentum (change from previous)."""

    def __init__(self, lookback: int = 5, version: str = "0.1.0") -> None:
        self._lookback = lookback
        self._version = version
        self._previous: dict[str, list[float]] = {}

    @property
    def name(self) -> str:
        return "momentum"

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
        if prediction is None:
            return StrategyDecision(
                signal=SignalType.HOLD,
                confidence=SignalConfidence(score=0.0),
                strength=SignalStrength.WEAK,
                reason=SignalReason.CONFIDENCE_TOO_LOW,
                explanation="No prediction available",
                model_version=model_version,
                strategy_version=self._version,
            )

        symbol = symbol.upper()
        if symbol not in self._previous:
            self._previous[symbol] = []
        self._previous[symbol].append(prediction)
        if len(self._previous[symbol]) > self._lookback:
            self._previous[symbol] = self._previous[symbol][-self._lookback :]

        history = self._previous[symbol]
        conf = prediction_confidence or 0.5

        if len(history) < 2:
            return StrategyDecision(
                signal=SignalType.HOLD,
                confidence=SignalConfidence(score=conf * 0.5),
                strength=SignalStrength.WEAK,
                reason=SignalReason.MOMENTUM_SIGNAL,
                explanation=f"Insufficient history ({len(history)}), need 2+",
                prediction_value=prediction,
                model_version=model_version,
                strategy_version=self._version,
            )

        change = history[-1] - history[-2]
        abs_change = abs(change)

        if abs_change < 0.01:
            return StrategyDecision(
                signal=SignalType.HOLD,
                confidence=SignalConfidence(score=conf),
                strength=SignalStrength.WEAK,
                reason=SignalReason.MOMENTUM_SIGNAL,
                explanation=f"Momentum {change:.4f} negligible, holding",
                prediction_value=prediction,
                model_version=model_version,
                strategy_version=self._version,
            )

        if change > 0:
            strength = SignalStrength.STRONG if change > 0.1 else SignalStrength.MODERATE
            return StrategyDecision(
                signal=SignalType.BUY,
                confidence=SignalConfidence(score=conf * min(1.0, abs_change / 0.2)),
                strength=strength,
                reason=SignalReason.MOMENTUM_SIGNAL,
                explanation=f"Positive momentum {change:.4f}, suggesting BUY",
                prediction_value=prediction,
                model_version=model_version,
                strategy_version=self._version,
            )

        strength = SignalStrength.STRONG if change < -0.1 else SignalStrength.MODERATE
        return StrategyDecision(
            signal=SignalType.SELL,
            confidence=SignalConfidence(score=conf * min(1.0, abs_change / 0.2)),
            strength=strength,
            reason=SignalReason.MOMENTUM_SIGNAL,
            explanation=f"Negative momentum {change:.4f}, suggesting SELL",
            prediction_value=prediction,
            model_version=model_version,
            strategy_version=self._version,
        )

    def metadata(self) -> StrategyMetadata:
        return StrategyMetadata(
            name="momentum",
            version=self._version,
            description=f"Momentum strategy: BUY/SELL based on prediction direction change (lookback={self._lookback})",
            model_family="regression",
            tags=["momentum", "trend"],
        )


class MeanReversionStrategy(Strategy):
    """Generates BUY when prediction is below mean, SELL when above (reversion)."""

    def __init__(
        self, window: int = 10, std_multiplier: float = 1.5, version: str = "0.1.0"
    ) -> None:
        self._window = window
        self._std_mult = std_multiplier
        self._version = version
        self._history: dict[str, list[float]] = {}

    @property
    def name(self) -> str:
        return "mean_reversion"

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
        if prediction is None:
            return StrategyDecision(
                signal=SignalType.HOLD,
                confidence=SignalConfidence(score=0.0),
                strength=SignalStrength.WEAK,
                reason=SignalReason.CONFIDENCE_TOO_LOW,
                explanation="No prediction available",
                model_version=model_version,
                strategy_version=self._version,
            )

        symbol = symbol.upper()
        if symbol not in self._history:
            self._history[symbol] = []
        self._history[symbol].append(prediction)
        if len(self._history[symbol]) > self._window:
            self._history[symbol] = self._history[symbol][-self._window :]

        history = self._history[symbol]
        conf = prediction_confidence or 0.5

        if len(history) < 3:
            return StrategyDecision(
                signal=SignalType.HOLD,
                confidence=SignalConfidence(score=conf * 0.5),
                strength=SignalStrength.WEAK,
                reason=SignalReason.MEAN_REVERSION,
                explanation=f"Insufficient history ({len(history)}), need 3+",
                prediction_value=prediction,
                model_version=model_version,
                strategy_version=self._version,
            )

        mean = sum(history) / len(history)
        variance = sum((x - mean) ** 2 for x in history) / len(history)
        std = variance**0.5
        deviation = prediction - mean

        if std == 0:
            return StrategyDecision(
                signal=SignalType.HOLD,
                confidence=SignalConfidence(score=conf),
                strength=SignalStrength.WEAK,
                reason=SignalReason.MEAN_REVERSION,
                explanation="Zero standard deviation in history, holding",
                prediction_value=prediction,
                model_version=model_version,
                strategy_version=self._version,
            )

        z_score = deviation / std

        if z_score > self._std_mult:
            return StrategyDecision(
                signal=SignalType.SELL,
                confidence=SignalConfidence(score=conf * min(1.0, z_score / (self._std_mult * 2))),
                strength=(
                    SignalStrength.STRONG
                    if z_score > self._std_mult * 2
                    else SignalStrength.MODERATE
                ),
                reason=SignalReason.MEAN_REVERSION,
                explanation=f"Z-score {z_score:.3f} > {self._std_mult}, expecting reversion down → SELL",
                prediction_value=prediction,
                model_version=model_version,
                strategy_version=self._version,
            )

        if z_score < -self._std_mult:
            return StrategyDecision(
                signal=SignalType.BUY,
                confidence=SignalConfidence(
                    score=conf * min(1.0, abs(z_score) / (self._std_mult * 2))
                ),
                strength=(
                    SignalStrength.STRONG
                    if z_score < -self._std_mult * 2
                    else SignalStrength.MODERATE
                ),
                reason=SignalReason.MEAN_REVERSION,
                explanation=f"Z-score {z_score:.3f} < -{self._std_mult}, expecting reversion up → BUY",
                prediction_value=prediction,
                model_version=model_version,
                strategy_version=self._version,
            )

        return StrategyDecision(
            signal=SignalType.HOLD,
            confidence=SignalConfidence(score=conf),
            strength=SignalStrength.WEAK,
            reason=SignalReason.MEAN_REVERSION,
            explanation=f"Z-score {z_score:.3f} within ±{self._std_mult}, holding",
            prediction_value=prediction,
            model_version=model_version,
            strategy_version=self._version,
        )

    def metadata(self) -> StrategyMetadata:
        return StrategyMetadata(
            name="mean_reversion",
            version=self._version,
            description=f"Mean-reversion strategy: BUY on low outliers, SELL on high outliers (window={self._window}, std_mult={self._std_mult})",
            model_family="regression",
            tags=["mean_reversion", "reversion"],
        )
