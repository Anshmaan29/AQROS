from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException

from aqros_oms.adapters.repository import OrderRepository
from aqros_oms.api.deps import get_order_repository
from aqros_oms.api.schemas import (
    CancelRequest,
    FillRequest,
    FillResponse,
    HealthResponse,
    OrderCreateRequest,
    OrderListResponse,
    OrderResponse,
)
from aqros_oms.domain.models import (
    Fill,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    RejectReason,
    TimeInForce,
    calculate_order_expiry,
    validate_order,
)

router = APIRouter()


def _order_to_response(order: Order) -> OrderResponse:
    fills = [
        FillResponse(
            trade_id=f.trade_id,
            quantity=f.quantity,
            price=f.price,
            commission=f.commission,
            created_at=f.created_at,
        )
        for f in order.fills
    ]
    return OrderResponse(
        order_id=order.order_id,
        client_order_id=order.client_order_id,
        portfolio_id=order.portfolio_id,
        symbol=order.symbol,
        side=order.side.value,
        order_type=order.order_type.value,
        quantity=order.quantity,
        price=order.price,
        stop_price=order.stop_price,
        time_in_force=order.time_in_force.value,
        status=order.status.value,
        filled_quantity=order.filled_quantity,
        filled_value=order.filled_value,
        total_commission=order.total_commission,
        avg_fill_price=order.avg_fill_price,
        last_fill_price=order.last_fill_price,
        remaining_quantity=order.remaining_quantity,
        fill_pct=order.fill_pct,
        reject_reason=order.reject_reason.value if order.reject_reason else None,
        reject_message=order.reject_message,
        expires_at=order.expires_at,
        strategy=order.strategy,
        correlation_id=order.correlation_id,
        created_at=order.created_at,
        updated_at=order.updated_at,
        fills=fills,
    )


@router.get("/health")
@router.get("/health/live")
async def health_live() -> HealthResponse:
    return HealthResponse()


@router.get("/health/ready")
async def health_ready() -> HealthResponse:
    return HealthResponse()


@router.post("/v1/orders", response_model=OrderResponse, status_code=201)
async def create_order(
    req: OrderCreateRequest,
    repo: OrderRepository = Depends(get_order_repository),
) -> OrderResponse:
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

    errors = validate_order(
        client_order_id=req.client_order_id,
        portfolio_id=req.portfolio_id,
        symbol=req.symbol,
        side=side,
        order_type=order_type,
        quantity=req.quantity,
        price=req.price,
        stop_price=req.stop_price,
        time_in_force=time_in_force,
    )
    if errors:
        reasons = "; ".join(f"{r.value}: {m}" for r, m in errors)
        raise HTTPException(status_code=422, detail=reasons)

    if order_type == OrderType.STOP_LIMIT and (req.stop_price is None or req.price is None):
        raise HTTPException(
            status_code=422, detail="Stop-limit orders require both stop_price and price"
        )

    if time_in_force == TimeInForce.GTD and req.expires_at is None:
        raise HTTPException(status_code=422, detail="GTD orders require expires_at")

    expires_at = calculate_order_expiry(time_in_force, now, req.expires_at)

    order = Order(
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
        filled_quantity=Decimal("0"),
        filled_value=Decimal("0"),
        total_commission=Decimal("0"),
        avg_fill_price=None,
        last_fill_price=None,
        reject_reason=None,
        reject_message=None,
        expires_at=expires_at,
        strategy=req.strategy,
        correlation_id=req.correlation_id,
        created_at=now,
        updated_at=now,
    )

    order.accept(now)

    await repo.save(order)
    return _order_to_response(order)


@router.get("/v1/orders/{order_id}", response_model=OrderResponse)
async def get_order(
    order_id: str,
    repo: OrderRepository = Depends(get_order_repository),
) -> OrderResponse:
    order = await repo.find_by_id(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail=f"Order not found: {order_id}")
    return _order_to_response(order)


@router.get("/v1/orders", response_model=OrderListResponse)
async def list_orders(
    portfolio_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
    repo: OrderRepository = Depends(get_order_repository),
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


@router.post("/v1/orders/{order_id}/cancel", response_model=OrderResponse)
async def cancel_order(
    order_id: str,
    _req: CancelRequest,
    repo: OrderRepository = Depends(get_order_repository),
) -> OrderResponse:
    order = await repo.find_by_id(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail=f"Order not found: {order_id}")
    if order.is_complete:
        raise HTTPException(
            status_code=400,
            detail=f"Order {order_id} is already complete (status: {order.status.value})",
        )
    order.cancel()
    await repo.save(order)
    return _order_to_response(order)


@router.post("/v1/orders/{order_id}/fill", response_model=OrderResponse)
async def fill_order(
    order_id: str,
    req: FillRequest,
    repo: OrderRepository = Depends(get_order_repository),
) -> OrderResponse:
    order = await repo.find_by_id(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail=f"Order not found: {order_id}")
    if order.is_complete:
        raise HTTPException(
            status_code=400,
            detail=f"Order {order_id} is already complete (status: {order.status.value})",
        )
    if req.quantity > order.remaining_quantity:
        raise HTTPException(
            status_code=422,
            detail=f"Fill quantity {req.quantity} exceeds remaining {order.remaining_quantity}",
        )

    fill = Fill(
        trade_id=req.trade_id,
        order_id=order_id,
        quantity=req.quantity,
        price=req.price,
        commission=req.commission,
        created_at=datetime.now(UTC),
    )
    order.apply_fill(fill)
    await repo.save(order)
    await repo.save_fill(fill)
    return _order_to_response(order)


@router.post("/v1/orders/{order_id}/reject", response_model=OrderResponse)
async def reject_order(
    order_id: str,
    reason: str = "unknown",
    message: str = "",
    repo: OrderRepository = Depends(get_order_repository),
) -> OrderResponse:
    order = await repo.find_by_id(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail=f"Order not found: {order_id}")
    if order.is_complete:
        raise HTTPException(
            status_code=400,
            detail=f"Order {order_id} is already complete (status: {order.status.value})",
        )

    try:
        reject_reason = RejectReason(reason)
    except ValueError:
        reject_reason = RejectReason.UNKNOWN

    order.reject(reject_reason, message)
    await repo.save(order)
    return _order_to_response(order)


@router.get("/v1/portfolios/{portfolio_id}/orders", response_model=OrderListResponse)
async def list_portfolio_orders(
    portfolio_id: str,
    status: str | None = None,
    limit: int = 100,
    repo: OrderRepository = Depends(get_order_repository),
) -> OrderListResponse:
    order_status = None
    if status is not None:
        try:
            order_status = OrderStatus(status)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Invalid status: {status}") from None

    orders = await repo.find_by_portfolio(portfolio_id, status=order_status, limit=limit)
    return OrderListResponse(
        orders=[_order_to_response(o) for o in orders],
        total=len(orders),
    )
