from __future__ import annotations

from typing import cast

from fastapi import Request

from aqros_live_trading.adapters.execution import ExecutionEngine
from aqros_live_trading.adapters.kill_switch import KillSwitchManager
from aqros_live_trading.adapters.repository import LiveOrderRepository
from aqros_live_trading.adapters.routing import OrderRouter
from aqros_live_trading.adapters.session import BrokerSessionManager


def get_order_repository(request: Request) -> LiveOrderRepository:
    return cast(LiveOrderRepository, request.app.state.order_repository)


def get_execution_engine(request: Request) -> ExecutionEngine:
    return cast(ExecutionEngine, request.app.state.execution_engine)


def get_order_router(request: Request) -> OrderRouter:
    return cast(OrderRouter, request.app.state.order_router)


def get_session_manager(request: Request) -> BrokerSessionManager:
    return cast(BrokerSessionManager, request.app.state.session_manager)


def get_kill_switch_manager(request: Request) -> KillSwitchManager:
    return cast(KillSwitchManager, request.app.state.kill_switch_manager)
