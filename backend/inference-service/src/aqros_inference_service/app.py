"""ASGI application for the inference service.

Wires the shared ``aqros_core`` app factory with model management, feature
fetching, and the prediction pipeline.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import structlog
from fastapi import FastAPI

from aqros_core.app import create_app
from aqros_core.health import HealthRegistry
from aqros_inference_service.adapters.dummy_backend import DummyModelBackend
from aqros_inference_service.adapters.event_bus import (
    AqrosEventBusPublisher,
)
from aqros_inference_service.adapters.feature_client import (
    HttpFeatureProvider,
)
from aqros_inference_service.adapters.model_loader import HttpModelLoader
from aqros_inference_service.adapters.monitoring import (
    InMemoryAuditRepository,
    LogMetricPublisher,
)
from aqros_inference_service.api.routes import inference
from aqros_inference_service.application.pipeline import PredictionPipeline
from aqros_inference_service.config import Settings
from aqros_inference_service.domain.model_manager import ModelManager
from aqros_inference_service.domain.validation import FeatureValidator
from aqros_inference_service.ports.ports import (
    FeatureProvider,
    ModelLoader,
)
from aqros_outbox import DirectOutboxRepository, OutboxMetrics

_logger = structlog.get_logger(__name__)

settings = Settings()
health_registry = HealthRegistry()


def _select_backend() -> DummyModelBackend:
    backend_name = settings.inference_backend.lower()
    if backend_name == "dummy":
        return DummyModelBackend()
    _logger.warning("unknown_backend", backend=backend_name, fallback="dummy")
    return DummyModelBackend()


def _build_app() -> FastAPI:
    base_app = create_app(settings, health=health_registry)
    base_lifespan = base_app.router.lifespan_context

    backend = _select_backend()
    validator = FeatureValidator()

    @asynccontextmanager
    async def combined_lifespan(app: FastAPI) -> AsyncIterator[None]:
        # --- HTTP clients -------------------------------------------------
        registry_client = httpx.AsyncClient(
            base_url=str(settings.model_registry_base_url),
            timeout=settings.model_registry_request_timeout_seconds,
        )
        feature_client = httpx.AsyncClient(
            base_url=str(settings.feature_store_base_url),
            timeout=settings.feature_store_request_timeout_seconds,
        )

        # --- Model loader & manager ---------------------------------------
        model_loader: ModelLoader = HttpModelLoader(registry_client, backend)
        model_manager = ModelManager(
            loader=model_loader,
            max_loaded_models=settings.max_loaded_models,
            cache_ttl_seconds=settings.model_cache_ttl_seconds,
        )
        app.state.model_manager = model_manager

        # --- Feature provider ---------------------------------------------
        feature_provider: FeatureProvider = HttpFeatureProvider(feature_client)

        # --- Event bus (via transactional outbox) -------------------------
        from aqros_events import InProcessEventBus

        event_bus = InProcessEventBus()
        outbox_metrics = OutboxMetrics()
        outbox_repo = DirectOutboxRepository(event_bus)
        publisher = AqrosEventBusPublisher(event_bus)
        app.state.outbox_repository = outbox_repo
        app.state.outbox_metrics = outbox_metrics

        # --- Metrics & audit ----------------------------------------------
        metric_publisher = LogMetricPublisher()
        audit_repository = InMemoryAuditRepository()

        # --- Prediction pipeline ------------------------------------------
        pipeline = PredictionPipeline(
            model_manager=model_manager,
            feature_provider=feature_provider,
            backend=backend,
            validator=validator,
            model_loader=model_loader,
            prediction_publisher=publisher,
            metric_publisher=metric_publisher,
            audit_repository=audit_repository,
            events_enabled=settings.event_publishing_enabled,
            metrics_enabled=settings.metrics_enabled,
            audit_enabled=settings.audit_enabled,
        )
        app.state.pipeline = pipeline

        # --- Health checks ------------------------------------------------
        health_registry.register(
            "feature_store",
            lambda: _check_feature_store(feature_client),
        )

        # --- Hot-reload polling task --------------------------------------
        polling_task = asyncio.create_task(_hot_reload_loop(pipeline, model_manager, model_loader))

        async with base_lifespan(app):
            yield

        polling_task.cancel()
        await registry_client.aclose()
        await feature_client.aclose()

    base_app.router.lifespan_context = combined_lifespan
    base_app.include_router(inference.router)
    return base_app


async def _check_feature_store(client: httpx.AsyncClient) -> bool:
    try:
        resp = await client.get("/health/live")
        return resp.is_success
    except httpx.HTTPError:
        return False


async def _hot_reload_loop(
    pipeline: PredictionPipeline,
    model_manager: ModelManager,
    model_loader: ModelLoader,
) -> None:
    """Periodically poll the model registry for new production versions."""
    while True:
        await asyncio.sleep(settings.hot_reload_poll_interval_seconds)
        for model_name in list(model_manager.production_versions):
            try:
                latest = await model_loader.get_latest_version(model_name)
                current = model_manager.production_versions.get(model_name)
                if latest is not None and current is not None and latest > current:
                    _logger.info(
                        "hot_reload.detected",
                        model_name=model_name,
                        current_version=current,
                        latest_version=latest,
                    )
                    await pipeline.reload_model(model_name, latest)
            except Exception as exc:
                _logger.warning(
                    "hot_reload.error",
                    model_name=model_name,
                    error=str(exc),
                )


app = _build_app()
