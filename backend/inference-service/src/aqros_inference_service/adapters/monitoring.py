"""LogMetricPublisher and InMemoryAuditRepository for the inference service.

``LogMetricPublisher`` emits structured log lines for observability.
``InMemoryAuditRepository`` stores audit records in memory (dev/test).
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog

from aqros_inference_service.domain.models import PredictionResult
from aqros_inference_service.ports.ports import AuditRepository, MetricPublisher

_logger = structlog.get_logger(__name__)


class LogMetricPublisher(MetricPublisher):
    """Publishes inference metrics to the structured log."""

    async def record_prediction(self, result: PredictionResult) -> None:
        _logger.info(
            "inference.prediction",
            symbol=result.symbol,
            value=result.value,
            confidence=result.confidence.score,
            model_name=result.model_name,
            model_version=result.model_version,
            latency_ms=result.latency_ms,
            status=result.status.value,
        )

    async def record_cache_hit(self) -> None:
        _logger.debug("inference.cache.hit")

    async def record_cache_miss(self) -> None:
        _logger.debug("inference.cache.miss")

    async def record_reload(self) -> None:
        _logger.info("inference.model.reload")

    async def record_validation_failure(self) -> None:
        _logger.warning("inference.validation.failure")


class InMemoryAuditRepository(AuditRepository):
    """Stores audit records in memory (dev/test)."""

    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    async def record_prediction(self, result: PredictionResult, metadata: object) -> None:
        self.records.append(
            {
                "type": "prediction",
                "symbol": result.symbol,
                "value": result.value,
                "confidence": result.confidence.score,
                "model_name": result.model_name,
                "model_version": result.model_version,
                "latency_ms": result.latency_ms,
                "status": result.status.value,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )

    async def record_model_event(
        self,
        event_type: str,
        model_name: str,
        model_version: int,
        detail: str | None = None,
    ) -> None:
        self.records.append(
            {
                "type": "model_event",
                "event_type": event_type,
                "model_name": model_name,
                "model_version": model_version,
                "detail": detail,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )
