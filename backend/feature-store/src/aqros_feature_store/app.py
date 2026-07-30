"""ASGI application for the feature-store service.

Wires the shared ``aqros_core`` app factory (config, logging, health) with
this service's business endpoints: DB engine/session lifecycle, the httpx
client used to reach the Market Data Service's REST API, the Redis client
for online feature serving, and the feature/pipeline routers. Mirrors
``aqros_market_data.app``'s combined-lifespan pattern.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import structlog
from fastapi import FastAPI
from redis import asyncio as aioredis

from aqros_core.app import create_app
from aqros_core.health import HealthRegistry
from aqros_feature_store.adapters import db
from aqros_feature_store.adapters.db import session_scope
from aqros_feature_store.adapters.market_data_client import HttpMarketDataSource
from aqros_feature_store.adapters.online_redis import RedisOnlineFeatureStore
from aqros_feature_store.adapters.repository import SqlAlchemyFeatureDefinitionRepository
from aqros_feature_store.api.routes import features, pipeline
from aqros_feature_store.config import Settings
from aqros_feature_store.domain.feature_definitions import FEATURE_REGISTRY

_logger = structlog.get_logger(__name__)

settings = Settings()

engine = db.create_engine(settings)
session_factory = db.create_session_factory(engine)

health_registry = HealthRegistry()
health_registry.register("database", lambda: db.ping(engine))


async def _redis_health(redis_client: aioredis.Redis | None) -> bool:
    """Readiness check: is Redis reachable?"""
    if redis_client is None:
        return False
    try:
        await redis_client.ping()
        return True
    except Exception:
        return False


async def _check_market_data_reachable(http_client: httpx.AsyncClient) -> bool:
    """Readiness check: can we reach the Market Data Service at all?

    Deliberately lenient (any response, even a 5xx, counts as "reachable" —
    only a connection-level failure counts as unhealthy) because a
    momentarily-degraded upstream should not make *this* service report
    unready; it should only block pipeline runs, which already surface a 502
    to the caller (see ``api/routes/pipeline.py``).
    """
    try:
        await http_client.get("/health/live")
    except httpx.HTTPError:
        return False
    return True


async def _seed_feature_definitions() -> None:
    """Idempotently register every catalog definition on startup.

    Ensures ``GET /v1/definitions`` is populated even before any computation
    has run — the catalog is metadata that should be discoverable
    independent of whether data has been computed yet.
    """
    async with session_scope(session_factory) as session:
        repository = SqlAlchemyFeatureDefinitionRepository(session)
        for registration in FEATURE_REGISTRY:
            await repository.upsert_definition(registration.definition)


def _build_app() -> FastAPI:
    base_app = create_app(settings, health=health_registry)
    base_lifespan = base_app.router.lifespan_context

    @asynccontextmanager
    async def combined_lifespan(app: FastAPI) -> AsyncIterator[None]:
        http_client = httpx.AsyncClient(
            base_url=str(settings.market_data_base_url),
            timeout=settings.market_data_request_timeout_seconds,
        )
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.http_client = http_client
        app.state.market_data_source = HttpMarketDataSource(http_client, settings)
        health_registry.register(
            "market_data_service", lambda: _check_market_data_reachable(http_client)
        )

        # --- Redis (online feature store) ---------------------------------
        redis_client: aioredis.Redis | None = None
        try:
            redis_client = aioredis.from_url(
                str(settings.redis_url),
                max_connections=settings.redis_pool_size,
                socket_connect_timeout=settings.redis_connection_timeout_s,
            )
            await redis_client.ping()
            app.state.online_feature_store = RedisOnlineFeatureStore(redis_client)
            health_registry.register("redis", lambda: _redis_health(redis_client))
        except Exception:
            app.state.online_feature_store = None
            _logger.warning(
                "redis_unavailable",
                extra={"redis_url": str(settings.redis_url)},
            )

        # --- Seed feature definitions -------------------------------------
        await _seed_feature_definitions()

        async with base_lifespan(app):
            yield

        if redis_client is not None:
            await redis_client.aclose()
        await http_client.aclose()
        await engine.dispose()

    base_app.router.lifespan_context = combined_lifespan
    base_app.include_router(features.router)
    base_app.include_router(pipeline.router)
    return base_app


app = _build_app()
