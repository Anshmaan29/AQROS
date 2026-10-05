"""ASGI application for the Strategy Engine."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import structlog
from fastapi import FastAPI

from aqros_core.app import create_app
from aqros_core.health import HealthRegistry
from aqros_events import InProcessEventBus
from aqros_outbox import DirectOutboxRepository, OutboxMetrics
from aqros_strategy_engine.adapters.service_clients import (
    HttpFeatureProvider,
    HttpInferenceClient,
    HttpModelRegistryClient,
    LogMetricPublisher,
)
from aqros_strategy_engine.api.routes import strategy
from aqros_strategy_engine.application.pipeline import StrategyPipeline
from aqros_strategy_engine.config import Settings
from aqros_strategy_engine.domain.strategies.implementations import (
    DummyStrategy,
    MeanReversionStrategy,
    MomentumStrategy,
    ThresholdStrategy,
)
from aqros_strategy_engine.domain.strategies.rule_based import RuleBasedStrategy
from aqros_strategy_engine.domain.strategy_manager import StrategyManager
from aqros_strategy_engine.ports.ports import (
    InferenceClient,
    MetricPublisher,
)

_logger = structlog.get_logger(__name__)

settings = Settings()
health_registry = HealthRegistry()


def _register_default_strategies(mgr: StrategyManager) -> None:
    mgr.register(DummyStrategy())
    mgr.register(ThresholdStrategy())
    mgr.register(MomentumStrategy())
    mgr.register(MeanReversionStrategy())
    mgr.register(RuleBasedStrategy(rules=[]))


def _build_app() -> FastAPI:
    base_app = create_app(settings, health=health_registry)
    base_lifespan = base_app.router.lifespan_context

    @asynccontextmanager
    async def combined_lifespan(app: FastAPI) -> AsyncIterator[None]:
        inference_client = httpx.AsyncClient(
            base_url=str(settings.inference_service_base_url),
            timeout=settings.inference_request_timeout_seconds,
        )
        feature_client = httpx.AsyncClient(
            base_url=str(settings.feature_store_base_url),
            timeout=settings.feature_store_request_timeout_seconds,
        )
        registry_client = httpx.AsyncClient(
            base_url=str(settings.model_registry_base_url),
            timeout=settings.model_registry_request_timeout_seconds,
        )

        inf_adapter: InferenceClient = HttpInferenceClient(inference_client)
        feat_provider = HttpFeatureProvider(feature_client)
        registry_resolver = HttpModelRegistryClient(registry_client)
        metric_publisher: MetricPublisher = LogMetricPublisher()

        # --- Transactional outbox (direct, no DB) -------------------------
        event_bus = InProcessEventBus()
        outbox_metrics = OutboxMetrics()
        outbox_repo = DirectOutboxRepository(event_bus)
        app.state.outbox_repository = outbox_repo
        app.state.outbox_metrics = outbox_metrics

        strategy_manager = StrategyManager()
        _register_default_strategies(strategy_manager)
        app.state.strategy_manager = strategy_manager

        pipeline = StrategyPipeline(
            strategy_manager=strategy_manager,
            feature_provider=feat_provider,
            inference_client=inf_adapter,
            model_registry_client=registry_resolver,
            metric_publisher=metric_publisher,
            risk_precheck_enabled=settings.risk_precheck_enabled,
            events_enabled=settings.event_publishing_enabled,
            metrics_enabled=settings.metrics_enabled,
        )
        app.state.pipeline = pipeline

        health_registry.register(
            "feature_store",
            lambda: _check_health(feature_client, "/health/live"),
        )
        health_registry.register(
            "inference_service",
            lambda: _check_health(inference_client, "/health/inference"),
        )

        async with base_lifespan(app):
            yield

        await inference_client.aclose()
        await feature_client.aclose()
        await registry_client.aclose()

    base_app.router.lifespan_context = combined_lifespan
    base_app.include_router(strategy.router)
    return base_app


async def _check_health(client: httpx.AsyncClient, path: str) -> bool:
    try:
        resp = await client.get(path)
        return resp.is_success
    except httpx.HTTPError:
        return False


app = _build_app()
