"""Strategy evaluation pipeline — orchestrates prediction→signal generation."""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime

from pydantic import BaseModel

from aqros_strategy_engine.application.events import (
    SignalGenerated,
    SignalRejected,
    StrategyEvaluated,
    StrategyFailed,
    StrategyReloaded,
)
from aqros_strategy_engine.domain.models import (
    SignalConfidence,
    SignalReason,
    SignalStrength,
    SignalType,
    StrategyDecision,
    StrategyEvaluationRequest,
    StrategyResult,
)
from aqros_strategy_engine.domain.strategy_manager import StrategyManager
from aqros_strategy_engine.ports.ports import (
    Clock,
    FeatureProvider,
    InferenceClient,
    MetricPublisher,
    ModelRegistryClient,
    SignalPublisher,
)

TOPIC_SIGNAL_GENERATED = "signals.generated"
TOPIC_SIGNAL_REJECTED = "signals.rejected"
TOPIC_STRATEGY_EVALUATED = "strategy.evaluated"
TOPIC_STRATEGY_LOADED = "strategy.loaded"
TOPIC_STRATEGY_RELOADED = "strategy.reloaded"
TOPIC_STRATEGY_FAILED = "strategy.failed"


class StrategyPipeline:
    """Orchestrates the strategy evaluation flow.

    Pipeline: Prediction → Feature Snapshot → Strategy Evaluation →
    Risk Pre-check → Signal Generation → Event Publication → API Response
    """

    def __init__(
        self,
        strategy_manager: StrategyManager,
        feature_provider: FeatureProvider | None = None,
        inference_client: InferenceClient | None = None,
        model_registry_client: ModelRegistryClient | None = None,
        metric_publisher: MetricPublisher | None = None,
        signal_publisher: SignalPublisher | None = None,
        clock: Clock | None = None,
        risk_precheck_enabled: bool = True,
        events_enabled: bool = True,
        metrics_enabled: bool = True,
    ) -> None:
        self._strategy_manager = strategy_manager
        self._feature_provider = feature_provider
        self._inference_client = inference_client
        self._model_registry_client = model_registry_client
        self._metric_publisher = metric_publisher
        self._signal_publisher = signal_publisher
        self._clock = clock or _RealClock()
        self._risk_precheck_enabled = risk_precheck_enabled
        self._events_enabled = events_enabled
        self._metrics_enabled = metrics_enabled
        self._stats: dict[str, float] = {
            "total_evaluations": 0.0,
            "total_signals": 0.0,
            "total_rejected": 0.0,
            "total_errors": 0.0,
            "total_latency_ms": 0.0,
            "buy_count": 0.0,
            "sell_count": 0.0,
            "hold_count": 0.0,
            "exit_count": 0.0,
            "reduce_count": 0.0,
            "increase_count": 0.0,
        }

    @property
    def statistics(self) -> dict[str, float]:
        return dict(self._stats)

    @property
    def strategy_manager(self) -> StrategyManager:
        return self._strategy_manager

    @property
    def feature_provider(self) -> FeatureProvider | None:
        return self._feature_provider

    async def evaluate(
        self,
        request: StrategyEvaluationRequest,
    ) -> StrategyResult:
        start_s = time.monotonic()
        self._stats["total_evaluations"] += 1
        request_id = str(uuid.uuid4())

        # --- Step 1: Resolve strategy -----------------------------------------
        strategy_name = request.strategy_name or "default"
        try:
            strategy = self._strategy_manager.get_strategy(strategy_name)
        except ValueError as exc:
            self._stats["total_errors"] += 1
            return StrategyResult(
                symbol=request.symbol,
                decision=StrategyDecision(
                    signal=SignalType.HOLD,
                    confidence=SignalConfidence(score=0.0),
                    strength=SignalStrength.WEAK,
                    reason=SignalReason.STRATEGY_ERROR,
                    explanation=str(exc),
                ),
                latency_ms=(time.monotonic() - start_s) * 1000,
                errors=[str(exc)],
            )

        await self._emit_event(
            TOPIC_STRATEGY_EVALUATED,
            StrategyEvaluated(
                symbol=request.symbol,
                strategy_name=strategy_name,
                request_id=request_id,
                correlation_id=request.correlation_id,
            ),
        )

        # --- Step 2: Fetch features if not provided ---------------------------
        features = request.features
        if features is None and self._feature_provider is not None:
            features = await self._feature_provider.get_features(request.symbol)

        # --- Step 3: Get prediction if not provided ---------------------------
        prediction = request.prediction
        prediction_confidence = request.prediction_confidence
        if prediction is None and self._inference_client is not None:
            try:
                result = await self._inference_client.predict(
                    symbol=request.symbol,
                    model_name=request.model_name,
                    model_version=request.model_version,
                    features=features,
                )
                prediction = result.prediction
                prediction_confidence = result.confidence
            except Exception as exc:
                return await self._fail(
                    request.symbol,
                    strategy_name,
                    request_id,
                    request.correlation_id,
                    start_s,
                    "INFERENCE_ERROR",
                    str(exc),
                )

        # --- Step 4: Strategy evaluation --------------------------------------
        try:
            decision = await strategy.evaluate(
                symbol=request.symbol,
                prediction=prediction,
                prediction_confidence=prediction_confidence,
                features=features or {},
                model_version=request.model_version,
            )
        except Exception as exc:
            return await self._fail(
                request.symbol,
                strategy_name,
                request_id,
                request.correlation_id,
                start_s,
                "STRATEGY_ERROR",
                str(exc),
            )

        # --- Step 5: Risk pre-check -------------------------------------------
        if (
            self._risk_precheck_enabled
            and decision.signal in (SignalType.BUY, SignalType.SELL, SignalType.EXIT)
            and decision.confidence.score < 0.2
        ):
            self._stats["total_rejected"] += 1
            await self._emit_event(
                TOPIC_SIGNAL_REJECTED,
                SignalRejected(
                    symbol=request.symbol,
                    strategy_name=strategy_name,
                    signal=decision.signal.value,
                    reason="Confidence below risk threshold (0.2)",
                    request_id=request_id,
                    correlation_id=request.correlation_id,
                ),
            )
            elapsed_ms = (time.monotonic() - start_s) * 1000
            return StrategyResult(
                symbol=request.symbol,
                decision=StrategyDecision(
                    signal=SignalType.HOLD,
                    confidence=decision.confidence,
                    strength=SignalStrength.WEAK,
                    reason=SignalReason.RISK_REJECTED,
                    explanation=f"Risk pre-check rejected {decision.signal.value}: confidence {decision.confidence.score:.3f} < 0.2",
                    prediction_value=decision.prediction_value,
                    model_version=request.model_version,
                    strategy_version=strategy.version,
                ),
                latency_ms=elapsed_ms,
            )

        # --- Step 6: Count signal type ----------------------------------------
        sig = decision.signal
        if sig == SignalType.BUY:
            self._stats["buy_count"] += 1
        elif sig == SignalType.SELL:
            self._stats["sell_count"] += 1
        elif sig == SignalType.HOLD:
            self._stats["hold_count"] += 1
        elif sig == SignalType.EXIT:
            self._stats["exit_count"] += 1
        elif sig == SignalType.REDUCE:
            self._stats["reduce_count"] += 1
        elif sig == SignalType.INCREASE:
            self._stats["increase_count"] += 1

        # --- Step 7: Publish event --------------------------------------------
        self._stats["total_signals"] += 1
        elapsed_ms = (time.monotonic() - start_s) * 1000
        self._stats["total_latency_ms"] += elapsed_ms

        await self._emit_event(
            TOPIC_SIGNAL_GENERATED,
            SignalGenerated(
                symbol=request.symbol,
                strategy_name=strategy_name,
                strategy_version=strategy.version,
                signal=decision.signal.value,
                confidence=decision.confidence.score,
                strength=decision.strength.value,
                reason=decision.reason.value,
                prediction=prediction or 0.0,
                model_version=request.model_version or 0,
                request_id=request_id,
                correlation_id=request.correlation_id,
                latency_ms=elapsed_ms,
            ),
        )

        if self._metrics_enabled and self._metric_publisher is not None:
            await self._metric_publisher.record_signal(decision)

        return StrategyResult(
            symbol=request.symbol,
            decision=decision,
            latency_ms=elapsed_ms,
        )

    async def evaluate_batch(
        self,
        requests: list[StrategyEvaluationRequest],
    ) -> list[StrategyResult]:
        results: list[StrategyResult] = []
        for req in requests:
            result = await self.evaluate(req)
            results.append(result)
        return results

    async def reload_strategy(self, name: str, version: str | None = None) -> bool:
        ok = self._strategy_manager.reload_strategy(name, version)
        if ok:
            self._stats["total_signals"] = self._stats.get("total_signals", 0)
            await self._emit_event(
                TOPIC_STRATEGY_RELOADED,
                StrategyReloaded(
                    strategy_name=name,
                    strategy_version=version or "",
                ),
            )
            if self._metrics_enabled and self._metric_publisher is not None:
                await self._metric_publisher.record_reload()
        return ok

    async def _fail(
        self,
        symbol: str,
        strategy_name: str,
        request_id: str,
        correlation_id: str | None,
        start_s: float,
        error_code: str,
        error_message: str,
    ) -> StrategyResult:
        elapsed_ms = (time.monotonic() - start_s) * 1000
        self._stats["total_errors"] += 1
        await self._emit_event(
            TOPIC_STRATEGY_FAILED,
            StrategyFailed(
                symbol=symbol,
                strategy_name=strategy_name,
                error_code=error_code,
                error_message=error_message,
                request_id=request_id,
                correlation_id=correlation_id,
            ),
        )
        return StrategyResult(
            symbol=symbol,
            decision=StrategyDecision(
                signal=SignalType.HOLD,
                confidence=SignalConfidence(score=0.0),
                strength=SignalStrength.WEAK,
                reason=SignalReason.STRATEGY_ERROR,
                explanation=f"{error_code}: {error_message}",
            ),
            latency_ms=elapsed_ms,
            errors=[f"{error_code}: {error_message}"],
        )

    async def _emit_event(self, topic: str, payload: BaseModel) -> None:
        if not self._events_enabled or self._signal_publisher is None:
            return
        raw = json.dumps(payload.model_dump(), default=str).encode("utf-8")
        await self._signal_publisher.publish(topic, raw)


class _RealClock(Clock):
    def now(self) -> datetime:
        return datetime.now(UTC)
