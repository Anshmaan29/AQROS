"""Retrieval endpoints: read stored instruments and OHLCV bars."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status

from aqros_market_data.api.deps import get_query_service
from aqros_market_data.api.schemas import (
    InstrumentResponse,
    OHLCVBarResponse,
    PaginatedBarsResponse,
)
from aqros_market_data.domain.models import BarInterval
from aqros_market_data.domain.services import MarketDataQueryService

router = APIRouter(prefix="/v1", tags=["market-data"])


@router.get("/instruments", response_model=list[InstrumentResponse])
async def list_instruments(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    service: MarketDataQueryService = Depends(get_query_service),
) -> list[InstrumentResponse]:
    """List known instruments (those that have been ingested at least once)."""
    instruments = await service.list_instruments(limit=limit, offset=offset)
    return [InstrumentResponse.from_domain(i) for i in instruments]


@router.get("/instruments/{symbol}", response_model=InstrumentResponse)
async def get_instrument(
    symbol: str,
    service: MarketDataQueryService = Depends(get_query_service),
) -> InstrumentResponse:
    """Fetch a single instrument by symbol."""
    instrument = await service.get_instrument(symbol)
    if instrument is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown instrument '{symbol}'"
        )
    return InstrumentResponse.from_domain(instrument)


@router.get("/instruments/{symbol}/bars", response_model=PaginatedBarsResponse)
async def get_bars(
    symbol: str,
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    interval: BarInterval = Query(default=BarInterval.DAILY),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    service: MarketDataQueryService = Depends(get_query_service),
) -> PaginatedBarsResponse:
    """Retrieve stored OHLCV bars for a symbol, optionally filtered by date range."""
    bars, total = await service.get_bars(
        symbol, start=start, end=end, interval=interval, limit=limit, offset=offset
    )
    return PaginatedBarsResponse(
        symbol=symbol.upper(),
        interval=interval,
        total=total,
        limit=limit,
        offset=offset,
        bars=[OHLCVBarResponse.from_domain(bar) for bar in bars],
    )
