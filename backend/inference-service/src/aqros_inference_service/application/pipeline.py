"""PredictionPipeline — orchestrates the inference flow end to end.

Pipeline steps:
1. Receive request
2. Resolve model (load/cache)
3. Fetch online features
4. Validate features
5. Run inference
6. Estimate confidence
7. Generate explanation
8. Audit log
9. Publish events
10. Return response
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from aqros_inference_service.adapters.backends import InferenceBackend
from aqros_inference_service.application.events import (
    PredictionAudited,
    PredictionCompleted,
    PredictionFailed,
    PredictionRequested,
    PredictionStarted,
)
from aqros_inference_service.domain.model_manager import ModelManager
from aqros_inference_service.domain.models import (
    PredictionConfidence,
    PredictionError,
    PredictionExplanation,
    PredictionRequest,
    PredictionResult,
    PredictionStatus,
)
from aqros_inference_service.domain.validation import FeatureValidator
from aqros_inference_service.ports.ports import (
    AuditRepository,
    Clock,
    FeatureProvider,
    MetricPublisher,
    ModelLoader,
    PredictionPublisher,
)

TOPIC_PREDICTION_REQUESTED = "inference.prediction.requested"
TOPIC_PREDICTION_STARTED = "inference.prediction.started"
TOPIC_PREDICTION_COMPLETED = "inference.prediction.completed"
TOPIC_PREDICTION_FAILED = "inference.prediction.failed"
TOPIC_MODEL_LOADED = "inference.model.loaded"
TOPIC_MODEL_RELOADED = "inference.model.reloaded"
TOPIC_PREDICTION_AUDITED = "inference.prediction.audited"


class PredictionPipeline:
    """Orchestrates the full prediction flow.

    All external dependencies are injected via ports. The pipeline is
    stateless (state lives in ``ModelManager``).
    """

    def __init__(
        self,
        model_manager: ModelManager,
        feature_provider: FeatureProvider,
        backend: InferenceBackend,
        validator: FeatureValidator,
        model_loader: ModelLoader,
        prediction_publisher: PredictionPublisher | None = None,
        metric_publisher: MetricPublisher | None = None,
        audit_repository: AuditRepository | None = None,
        clock: Clock | None = None,
        events_enabled: bool = True,
        metrics_enabled: bool = True,
        audit_enabled: bool = True,
    ) -> None:
        self._model_manager = model_manager
        self._feature_provider = feature_provider
        self._backend = backend
        self._validator = validator
        self._model_loader = model_loader
        self._publisher = prediction_publisher
        self._metrics = metric_publisher
        self._audit = audit_repository
        self._clock = clock or _RealClock()
        self._events_enabled = events_enabled
        self._metrics_enabled = metrics_enabled
        self._audit_enabled = audit_enabled
        self._stats: dict[str, float] = {
            "total_requests": 0.0,
            "total_success": 0.0,
            "total_failures": 0.0,
            "total_latency_ms": 0.0,
        }

    @property
    def statistics(self) -> dict[str, float]:
        return dict(self._stats)

    async def predict(
        self,
        symbol: str,
        model_name: str,
        *,
        model_version: int | None = None,
        features: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> PredictionResult:
        """Run the full prediction pipeline for a single symbol."""
        request_id = str(uuid.uuid4())
        start_s = time.monotonic()

        self._stats["total_requests"] += 1

        await self._emit_event(
            TOPIC_PREDICTION_REQUESTED,
            PredictionRequested(
                symbol=symbol,
                model_name=model_name,
                model_version=model_version or 0,
                request_id=request_id,
                correlation_id=correlation_id,
            ),
        )

        # --- Resolve model ------------------------------------------------
        try:
            loaded = await self._model_manager.get_model(model_name, model_version)
        except ValueError as exc:
            return await self._fail(
                symbol=symbol,
                model_name=model_name,
                model_version=model_version or 0,
                request_id=request_id,
                correlation_id=correlation_id,
                error=PredictionError(
                    code="MODEL_NOT_FOUND",
                    message=str(exc),
                ),
                start_s=start_s,
            )

        # --- Fetch online features if not provided ------------------------
        if features is None:
            features = await self._feature_provider.get_features(symbol)
            if self._metrics_enabled and self._metrics is not None:
                await self._metrics.record_cache_miss()

        # --- Validate features --------------------------------------------
        validation_errors = self._validator.validate_all(symbol, features)
        if validation_errors:
            if self._metrics_enabled and self._metrics is not None:
                await self._metrics.record_validation_failure()
            return await self._fail(
                symbol=symbol,
                model_name=model_name,
                model_version=loaded.version,
                request_id=request_id,
                correlation_id=correlation_id,
                error=PredictionError(
                    code="FEATURE_VALIDATION_FAILED",
                    message="; ".join(e.message for e in validation_errors),
                ),
                start_s=start_s,
            )

        await self._emit_event(
            TOPIC_PREDICTION_STARTED,
            PredictionStarted(
                symbol=symbol,
                model_name=model_name,
                model_version=loaded.version,
                request_id=request_id,
                correlation_id=correlation_id,
                feature_count=len(features),
            ),
        )

        # --- Run inference ------------------------------------------------
        try:
            value = await self._backend.predict(loaded.backend, features)
        except Exception as exc:
            return await self._fail(
                symbol=symbol,
                model_name=model_name,
                model_version=loaded.version,
                request_id=request_id,
                correlation_id=correlation_id,
                error=PredictionError(
                    code="INFERENCE_ERROR",
                    message=str(exc),
                ),
                start_s=start_s,
            )

        # --- Confidence & explanation -------------------------------------
        confidence_score = await self._backend.estimate_confidence(loaded.backend, value, features)
        confidence = PredictionConfidence(score=confidence_score)
        explanation_data = await self._backend.explain(loaded.backend, value, features)
        explanation = PredictionExplanation(
            feature_importance=explanation_data,
            method="feature_importance",
        )

        elapsed_ms = (time.monotonic() - start_s) * 1000

        result = PredictionResult(
            symbol=symbol.upper(),
            value=value,
            confidence=confidence,
            explanation=explanation,
            model_name=model_name,
            model_version=loaded.version,
            feature_version=loaded.metadata.get("feature_version", 0),
            latency_ms=elapsed_ms,
            status=PredictionStatus.SUCCESS,
        )

        # --- Audit & events & metrics -------------------------------------
        await self._audit_prediction(result, request_id, correlation_id)
        await self._emit_event(
            TOPIC_PREDICTION_COMPLETED,
            PredictionCompleted(
                symbol=symbol,
                model_name=model_name,
                model_version=loaded.version,
                request_id=request_id,
                correlation_id=correlation_id,
                value=value,
                confidence=confidence_score,
                latency_ms=elapsed_ms,
            ),
        )
        if self._metrics_enabled and self._metrics is not None:
            await self._metrics.record_prediction(result)

        self._stats["total_success"] += 1
        self._stats["total_latency_ms"] += elapsed_ms

        return result

    async def predict_batch(
        self,
        requests: list[PredictionRequest],
    ) -> list[PredictionResult]:
        """Run predictions for multiple symbols.

        Currently sequential; in production this would use ``asyncio.gather``
        with a semaphore for concurrency control.
        """
        results: list[PredictionResult] = []
        for req in requests:
            result = await self.predict(
                symbol=req.symbol,
                model_name=req.model_name,
                model_version=req.model_version,
                features=req.features,
                correlation_id=req.correlation_id,
            )
            results.append(result)
        return results

    @property
    def model_manager(self) -> ModelManager:
        return self._model_manager

    @property
    def feature_provider(self) -> FeatureProvider:
        return self._feature_provider

    async def check_feature_store_health(self) -> bool:
        return await self._feature_provider.health_check()

    async def reload_model(self, model_name: str, version: int | None = None) -> bool:
        """Force-reload a model (hot-reload)."""
        if version is not None:
            await self._model_manager.reload_version(model_name, version)
        else:
            result = await self._model_manager.reload_production(model_name)
            if result is None:
                return False
        if self._metrics_enabled and self._metrics is not None:
            await self._metrics.record_reload()
        return True

    async def rollback_model(self, model_name: str) -> bool:
        """Roll back to the previous production version."""
        result = await self._model_manager.rollback(model_name)
        if self._metrics_enabled and self._metrics is not None:
            await self._metrics.record_reload()
        return result is not None

    async def _fail(
        self,
        symbol: str,
        model_name: str,
        model_version: int,
        request_id: str,
        correlation_id: str | None,
        error: PredictionError,
        start_s: float,
    ) -> PredictionResult:
        elapsed_ms = (time.monotonic() - start_s) * 1000
        result = PredictionResult(
            symbol=symbol.upper(),
            value=0.0,
            confidence=PredictionConfidence(score=0.0),
            model_name=model_name,
            model_version=model_version,
            latency_ms=elapsed_ms,
            status=PredictionStatus.FAILED,
            error=error,
        )
        self._stats["total_failures"] += 1
        if self._metrics_enabled and self._metrics is not None:
            await self._metrics.record_prediction(result)
        await self._emit_event(
            TOPIC_PREDICTION_FAILED,
            PredictionFailed(
                symbol=symbol,
                model_name=model_name,
                model_version=model_version,
                request_id=request_id,
                correlation_id=correlation_id,
                error_code=error.code,
                error_message=error.message,
            ),
        )
        return result

    async def _audit_prediction(
        self,
        result: PredictionResult,
        request_id: str,
        correlation_id: str | None,
    ) -> None:
        if not self._audit_enabled or self._audit is None:
            return
        metadata = {
            "request_id": request_id,
            "correlation_id": correlation_id,
            "timestamp": self._clock.now().isoformat(),
        }
        await self._audit.record_prediction(result, metadata)
        await self._emit_event(
            TOPIC_PREDICTION_AUDITED,
            PredictionAudited(
                request_id=request_id,
                symbol=result.symbol,
                model_name=result.model_name,
                model_version=result.model_version,
                value=result.value,
                confidence=result.confidence.score,
                latency_ms=result.latency_ms,
                timestamp=str(self._clock.now()),
            ),
        )

    async def _emit_event(self, topic: str, payload: BaseModel) -> None:
        if not self._events_enabled or self._publisher is None:
            return
        raw = json.dumps(payload.model_dump(), default=str).encode("utf-8")
        await self._publisher.publish(topic, raw)


class _RealClock(Clock):
    def now(self) -> datetime:
        return datetime.now(UTC)
