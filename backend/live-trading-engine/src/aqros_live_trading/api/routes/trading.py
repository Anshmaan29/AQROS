from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException

from aqros_live_trading.adapters.calendar import TradingCalendar
from aqros_live_trading.adapters.execution import ExecutionEngine
from aqros_live_trading.adapters.kill_switch import KillSwitchManager
from aqros_live_trading.adapters.repository import LiveOrderRepository
from aqros_live_trading.adapters.routing import OrderRouter
from aqros_live_trading.adapters.session import BrokerSessionManager
from aqros_live_trading.api.deps import (
    get_execution_engine,
    get_kill_switch_manager,
    get_order_repository,
    get_order_router,
    get_session_manager,
)
from aqros_live_trading.api.schemas import (
    AccountResponse,
    BrokerInfoResponse,
    CancelRequest,
    ConnectionHealthResponse,
    HealthResponse,
    KillSwitchResponse,
    LiveOrderResponse,
    OrderCreateRequest,
    OrderListResponse,
    PositionResponse,
    TradingSessionResponse,
)
from aqros_live_trading.domain.models import (
    LiveOrder,
    OrderRouteStatus,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)

router = APIRouter()


def _order_to_response(order: LiveOrder) -> LiveOrderResponse:
    return LiveOrderResponse(
        order_id=order.order_id,
        client_order_id=order.client_order_id,
        broker_order_id=order.broker_order_id,
        portfolio_id=order.portfolio_id,
        symbol=order.symbol,
        side=order.side.value,
        order_type=order.order_type.value,
        quantity=order.quantity,
        price=order.price,
        stop_price=order.stop_price,
        time_in_force=order.time_in_force.value,
        status=order.status.value,
        route_status=order.route_status.value,
        filled_quantity=order.filled_quantity,
        remaining_quantity=order.remaining_quantity or Decimal("0"),
        avg_fill_price=order.avg_fill_price,
        last_fill_price=order.last_fill_price,
        total_commission=order.total_commission,
        broker_latency_ms=order.broker_latency_ms,
        reject_reason=order.reject_reason,
        reject_message=order.reject_message,
        strategy=order.strategy,
        correlation_id=order.correlation_id,
        routed_to=order.routed_to,
        fill_pct=order.fill_pct,
        created_at=order.created_at,
        updated_at=order.updated_at,
    )


@router.get("/health")
@router.get("/health/live")
async def health_live() -> HealthResponse:
    return HealthResponse()


@router.get("/health/ready")
async def health_ready() -> HealthResponse:
    return HealthResponse()


@router.post("/v1/orders", response_model=LiveOrderResponse, status_code=201)
async def create_order(
    req: OrderCreateRequest,
    repo: LiveOrderRepository = Depends(get_order_repository),
    router: OrderRouter = Depends(get_order_router),
    execution_engine: ExecutionEngine = Depends(get_execution_engine),
    session_manager: BrokerSessionManager = Depends(get_session_manager),
) -> LiveOrderResponse:
    if session_manager.kill_switch.is_triggered():
        raise HTTPException(status_code=503, detail="Kill switch is triggered")

    now = datetime.now(UTC)

    try:
        side = OrderSide(req.side)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid side: {req.side}") from None

    try:
        order_type = OrderType(req.order_type)
    except ValueError:
        raise HTTPException(
            status_code=422, detail=f"Invalid order type: {req.order_type}"
        ) from None

    try:
        time_in_force = TimeInForce(req.time_in_force)
    except ValueError:
        raise HTTPException(
            status_code=422, detail=f"Invalid time in force: {req.time_in_force}"
        ) from None

    existing = await repo.find_by_client_order_id(req.client_order_id)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Duplicate client_order_id: {req.client_order_id}",
        )

    order = LiveOrder(
        order_id=str(uuid.uuid4()),
        client_order_id=req.client_order_id,
        portfolio_id=req.portfolio_id,
        symbol=req.symbol.upper(),
        side=side,
        order_type=order_type,
        quantity=req.quantity,
        price=req.price,
        stop_price=req.stop_price,
        time_in_force=time_in_force,
        status=OrderStatus.PENDING,
        route_status=OrderRouteStatus.PENDING_ROUTE,
        filled_quantity=Decimal("0"),
        remaining_quantity=req.quantity,
        strategy=req.strategy,
        correlation_id=req.correlation_id,
        created_at=now,
        updated_at=now,
    )

    try:
        _broker_name, broker = await router.route(order)
        execution_engine = ExecutionEngine(broker)
        order = await execution_engine.execute(order)
    except (ConnectionError, Exception) as exc:
        order.status = OrderStatus.REJECTED
        order.route_status = OrderRouteStatus.FAILED
        order.reject_reason = "routing_failed"
        order.reject_message = str(exc)
        order.updated_at = now

    await repo.save(order)
    return _order_to_response(order)


