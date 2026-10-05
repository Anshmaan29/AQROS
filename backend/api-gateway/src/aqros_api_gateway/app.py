"""ASGI application for the api-gateway service.

The gateway is the single north-south ingress: it discovers the platform
topology, aggregates health, and reverse-proxies to the public services. It
deliberately exposes only ``Exposure.PUBLIC`` routes — the money path stays
internal (see ``domain/topology.py``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from aqros_api_gateway.adapters.proxy import GatewayProxy
from aqros_api_gateway.api.routes import gateway, health
from aqros_api_gateway.config import Settings
from aqros_core.app import create_app
from aqros_core.health import HealthRegistry
from aqros_core.http import ResilientClient, RetryPolicy, create_async_client

_logger = structlog.get_logger(__name__)

settings = Settings()


def _build_app() -> FastAPI:
    # The gateway has no database of its own, so readiness is self-contained.
    health_registry = HealthRegistry()

    base_app = create_app(settings, health=health_registry)
    base_lifespan = base_app.router.lifespan_context

    @asynccontextmanager
    async def combined_lifespan(app: FastAPI) -> AsyncIterator[None]:
        # One shared client across all upstreams so connection pooling and the
        # per-upstream circuit breakers are reused rather than rebuilt per call.
        http_client = ResilientClient(
            create_async_client(
                timeout_seconds=settings.proxy_timeout_seconds,
                connect_timeout_seconds=settings.proxy_connect_timeout_seconds,
                max_connections=settings.proxy_max_connections,
            ),
            policy=RetryPolicy(max_attempts=3),
        )
        proxy = GatewayProxy(http_client)

        app.state.http_client = http_client
        app.state.proxy = proxy
        app.state.probe_timeout_seconds = settings.probe_timeout_seconds
        app.state.auth_base_url = settings.auth_base_url

        async with base_lifespan(app):
            yield

        await proxy.aclose()
        _logger.info("api_gateway.shutdown")

    base_app.router.lifespan_context = combined_lifespan
    # Order matters: the proxy route is a catch-all (`/v1/{service}/{path:path}`)
    # and FastAPI matches in registration order. Registering health *after* it
    # meant `/v1/health/platform` was swallowed by the proxy and answered 404
    # `unknown_service`.
    base_app.include_router(health.router)
    base_app.include_router(gateway.router)
    return base_app


app = _build_app()
