from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException

from aqros_paper_trading.api.deps import get_exchange
from aqros_paper_trading.api.schemas import (
    FillResultResponse,
    HealthResponse,
    MarketDataRequest,
    MarketDataResponse,
    OrderCreateRequest,
    OrderListResponse,
    PaperOrderResponse,
    SimulationConfigResponse,
)
from aqros_paper_trading.domain.models import (
    MarketDataSnapshot,
    OrderSide,
    OrderType,
    SimulatedExchange,
    SimulatedOrder,
)

router = APIRouter()


def _order_to_response(order: SimulatedOrder) -> PaperOrderResponse:
    fills = [
        FillResultResponse(
            trade_id=f.trade_id,
            fill_quantity=f.fill_quantity,
            fill_price=f.fill_price,
            commission=f.commission,
            slippage=f.slippage,
            is_partial=f.is_partial,
            timestamp=f.timestamp,
        )
        for f in order.fills
    ]
    return PaperOrderResponse(
        order_id=order.order_id,
        client_order_id=order.client_order_id,
        portfolio_id=order.portfolio_id,
        symbol=order.symbol,
        side=order.side.value,
        order_type=order.order_type.value,
        quantity=order.quantity,
        price=order.price,
        stop_price=order.stop_price,
        status=order.status.value,
        filled_quantity=order.filled_quantity,
        filled_value=order.filled_value,
        total_commission=order.total_commission,
        avg_fill_price=order.avg_fill_price,
        last_fill_price=order.last_fill_price,
        remaining_quantity=order.remaining_quantity,
        reject_reason=order.reject_reason.value if order.reject_reason else None,
        reject_message=order.reject_message,
        strategy=order.strategy,
        correlation_id=order.correlation_id,
        created_at=order.created_at,
        updated_at=order.updated_at,
        fills=fills,
    )


def _market_to_response(snapshot: MarketDataSnapshot) -> MarketDataResponse:
    return MarketDataResponse(
        symbol=snapshot.symbol,
        bid=snapshot.bid,
        ask=snapshot.ask,
        last=snapshot.last,
        volume=snapshot.volume,
        bid_size=snapshot.bid_size,
        ask_size=snapshot.ask_size,
        timestamp=snapshot.timestamp,
    )


@router.get("/health")
@router.get("/health/live")
async def health_live() -> HealthResponse:
    return HealthResponse()


@router.get("/health/ready")
async def health_ready() -> HealthResponse:
    return HealthResponse()


@router.post("/v1/orders", response_model=PaperOrderResponse, status_code=201)
async def place_order(
    req: OrderCreateRequest,
    exchange: SimulatedExchange = Depends(get_exchange),
) -> PaperOrderResponse:
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

    if req.quantity <= Decimal("0"):
        raise HTTPException(status_code=422, detail="Quantity must be positive")

    if order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT) and (
        req.price is None or req.price <= Decimal("0")
    ):
        raise HTTPException(status_code=422, detail="Limit price required and must be positive")

    if order_type in (OrderType.STOP, OrderType.STOP_LIMIT) and (
        req.stop_price is None or req.stop_price <= Decimal("0")
    ):
        raise HTTPException(status_code=422, detail="Stop price required and must be positive")

    market = exchange.get_market(req.symbol.upper())
    if market is None:
        raise HTTPException(
            status_code=400, detail=f"No market data available for {req.symbol.upper()}"
        )

    order = SimulatedOrder(
        order_id=str(uuid.uuid4()),
        client_order_id=req.client_order_id,
        portfolio_id=req.portfolio_id,
        symbol=req.symbol.upper(),
        side=side,
        order_type=order_type,
        quantity=req.quantity,
        price=req.price,
        stop_price=req.stop_price,
        strategy=req.strategy,
        correlation_id=req.correlation_id,
        created_at=now,
        updated_at=now,
    )

    # Fills are applied onto the order by the exchange; _order_to_response
    # serialises them from order.fills, so there is nothing to return here.
    exchange.place_order(order)
    return _order_to_response(order)


@router.get("/v1/orders/{order_id}", response_model=PaperOrderResponse)
async def get_order(
    order_id: str,
    exchange: SimulatedExchange = Depends(get_exchange),
) -> PaperOrderResponse:
    order = exchange.order_queue.find(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail=f"Order not found: {order_id}")
    return _order_to_response(order)


@router.get("/v1/orders", response_model=OrderListResponse)
async def list_orders(
    portfolio_id: str | None = None,
    symbol: str | None = None,
    status: str | None = None,
    exchange: SimulatedExchange = Depends(get_exchange),
) -> OrderListResponse:
    order_status = None
    if status is not None:
        try:
            from aqros_paper_trading.domain.models import OrderStatus

            order_status = OrderStatus(status)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Invalid status: {status}") from None
    orders = exchange.list_orders(portfolio_id=portfolio_id, symbol=symbol, status=order_status)
    return OrderListResponse(
        orders=[_order_to_response(o) for o in orders],
        total=len(orders),
    )


@router.post("/v1/orders/{order_id}/cancel", response_model=PaperOrderResponse)
async def cancel_order(
    order_id: str,
    exchange: SimulatedExchange = Depends(get_exchange),
) -> PaperOrderResponse:
    order = exchange.order_queue.find(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail=f"Order not found: {order_id}")
    if order.is_complete:
        raise HTTPException(status_code=400, detail=f"Order {order_id} is already complete")
    order.cancel()
    exchange.cancel_order(order_id)
    return _order_to_response(order)


@router.post("/v1/market", response_model=MarketDataResponse)
async def update_market(
    req: MarketDataRequest,
    exchange: SimulatedExchange = Depends(get_exchange),
) -> MarketDataResponse:
    now = datetime.now(UTC)
    snapshot = MarketDataSnapshot(
        symbol=req.symbol.upper(),
        bid=req.bid,
        ask=req.ask,
        last=req.last,
        volume=req.volume,
        bid_size=req.bid_size,
        ask_size=req.ask_size,
        timestamp=now,
    )
    exchange.update_market(snapshot)

    # Resting orders that cross the new quote are filled by the exchange, which
    # applies each fill to its order. This endpoint reports the market snapshot,
    # so the fill list itself is not part of the response.
    exchange.process_resting_orders(snapshot)
    return _market_to_response(snapshot)


@router.get("/v1/market/{symbol}", response_model=MarketDataResponse)
async def get_market(
    symbol: str,
    exchange: SimulatedExchange = Depends(get_exchange),
) -> MarketDataResponse:
    snapshot = exchange.get_market(symbol.upper())
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"No market data for {symbol.upper()}")
    return _market_to_response(snapshot)


@router.get("/v1/markets", response_model=list[str])
async def list_markets(
    exchange: SimulatedExchange = Depends(get_exchange),
) -> list[str]:
    return exchange.list_markets()


@router.get("/v1/config", response_model=SimulationConfigResponse)
async def get_config(
    exchange: SimulatedExchange = Depends(get_exchange),
) -> SimulationConfigResponse:
    return SimulationConfigResponse()
