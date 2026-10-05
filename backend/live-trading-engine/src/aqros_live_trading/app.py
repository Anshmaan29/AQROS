from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from aqros_core.app import create_app
from aqros_core.db import schema_check
from aqros_core.health import HealthRegistry
from aqros_events import InProcessEventBus
from aqros_live_trading.adapters import db
from aqros_live_trading.adapters.broker.alpaca_stub import AlpacaAdapter
from aqros_live_trading.adapters.broker.ibkr_stub import IBKRAdapter
from aqros_live_trading.adapters.broker.paper_broker import PaperBrokerAdapter
from aqros_live_trading.adapters.calendar import TradingCalendar
from aqros_live_trading.adapters.execution import ExecutionEngine
from aqros_live_trading.adapters.kill_switch import KillSwitchManager
from aqros_live_trading.adapters.position_sync import PositionSynchronizer
from aqros_live_trading.adapters.repository import (
    BrokerConnectionRepository,
    LiveOrderRepository,
    PositionSyncRepository,
)
from aqros_live_trading.adapters.routing import OrderRouter
from aqros_live_trading.adapters.session import BrokerSessionManager
from aqros_live_trading.api.routes import trading
from aqros_live_trading.config import Settings
from aqros_live_trading.domain.models import (
    BrokerAdapter,
    ConnectionHealth,
    KillSwitch,
    OrderType,
    ReconnectionPolicy,
    RouteRule,
)
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


def _build_broker() -> BrokerAdapter:
    adapter_name = settings.broker_adapter
    if adapter_name == "paper":
        return PaperBrokerAdapter(
            heartbeat_interval=settings.heartbeat_interval_seconds,
            heartbeat_timeout=settings.heartbeat_timeout_seconds,
        )
    elif adapter_name == "alpaca":
        return AlpacaAdapter(
            api_key=settings.broker_api_key,
            api_secret=settings.broker_api_secret,
            base_url=settings.broker_base_url,
            websocket_url=settings.broker_websocket_url,
            heartbeat_interval=settings.heartbeat_interval_seconds,
            heartbeat_timeout=settings.heartbeat_timeout_seconds,
        )
    elif adapter_name == "ibkr":
        return IBKRAdapter(
            api_key=settings.broker_api_key,
            api_secret=settings.broker_api_secret,
            base_url=settings.broker_base_url,
            heartbeat_interval=settings.heartbeat_interval_seconds,
            heartbeat_timeout=settings.heartbeat_timeout_seconds,
        )
    else:
        raise ValueError(f"Unknown broker adapter: {adapter_name}")


def _build_app() -> FastAPI:
    base_app = create_app(settings, health=health_registry)
    base_lifespan = base_app.router.lifespan_context

    @asynccontextmanager
    async def combined_lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.order_repository = LiveOrderRepository(session_factory)
        app.state.position_repository = PositionSyncRepository(session_factory)
        app.state.broker_connection_repository = BrokerConnectionRepository(session_factory)

        broker = _build_broker()
        app.state.broker = broker

        health = ConnectionHealth(
            heartbeat_interval=settings.heartbeat_interval_seconds,
            heartbeat_timeout=settings.heartbeat_timeout_seconds,
        )
        kill_switch = KillSwitch(
            enabled=settings.kill_switch_enabled,
            auto_trigger_on_disconnect_seconds=settings.kill_switch_auto_trigger_on_disconnect_seconds,
        )
        reconnection_policy = ReconnectionPolicy(
            base_delay=settings.reconnect_base_delay_seconds,
            max_delay=settings.reconnect_max_delay_seconds,
            max_attempts=settings.reconnect_max_attempts,
            jitter=settings.reconnect_jitter,
        )

        session_manager = BrokerSessionManager(broker, health, kill_switch, reconnection_policy)
        execution_engine = ExecutionEngine(broker)
        kill_switch_manager = KillSwitchManager(kill_switch)
        calendar = TradingCalendar(timezone_str=settings.trading_calendar_timezone)
        position_sync = PositionSynchronizer(
            broker,
            sync_interval=settings.position_sync_interval_seconds,
            account_sync_interval=settings.account_sync_interval_seconds,
        )

        order_router = OrderRouter({"default": broker})
        order_router.add_rule(
            RouteRule(
                symbol_pattern="*",
                order_types=(
                    OrderType.MARKET,
                    OrderType.LIMIT,
                    OrderType.STOP,
                    OrderType.STOP_LIMIT,
                ),
                preferred_broker="default",
            )
        )

        app.state.session_manager = session_manager
        app.state.execution_engine = execution_engine
        app.state.kill_switch_manager = kill_switch_manager
        app.state.calendar = calendar
        app.state.position_sync = position_sync
        app.state.order_router = order_router

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

        await session_manager.start()

        async with base_lifespan(app):
            yield

        await session_manager.stop()
        await outbox_dispatcher.stop()
        await engine.dispose()

    base_app.router.lifespan_context = combined_lifespan
    base_app.include_router(trading.router)
    return base_app


app = _build_app()
