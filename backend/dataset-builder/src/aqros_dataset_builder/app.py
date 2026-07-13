"""ASGI application for the dataset-builder service.

Wires the shared ``aqros_core`` app factory (config, logging, health) with
this service's business endpoints: DB engine/session lifecycle, the httpx
clients used to reach the Market Data and Feature Store services' REST
APIs, the local Parquet storage adapter, and the dataset/build-run routers.
Mirrors ``aqros_feature_store.app``'s combined-lifespan pattern.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from aqros_core.app import create_app
from aqros_core.health import HealthRegistry
from aqros_dataset_builder.adapters import db
from aqros_dataset_builder.adapters.feature_store_client import HttpFeatureSource
from aqros_dataset_builder.adapters.git_info import SubprocessGitInfoProvider
from aqros_dataset_builder.adapters.market_data_client import HttpMarketDataSource
from aqros_dataset_builder.adapters.parquet_storage import LocalParquetStorage
from aqros_dataset_builder.api.routes import build_runs, datasets
from aqros_dataset_builder.config import Settings

settings = Settings()

engine = db.create_engine(settings)
session_factory = db.create_session_factory(engine)

health_registry = HealthRegistry()
health_registry.register("database", lambda: db.ping(engine))


async def _check_upstream_reachable(http_client: httpx.AsyncClient) -> bool:
    """Readiness check: can we reach an upstream service at all?

    Deliberately lenient (any response, even a 5xx, counts as "reachable" —
    only a connection-level failure counts as unhealthy), mirroring
    feature-store's own market-data reachability check: a momentarily
    degraded upstream should not make *this* service report unready; it
    should only block build requests, which already surface a 502 to the
    caller.
    """
    try:
        await http_client.get("/health/live")
    except httpx.HTTPError:
        return False
    return True


def _build_app() -> FastAPI:
    base_app = create_app(settings, health=health_registry)
    base_lifespan = base_app.router.lifespan_context

    @asynccontextmanager
    async def combined_lifespan(app: FastAPI) -> AsyncIterator[None]:
        market_data_client = httpx.AsyncClient(
            base_url=str(settings.market_data_base_url),
            timeout=settings.upstream_request_timeout_seconds,
        )
        feature_store_client = httpx.AsyncClient(
            base_url=str(settings.feature_store_base_url),
            timeout=settings.upstream_request_timeout_seconds,
        )

        app.state.settings = settings
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.market_data_client = market_data_client
        app.state.feature_store_client = feature_store_client
        app.state.market_data_source = HttpMarketDataSource(market_data_client, settings)
        app.state.feature_source = HttpFeatureSource(feature_store_client, settings)
        app.state.dataset_storage = LocalParquetStorage(settings.dataset_artifact_dir)
        app.state.git_info_provider = SubprocessGitInfoProvider(settings.git_repo_root)

        health_registry.register(
            "market_data_service", lambda: _check_upstream_reachable(market_data_client)
        )
        health_registry.register(
            "feature_store_service", lambda: _check_upstream_reachable(feature_store_client)
        )

        async with base_lifespan(app):
            yield

        await market_data_client.aclose()
        await feature_store_client.aclose()
        await engine.dispose()

    base_app.router.lifespan_context = combined_lifespan
    base_app.include_router(datasets.router)
    base_app.include_router(build_runs.router)
    return base_app


app = _build_app()
