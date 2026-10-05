"""Ensemble and weighted voting strategies — combine multiple decisions."""

from __future__ import annotations

from collections import Counter
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


class WeightedVotingStrategy(Strategy):
    """Combines multiple sub-strategies via weighted voting."""

    def __init__(
        self,
        strategies: list[tuple[Strategy, float]],
        name: str = "weighted_voting",
        version: str = "0.1.0",
    ) -> None:
        self._strategies = strategies
        self._name = name
        self._version = version

    @property
    def name(self) -> str:
        return self._name

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
        if not self._strategies:
            return StrategyDecision(
                signal=SignalType.HOLD,
                confidence=SignalConfidence(score=0.0),
                strength=SignalStrength.WEAK,
                reason=SignalReason.STRATEGY_ERROR,
                explanation="No sub-strategies registered",
            )

        votes: dict[SignalType, float] = {}
        total_weight = 0.0
        total_confidence = 0.0
        explanations: list[str] = []

        for strategy, weight in self._strategies:
            try:
                decision = await strategy.evaluate(
                    symbol, prediction, prediction_confidence, features, model_version
                )
                votes[decision.signal] = votes.get(decision.signal, 0.0) + weight
                total_weight += weight
                total_confidence += decision.confidence.score * weight
                explanations.append(f"{strategy.name}: {decision.signal.value}")
            except Exception as exc:
                explanations.append(f"{strategy.name}: error ({exc})")

        if total_weight == 0 or not votes:
            return StrategyDecision(
                signal=SignalType.HOLD,
                confidence=SignalConfidence(score=0.0),
                strength=SignalStrength.WEAK,
                reason=SignalReason.STRATEGY_ERROR,
                explanation="; ".join(explanations),
                model_version=model_version,
                strategy_version=self._version,
            )

        winner = max(votes, key=lambda k: votes[k])
        confidence = SignalConfidence(
            score=total_confidence / total_weight,
        )
        return StrategyDecision(
            signal=winner,
            confidence=confidence,
            strength=(
                SignalStrength.STRONG
                if votes[winner] / total_weight > 0.6
                else SignalStrength.MODERATE
            ),
            reason=SignalReason.WEIGHTED_VOTE,
            explanation="; ".join(explanations),
            prediction_value=prediction,
            model_version=model_version,
            strategy_version=self._version,
        )

    def metadata(self) -> StrategyMetadata:
        return StrategyMetadata(
            name=self._name,
            version=self._version,
            description=f"Weighted voting strategy with {len(self._strategies)} sub-strategies",
            tags=["ensemble", "voting"],
        )


class EnsembleStrategy(Strategy):
    """Combines decisions via majority vote (each strategy gets one vote)."""

    def __init__(
        self,
        strategies: list[Strategy],
        name: str = "ensemble",
        version: str = "0.1.0",
    ) -> None:
        self._strategies = strategies
        self._name = name
        self._version = version

    @property
    def name(self) -> str:
        return self._name

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
        if not self._strategies:
            return StrategyDecision(
                signal=SignalType.HOLD,
                confidence=SignalConfidence(score=0.0),
                strength=SignalStrength.WEAK,
                reason=SignalReason.STRATEGY_ERROR,
                explanation="No sub-strategies registered",
            )

        decisions: list[StrategyDecision] = []
        for strategy in self._strategies:
            try:
                decision = await strategy.evaluate(
                    symbol, prediction, prediction_confidence, features, model_version
                )
                decisions.append(decision)
            except Exception:
                pass

        if not decisions:
            return StrategyDecision(
                signal=SignalType.HOLD,
                confidence=SignalConfidence(score=0.0),
                strength=SignalStrength.WEAK,
                reason=SignalReason.STRATEGY_ERROR,
                explanation="All sub-strategies failed",
                model_version=model_version,
                strategy_version=self._version,
            )

        vote_counts = Counter(d.signal for d in decisions)
        winner = vote_counts.most_common(1)[0][0]
        consensus_ratio = vote_counts[winner] / len(decisions)
        avg_confidence = sum(d.confidence.score for d in decisions) / len(decisions)

        return StrategyDecision(
            signal=winner,
            confidence=SignalConfidence(score=avg_confidence),
            strength=SignalStrength.STRONG if consensus_ratio > 0.6 else SignalStrength.MODERATE,
            reason=SignalReason.ENSEMBLE_CONSENSUS,
            explanation=f"Majority vote: {winner.value} ({vote_counts[winner]}/{len(decisions)} votes, consensus {consensus_ratio:.0%})",
            prediction_value=prediction,
            model_version=model_version,
            strategy_version=self._version,
        )

    def metadata(self) -> StrategyMetadata:
        return StrategyMetadata(
            name=self._name,
            version=self._version,
            description=f"Ensemble strategy: majority vote across {len(self._strategies)} sub-strategies",
            tags=["ensemble", "majority_vote"],
        )
