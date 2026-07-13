"""ASGI application for the training-pipeline service.

Wires the shared ``aqros_core`` app factory (config, logging, health) with
this service's endpoints: DB engine/session lifecycle, the single httpx
client used to reach the Dataset Builder's REST API, the local artifact
store, the git-info provider, and the training-run/trained-model routers.
Mirrors ``aqros_dataset_builder.app``'s combined-lifespan pattern.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from aqros_core.app import create_app
from aqros_core.health import HealthRegistry
from aqros_training_pipeline.adapters import db
from aqros_training_pipeline.adapters.dataset_builder_client import HttpDatasetBuilderClient
from aqros_training_pipeline.adapters.git_info import SubprocessGitInfoProvider
from aqros_training_pipeline.adapters.local_artifact_store import LocalArtifactStore
from aqros_training_pipeline.api.routes import trained_models, training_runs
from aqros_training_pipeline.config import Settings

settings = Settings()

engine = db.create_engine(settings)
session_factory = db.create_session_factory(engine)

health_registry = HealthRegistry()
health_registry.register("database", lambda: db.ping(engine))


async def _check_upstream_reachable(http_client: httpx.AsyncClient) -> bool:
    """Readiness check: can we reach the Dataset Builder at all?

    Deliberately lenient (any response, even a 5xx, counts as "reachable";
    only a connection-level failure counts as unhealthy) — a momentarily
    degraded upstream should not make this service report unready, it should
    only block training requests (which surface a 502 to the caller).
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
        dataset_builder_http = httpx.AsyncClient(
            base_url=str(settings.dataset_builder_base_url),
            timeout=settings.upstream_request_timeout_seconds,
        )

        app.state.settings = settings
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.dataset_builder_http = dataset_builder_http
        app.state.dataset_builder_client = HttpDatasetBuilderClient(dataset_builder_http)
        app.state.artifact_store = LocalArtifactStore(settings.artifact_dir)
        app.state.git_info_provider = SubprocessGitInfoProvider(settings.git_repo_root)

        health_registry.register(
            "dataset_builder_service",
            lambda: _check_upstream_reachable(dataset_builder_http),
        )

        async with base_lifespan(app):
            yield

        await dataset_builder_http.aclose()
        await engine.dispose()

    base_app.router.lifespan_context = combined_lifespan
    base_app.include_router(training_runs.router)
    base_app.include_router(trained_models.router)
    return base_app


app = _build_app()
