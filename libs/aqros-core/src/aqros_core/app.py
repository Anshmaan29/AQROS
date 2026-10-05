"""FastAPI application factory.

``create_app`` wires the cross-cutting concerns (config, logging, health) into
a ready-to-serve FastAPI app. Services call this with their own settings and,
in later phases, register routers and health checks. Phase 0 apps expose only
health and a root metadata endpoint.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from aqros_core.config import BaseServiceSettings
from aqros_core.health import HealthRegistry, build_health_router
from aqros_core.logging import configure_logging
from aqros_core.middleware import install_observability


def create_app(
    settings: BaseServiceSettings,
    health: HealthRegistry | None = None,
) -> FastAPI:
    """Create a configured FastAPI application for a service.

    Every service gets the same cross-cutting behaviour for free: structured
    logging, correlation-ID propagation, and a ``/metrics`` endpoint. Services
    only add their own routers and health checks on top.
    """
    logger = configure_logging(settings)
    registry = health if health is not None else HealthRegistry()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger.info("service.starting", port=settings.port, env=settings.environment.value)
        yield
        logger.info("service.stopping")

    app = FastAPI(title=settings.service_name, version=settings.version, lifespan=lifespan)
    app.state.settings = settings
    app.state.logger = logger
    app.state.health = registry
    app.include_router(build_health_router(registry, settings))
    install_observability(app)

    @app.get("/", tags=["meta"])
    async def root() -> dict[str, str]:
        return {
            "service": settings.service_name,
            "version": settings.version,
            "status": "ok",
        }

    return app