@router.get("/v1/orders/{order_id}", response_model=LiveOrderResponse)
async def get_order(
    order_id: str,
    repo: LiveOrderRepository = Depends(get_order_repository),
) -> LiveOrderResponse:
    order = await repo.find_by_id(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail=f"Order not found: {order_id}")
    return _order_to_response(order)


@router.get("/v1/orders", response_model=OrderListResponse)
async def list_orders(
    portfolio_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
    repo: LiveOrderRepository = Depends(get_order_repository),
) -> OrderListResponse:
    order_status = None
    if status is not None:
        try:
            order_status = OrderStatus(status)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Invalid status: {status}") from None

    if portfolio_id is not None:
        orders = await repo.find_by_portfolio(portfolio_id, status=order_status, limit=limit)
    else:
        orders = await repo.find_open_orders(limit=limit)

    return OrderListResponse(
        orders=[_order_to_response(o) for o in orders],
        total=len(orders),
    )


@router.post("/v1/orders/{order_id}/cancel", response_model=LiveOrderResponse)
async def cancel_order(
    order_id: str,
    _req: CancelRequest,
    repo: LiveOrderRepository = Depends(get_order_repository),
    execution_engine: ExecutionEngine = Depends(get_execution_engine),
) -> LiveOrderResponse:
    order = await repo.find_by_id(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail=f"Order not found: {order_id}")
    if order.is_complete:
        raise HTTPException(
            status_code=400,
            detail=f"Order {order_id} is already complete (status: {order.status.value})",
        )

    if order.broker_order_id:
        try:
            report = await execution_engine.cancel(order.broker_order_id)
            order.status = report.status
            order.updated_at = datetime.now(UTC)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Broker cancel failed: {exc}") from exc
    else:
        order.status = OrderStatus.CANCELLED
        order.updated_at = datetime.now(UTC)

    await repo.save(order)
    return _order_to_response(order)


@router.get("/v1/positions", response_model=list[PositionResponse])
async def get_positions(
    execution_engine: ExecutionEngine = Depends(get_execution_engine),
) -> list[PositionResponse]:
    try:
        positions = await execution_engine.get_positions()
    except NotImplementedError:
        raise HTTPException(
            status_code=501, detail="Positions not yet implemented by broker"
        ) from None
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to get positions: {exc}") from exc

    return [
        PositionResponse(
            symbol=p.symbol,
            quantity=p.quantity,
            market_value=p.market_value,
            cost_basis=p.cost_basis,
            avg_entry_price=p.avg_entry_price,
            unrealized_pl=p.unrealized_pl,
            realized_pl=p.realized_pl,
            updated_at=p.updated_at,
        )
        for p in positions
    ]


@router.get("/v1/account", response_model=AccountResponse)
async def get_account(
    execution_engine: ExecutionEngine = Depends(get_execution_engine),
) -> AccountResponse:
    try:
        account = await execution_engine.get_account()
    except NotImplementedError:
        raise HTTPException(
            status_code=501, detail="Account info not yet implemented by broker"
        ) from None
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to get account: {exc}") from exc

    return AccountResponse(
        account_id=account.account_id,
        buying_power=account.buying_power,
        cash=account.cash,
        portfolio_value=account.portfolio_value,
        currency=account.currency,
        status=account.status,
        timestamp=account.timestamp,
    )


