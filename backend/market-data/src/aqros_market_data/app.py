"""ASGI application for the market-data service.

Wires the shared ``aqros_core`` app factory (config, logging, health) with
this service's business endpoints: DB engine/session lifecycle, the
configured market-data provider, and the ingestion/retrieval routers.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from aqros_core.app import create_app
from aqros_core.health import HealthRegistry
from aqros_market_data.adapters import db
from aqros_market_data.adapters.providers import create_provider
from aqros_market_data.api.routes import ingestion, market_data
from aqros_market_data.config import Settings

settings = Settings()

engine = db.create_engine(settings)
session_factory = db.create_session_factory(engine)

health_registry = HealthRegistry()
health_registry.register("database", lambda: db.ping(engine))


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.provider = create_provider(settings)
    yield
    await engine.dispose()


def _build_app() -> FastAPI:
    base_app = create_app(settings, health=health_registry)
    # `create_app` already set a lifespan for cross-cutting startup/shutdown
    # logging; wrap it so both the shared and service-specific lifespans run.
    base_lifespan = base_app.router.lifespan_context

    @asynccontextmanager
    async def combined_lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with base_lifespan(app), _lifespan(app):
            yield

    base_app.router.lifespan_context = combined_lifespan
    base_app.include_router(ingestion.router)
    base_app.include_router(market_data.router)
    return base_app


app = _build_app()
