from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from aqros_core.app import create_app
from aqros_core.db import schema_check
from aqros_core.health import HealthRegistry
from aqros_events import InProcessEventBus
from aqros_oms.adapters import db
from aqros_oms.adapters.repository import OrderRepository
from aqros_oms.api.routes import orders
from aqros_oms.config import Settings
from aqros_outbox import OutboxConfig, OutboxDispatcher, OutboxMetrics, SqlAlchemyOutboxRepository

_logger = structlog.get_logger(__name__)

settings = Settings()

engine = db.create_engine(settings)
session_factory = db.create_session_factory(engine)

health_registry = HealthRegistry()
health_registry.register("database", lambda: db.ping(engine))


# Connectivity alone is not readiness: an unmigrated database answers
# SELECT 1 happily and then 500s on every real request.
health_registry.register("schema", schema_check(engine))


def _build_app() -> FastAPI:
    base_app = create_app(settings, health=health_registry)
    base_lifespan = base_app.router.lifespan_context

    @asynccontextmanager
    async def combined_lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.order_repository = OrderRepository(session_factory)

        outbox_repo = SqlAlchemyOutboxRepository(session_factory)
        event_bus = InProcessEventBus()
        outbox_config = OutboxConfig(
            poll_interval_seconds=settings.outbox_poll_interval_seconds,
            batch_size=settings.outbox_batch_size,
            max_retries=settings.outbox_max_retries,
            retention_hours=settings.outbox_retention_hours,
        )
        outbox_metrics = OutboxMetrics()
        outbox_dispatcher = OutboxDispatcher(outbox_repo, event_bus, outbox_config, outbox_metrics)
        await outbox_dispatcher.start()
        app.state.outbox_repository = outbox_repo
        app.state.outbox_dispatcher = outbox_dispatcher
        app.state.outbox_metrics = outbox_metrics

        async with base_lifespan(app):
            yield

        await outbox_dispatcher.stop()
        await engine.dispose()

    base_app.router.lifespan_context = combined_lifespan
    base_app.include_router(orders.router)
    return base_app


app = _build_app()