@router.get("/v1/broker/status", response_model=BrokerInfoResponse)
async def broker_status(
    session_manager: BrokerSessionManager = Depends(get_session_manager),
) -> BrokerInfoResponse:
    health = session_manager.health
    ks = session_manager.kill_switch
    return BrokerInfoResponse(
        name="default",
        connected=health.is_connected(),
        health=ConnectionHealthResponse(
            status=health.status.value,
            last_connected_at=health.last_connected_at,
            last_disconnected_at=health.last_disconnected_at,
            last_heartbeat_at=health.last_heartbeat_at,
            consecutive_failures=health.consecutive_failures,
            total_disconnections=health.total_disconnections,
            total_reconnections=health.total_reconnections,
        ),
        kill_switch=KillSwitchResponse(
            status=ks.status.value,
            triggered_at=ks.triggered_at,
            triggered_by=ks.triggered_by,
            reason=ks.reason,
            enabled=ks.enabled,
        ),
    )


@router.post("/v1/broker/connect")
async def broker_connect(
    session_manager: BrokerSessionManager = Depends(get_session_manager),
) -> dict[str, object]:
    await session_manager.start()
    return {"status": "ok", "broker_connected": session_manager.health.is_connected()}


@router.post("/v1/broker/disconnect")
async def broker_disconnect(
    session_manager: BrokerSessionManager = Depends(get_session_manager),
) -> dict[str, object]:
    await session_manager.stop()
    return {"status": "ok", "broker_connected": session_manager.health.is_connected()}


@router.get("/v1/kill-switch", response_model=KillSwitchResponse)
async def get_kill_switch(
    kill_switch_manager: KillSwitchManager = Depends(get_kill_switch_manager),
) -> KillSwitchResponse:
    ks = kill_switch_manager.get_status()
    return KillSwitchResponse(
        status=ks["status"],
        triggered_at=ks["triggered_at"],
        triggered_by=ks["triggered_by"],
        reason=ks["reason"],
        enabled=ks["enabled"],
    )


@router.post("/v1/kill-switch/trigger")
async def trigger_kill_switch(
    reason: str = "manual",
    kill_switch_manager: KillSwitchManager = Depends(get_kill_switch_manager),
) -> dict[str, str]:
    kill_switch_manager.trigger(by="api", reason=reason)
    return {"status": "triggered", "reason": reason}


@router.post("/v1/kill-switch/reset")
async def reset_kill_switch(
    kill_switch_manager: KillSwitchManager = Depends(get_kill_switch_manager),
) -> dict[str, str]:
    kill_switch_manager.reset()
    return {"status": "reset"}


@router.get("/v1/calendar", response_model=TradingSessionResponse)
async def get_calendar(
    session_manager: BrokerSessionManager = Depends(get_session_manager),
) -> TradingSessionResponse:
    calendar = TradingCalendar()
    now = datetime.now(UTC)
    session = calendar.get_trading_session(now)
    return TradingSessionResponse(
        date=session.date.isoformat(),
        open=session.open,
        close=session.close,
        pre_market_open=session.pre_market_open,
        pre_market_close=session.pre_market_close,
        after_hours_open=session.after_hours_open,
        after_hours_close=session.after_hours_close,
        status=calendar.current_session_status(now).value,
    )


@router.get("/v1/routes")
async def get_routes(
    order_router: OrderRouter = Depends(get_order_router),
) -> list[dict[str, object]]:
    return [
        {
            "symbol_pattern": r.symbol_pattern,
            "order_types": [t.value for t in r.order_types],
            "preferred_broker": r.preferred_broker,
            "fallback_brokers": list(r.fallback_brokers),
            "requires_pre_trade_risk": r.requires_pre_trade_risk,
        }
        for r in order_router.get_rules()
    ]
