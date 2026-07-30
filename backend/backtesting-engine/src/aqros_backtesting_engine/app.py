"""FastAPI application for the backtesting-engine service."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

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


def _build_http_client(base_url: str, timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(timeout))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)

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

    yield

    await market_data_client.aclose()
    await model_registry_client.aclose()
    await feature_store_http_client.aclose()
    await engine.dispose()


app = FastAPI(
    title="AQROS Backtesting Engine",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(backtests_router)


@app.get("/health")
@app.get("/health/live")
async def health_live() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.get("/health/ready")
async def health_ready() -> JSONResponse:
    engine = getattr(app.state, "engine", None)
    if engine is None or not await ping(engine):
        return JSONResponse({"status": "unhealthy"}, status_code=503)
    return JSONResponse({"status": "ok"})
