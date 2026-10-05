from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from aqros_core.app import create_app
from aqros_core.health import HealthRegistry
from aqros_events import InProcessEventBus
from aqros_outbox import OutboxConfig, OutboxDispatcher, OutboxMetrics, SqlAlchemyOutboxRepository
from aqros_paper_trading.adapters import db
from aqros_paper_trading.api.routes import trading
from aqros_paper_trading.config import Settings
from aqros_paper_trading.domain.models import (
    CommissionConfig,
    LatencyConfig,
    MatchingEngine,
    SimulatedExchange,
    SlippageConfig,
)

_logger = structlog.get_logger(__name__)

settings = Settings()

engine = db.create_engine(settings)
session_factory = db.create_session_factory(engine)

health_registry = HealthRegistry()
health_registry.register("database", lambda: db.ping(engine))


def _build_exchange() -> SimulatedExchange:
    slippage = SlippageConfig(
        model=settings.slippage_model,
        linear_slippage_pct=settings.slippage_linear_pct,
        sqrt_slippage_pct=settings.slippage_sqrt_pct,
        base_slippage_pct=settings.slippage_base_pct,
        min_slippage_pct=settings.slippage_min_pct,
        max_slippage_pct=settings.slippage_max_pct,
    )
    commission = CommissionConfig(
        model=settings.commission_model,
        per_share_rate=settings.commission_per_share_rate,
        pct_rate=settings.commission_pct_rate,
        min_commission=settings.commission_min,
        max_commission=settings.commission_max,
    )
    latency = LatencyConfig(
        base_delay_ms=settings.latency_base_delay_ms,
        jitter_ms=settings.latency_jitter_ms,
        min_delay_ms=settings.latency_min_delay_ms,
    )
    matching_engine = MatchingEngine(
        slippage_config=slippage,
        commission_config=commission,
    )
    return SimulatedExchange(
        matching_engine=matching_engine,
        latency_config=latency,
    )


def _build_app() -> FastAPI:
    base_app = create_app(settings, health=health_registry)
    base_lifespan = base_app.router.lifespan_context

    @asynccontextmanager
    async def combined_lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.exchange = _build_exchange()

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
    base_app.include_router(trading.router)
    return base_app


app = _build_app()
