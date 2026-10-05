"""ASGI application for the backtesting-engine service.

Built on ``aqros_core.create_app`` so it gets the same health semantics,
structured logging, correlation IDs and ``/metrics`` as every other service. It
previously constructed a bare ``FastAPI`` with hand-rolled ``/health`` routes:
that reported healthy against an unmigrated database and gave compose nothing
reliable to gate on.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from aqros_backtesting_engine.adapters.calendar_provider import DefaultCalendarProvider
from aqros_backtesting_engine.adapters.db import create_engine, create_session_factory, ping
from aqros_backtesting_engine.adapters.feature_store_client import (
    HttpFeatureStoreClient,
)
from aqros_backtesting_engine.adapters.market_data_client import HttpMarketDataClient
from aqros_backtesting_engine.adapters.model_registry_client import (
    HttpModelRegistryClient,
)
from aqros_backtesting_engine.api.routes.backtests import router as backtests_router
from aqros_backtesting_engine.config import Settings
from aqros_core.app import create_app
from aqros_core.db import schema_check
from aqros_core.health import HealthRegistry

settings = Settings()


def _build_http_client(base_url: str, timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(timeout))


def _build_app() -> FastAPI:
    health_registry = HealthRegistry()
    base_app = create_app(settings, health=health_registry)
    base_lifespan = base_app.router.lifespan_context

    @asynccontextmanager
    async def combined_lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        session_factory = create_session_factory(engine)
        # Registered after the engine exists, so both checks are live once the
        # lifespan has run. `schema` matters here: a reachable database with no
        # tables would otherwise report ready and then fail every backtest.
        health_registry.register("database", lambda: ping(engine))
        health_registry.register("schema", schema_check(engine))

        market_data_client = _build_http_client(
            str(settings.market_data_base_url).rstrip("/"),
            settings.upstream_request_timeout_seconds,
        )
        model_registry_client = _build_http_client(
            str(settings.model_registry_base_url).rstrip("/"),
            settings.upstream_request_timeout_seconds,
        )
        feature_store_http_client = _build_http_client(
            str(settings.feature_store_base_url).rstrip("/"),
            settings.upstream_request_timeout_seconds,
        )

        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.market_data_client = HttpMarketDataClient(market_data_client)
        app.state.model_registry_client = HttpModelRegistryClient(model_registry_client)
        app.state.feature_store_client = HttpFeatureStoreClient(feature_store_http_client)
        app.state.calendar_provider = DefaultCalendarProvider()

        async with base_lifespan(app):
            yield

        await market_data_client.aclose()
        await model_registry_client.aclose()
        await feature_store_http_client.aclose()
        await engine.dispose()

    base_app.router.lifespan_context = combined_lifespan
    base_app.include_router(backtests_router)
    return base_app


app = _build_app()
