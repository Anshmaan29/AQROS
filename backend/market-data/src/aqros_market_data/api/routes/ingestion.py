"""Ingestion endpoints: trigger a historical OHLCV download for a symbol."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, status

from aqros_market_data.api.deps import get_ingestion_service
from aqros_market_data.api.schemas import IngestionRequest, IngestionResponse
from aqros_market_data.domain.ports import MarketDataProviderError, SymbolNotFoundError
from aqros_market_data.domain.services import IngestionService

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/v1/ingestion", tags=["ingestion"])


@router.post("", response_model=IngestionResponse, status_code=status.HTTP_201_CREATED)
async def ingest_symbol(
    payload: IngestionRequest,
    service: IngestionService = Depends(get_ingestion_service),
) -> IngestionResponse:
    """Download historical OHLCV data for a symbol and persist validated bars.

    Rejected bars (failing validation) are reported in the response rather
    than causing a failure — the caller can see exactly what was rejected.
    """
    try:
        result = await service.ingest_symbol(
            payload.symbol,
            start=payload.start,
            end=payload.end,
            interval=payload.interval,
        )
    except SymbolNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except MarketDataProviderError as exc:
        logger.error("ingestion.provider_error", symbol=payload.symbol, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Upstream market-data provider failed: {exc}",
        ) from exc

    return IngestionResponse(
        symbol=result.symbol,
        fetched=result.fetched,
        persisted=result.persisted,
        rejected=result.rejected,
        rejection_reasons=result.rejection_reasons,
    )
