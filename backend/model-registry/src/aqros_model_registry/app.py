"""ASGI application for the model-registry service.

Wires the shared ``aqros_core`` app factory (config, logging, health) with
this service's endpoints: DB engine/session lifecycle, the single httpx
client used to reach the Training Pipeline's REST API (its sole upstream
dependency), the local artifact store, the artifact signer, and the
models/artifacts/transitions/history routers. Mirrors
``aqros_training_pipeline.app``'s combined-lifespan pattern.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI

from aqros_core.app import create_app
from aqros_core.health import HealthRegistry
from aqros_model_registry.adapters import db
from aqros_model_registry.adapters.local_artifact_store import LocalArtifactStore
from aqros_model_registry.adapters.signer import CosignArtifactVerifier
from aqros_model_registry.adapters.training_pipeline_client import HttpTrainingPipelineClient
from aqros_model_registry.api.routes import artifacts, history, models, transitions
from aqros_model_registry.config import Settings

settings = Settings()

engine = db.create_engine(settings)
session_factory = db.create_session_factory(engine)

health_registry = HealthRegistry()
health_registry.register("database", lambda: db.ping(engine))


def _check_artifact_store_writable_sync(base_dir: str) -> bool:
    """Synchronous reachable/writable check for the artifact store's base directory.

    Ensures ``base_dir`` exists and that a small marker file can be written to
    and removed from it, proving the mounted artifact volume is both reachable
    and writable — without touching any real model artifact.
    """
    path = Path(base_dir)
    path.mkdir(parents=True, exist_ok=True)
    marker = path / f".health-{uuid.uuid4().hex}"
    marker.write_bytes(b"ok")
    try:
        return marker.read_bytes() == b"ok"
    finally:
        marker.unlink(missing_ok=True)


async def _check_artifact_store_healthy(base_dir: str) -> bool:
    """Readiness check: is the artifact store's base directory reachable and writable?"""
    try:
        return await asyncio.to_thread(_check_artifact_store_writable_sync, base_dir)
    except OSError:
        return False


health_registry.register(
    "artifact_store",
    lambda: _check_artifact_store_healthy(settings.artifact_dir),
)


def _build_artifact_signer() -> CosignArtifactVerifier:
    """Construct the ``ArtifactSigner`` from settings.

    Passes ``public_key=None`` (and no ``verify_hook``) when artifact signing
    is not configured, so the verifier is a tolerant no-op (Requirement 21.3).
    """
    public_key = (
        settings.artifact_signing_public_key_path if settings.artifact_signing_enabled else None
    )
    return CosignArtifactVerifier(public_key=public_key)


def _build_app() -> FastAPI:
    base_app = create_app(settings, health=health_registry)
    base_lifespan = base_app.router.lifespan_context

    @asynccontextmanager
    async def combined_lifespan(app: FastAPI) -> AsyncIterator[None]:
        training_pipeline_http = httpx.AsyncClient(
            base_url=str(settings.training_pipeline_base_url),
            timeout=settings.upstream_request_timeout_seconds,
        )

        app.state.settings = settings
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.training_pipeline_http = training_pipeline_http
        app.state.training_pipeline_client = HttpTrainingPipelineClient(training_pipeline_http)
        app.state.artifact_store = LocalArtifactStore(settings.artifact_dir)
        app.state.artifact_signer = _build_artifact_signer()

        async with base_lifespan(app):
            yield

        await training_pipeline_http.aclose()
        await engine.dispose()

    base_app.router.lifespan_context = combined_lifespan
    base_app.include_router(models.router)
    base_app.include_router(artifacts.router)
    base_app.include_router(transitions.router)
    base_app.include_router(history.router)
    return base_app


app = _build_app()
