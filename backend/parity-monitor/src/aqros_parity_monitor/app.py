"""ASGI application for the parity-monitor service.

Wires the shared ``aqros_core`` app factory with the parity monitor's own
adapters: HTTP client for the Feature Store REST API, Redis for online
feature reads, in-memory report repository, and event bus publisher.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import structlog
from fastapi import FastAPI

from aqros_core.app import create_app
from aqros_core.health import HealthRegistry
from aqros_outbox import DirectOutboxRepository, OutboxMetrics
from aqros_parity_monitor.adapters.event_bus import AqrosEventBusPublisher
from aqros_parity_monitor.adapters.offline_client import (
    HttpOfflineFeatureProvider,
)
from aqros_parity_monitor.adapters.online_client import (
    RedisOnlineFeatureProvider,
)
from aqros_parity_monitor.adapters.report_repository import (
    InMemoryParityReportRepository,
    LogMetricPublisher,
)
from aqros_parity_monitor.api.routes import parity
from aqros_parity_monitor.application.service import ParityService
from aqros_parity_monitor.config import Settings
from aqros_parity_monitor.domain.monitor import ParityMonitor
from aqros_parity_monitor.ports.ports import (
    OfflineFeatureProvider,
    OnlineFeatureProvider,
)

_logger = structlog.get_logger(__name__)

settings = Settings()
health_registry = HealthRegistry()


class _OfflineStub(OfflineFeatureProvider):
    """Fallback when the feature store is unreachable — returns empty data."""

    async def get_snapshot(self, symbol: str, **kwargs: object) -> dict[str, object]:
        return {}

    async def get_feature_value(self, symbol: str, feature_name: str, **kwargs: object) -> None:
        return None

    async def get_feature_version(self, symbol: str, feature_name: str) -> None:
        return None


class _OnlineStub(OnlineFeatureProvider):
    """Fallback when Redis is unavailable — returns empty data."""

    async def get_snapshot(self, symbol: str) -> dict[str, object]:
        return {}

    async def get_feature_value(self, symbol: str, feature_name: str) -> None:
        return None

    async def get_feature_timestamp(self, symbol: str, feature_name: str) -> None:
        return None

    async def health_check(self) -> bool:
        return False


def _build_app() -> FastAPI:
    base_app = create_app(settings, health=health_registry)
    base_lifespan = base_app.router.lifespan_context

    @asynccontextmanager
    async def combined_lifespan(app: FastAPI) -> AsyncIterator[None]:
        # --- HTTP client (Feature Store) ----------------------------------
        http_client = httpx.AsyncClient(
            base_url=str(settings.feature_store_base_url),
            timeout=settings.feature_store_request_timeout_seconds,
        )
        offline_provider: OfflineFeatureProvider = HttpOfflineFeatureProvider(http_client)
        app.state.offline_provider = offline_provider

        # --- Redis (online feature store) ---------------------------------
        from redis import asyncio as aioredis

        online_provider: OnlineFeatureProvider
        redis_client: aioredis.Redis | None = None
        try:
            redis_client = aioredis.from_url(
                str(settings.redis_url),
                max_connections=settings.redis_pool_size,
                socket_connect_timeout=settings.redis_connection_timeout_s,
            )
            await redis_client.ping()
            online_provider = RedisOnlineFeatureProvider(redis_client)
            app.state.online_provider = online_provider
            health_registry.register("redis", lambda: _check_redis(redis_client))
        except Exception:
            _logger.warning(
                "redis_unavailable",
                extra={"redis_url": str(settings.redis_url)},
            )
            online_provider = _OnlineStub()
            app.state.online_provider = online_provider

        # --- Event bus (via transactional outbox) -------------------------
        from aqros_events import InProcessEventBus

        event_bus = InProcessEventBus()
        outbox_metrics = OutboxMetrics()
        outbox_repo = DirectOutboxRepository(event_bus)
        event_publisher = AqrosEventBusPublisher(event_bus)
        app.state.outbox_repository = outbox_repo
        app.state.outbox_metrics = outbox_metrics

        # --- Metrics & repository -----------------------------------------
        report_repository = InMemoryParityReportRepository()
        metric_publisher = LogMetricPublisher()

        # --- Domain monitor & service -------------------------------------
        monitor = ParityMonitor(
            default_tolerance=settings.default_tolerance,
            max_feature_age_seconds=settings.max_feature_age_seconds,
        )
        parity_service = ParityService(
            offline_provider=offline_provider,
            online_provider=online_provider,
            report_repository=report_repository,
            monitor=monitor,
            metric_publisher=metric_publisher,
            event_publisher=event_publisher,
            metrics_enabled=settings.metrics_enabled,
            events_enabled=settings.event_publishing_enabled,
        )
        app.state.parity_service = parity_service

        # --- Health checks ------------------------------------------------
        health_registry.register(
            "feature_store",
            lambda: _check_offline_reachable(http_client),
        )

        async with base_lifespan(app):
            yield

        if redis_client is not None:
            await redis_client.aclose()
        await http_client.aclose()

    base_app.router.lifespan_context = combined_lifespan
    base_app.include_router(parity.router)
    return base_app


async def _check_redis(redis_client: object) -> bool:
    if redis_client is None:
        return False
    try:
        ping = getattr(redis_client, "ping", None)
        if ping is None:
            return False
        result = ping()
        if hasattr(result, "__await__"):
            await result
        return True
    except Exception:
        return False


async def _check_offline_reachable(http_client: httpx.AsyncClient) -> bool:
    try:
        resp = await http_client.get("/health/live")
        return resp.is_success
    except httpx.HTTPError:
        return False


app = _build_app()
