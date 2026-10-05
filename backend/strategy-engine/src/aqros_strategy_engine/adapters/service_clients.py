"""Adapter implementations for the Strategy Engine."""

from __future__ import annotations

from typing import Any

import httpx

from aqros_strategy_engine.domain.models import (
    StrategyDecision,
)
from aqros_strategy_engine.ports.ports import (
    FeatureProvider,
    InferenceClient,
    InferenceResult,
    MetricPublisher,
    ModelRegistryClient,
    SignalPublisher,
)


class HttpInferenceClient(InferenceClient):
    """Fetches predictions from the Inference Service REST API."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def predict(
        self,
        symbol: str,
        model_name: str,
        model_version: int | None = None,
        features: dict[str, Any] | None = None,
    ) -> InferenceResult:
        body: dict[str, Any] = {"symbol": symbol, "model_name": model_name}
        if model_version is not None:
            body["model_version"] = model_version
        if features is not None:
            body["features"] = features
        resp = await self._client.post("/v1/predict", json=body)
        resp.raise_for_status()
        data = resp.json()
        return InferenceResult(
            prediction=float(data.get("value", 0.0)),
            confidence=float(data.get("confidence", 0.5)),
        )

    async def health_check(self) -> bool:
        try:
            resp = await self._client.get("/health/inference")
            return resp.is_success
        except httpx.HTTPError:
            return False


class HttpModelRegistryClient(ModelRegistryClient):
    """Fetches champion model version from the Model Registry REST API."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def get_champion_version(self, model_name: str) -> int | None:
        try:
            resp = await self._client.get(f"/v1/models/{model_name}/champion")
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
            return int(data.get("version", 0))
        except httpx.HTTPError:
            return None


class HttpFeatureProvider(FeatureProvider):
    """Fetches features from the Feature Store REST API."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def get_features(self, symbol: str) -> dict[str, Any]:
        try:
            resp = await self._client.get(
                f"/v1/features/{symbol}",
            )
            if resp.status_code == 404:
                return {}
            resp.raise_for_status()
            return dict(resp.json())
        except httpx.HTTPError:
            return {}

    async def health_check(self) -> bool:
        try:
            resp = await self._client.get("/health/live")
            return resp.is_success
        except httpx.HTTPError:
            return False


class AqrosEventBusPublisher(SignalPublisher):
    """Publishes strategy events via the AQROS event bus."""

    def __init__(self, bus: object) -> None:
        self._bus = bus

    async def publish(self, topic: str, payload: bytes) -> None:
        if hasattr(self._bus, "publish"):
            from datetime import UTC, datetime

            from aqros_events import EventEnvelope

            envelope = EventEnvelope(
                topic=topic,
                payload=payload,
                event_time=datetime.now(UTC),
                knowledge_time=datetime.now(UTC),
                producer="strategy-engine",
                schema_version="1.0",
            )
            await self._bus.publish(envelope)


class LogMetricPublisher(MetricPublisher):
    """Logs metrics via structlog — stub for the observability stack."""

    def __init__(self) -> None:
        import structlog

        self._log = structlog.get_logger(__name__)

    async def record_signal(self, decision: StrategyDecision) -> None:
        self._log.info(
            "signal.metric", signal=decision.signal.value, confidence=decision.confidence.score
        )

    async def record_reload(self) -> None:
        self._log.info("strategy.reload.metric")

    async def record_error(self) -> None:
        self._log.warning("strategy.error.metric")
