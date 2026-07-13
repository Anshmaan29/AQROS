"""Pipeline endpoints: trigger feature computation and inspect its audit trail."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from aqros_feature_store.api.deps import get_engineering_service
from aqros_feature_store.api.schemas import ComputationRequest, ComputationRunResponse
from aqros_feature_store.domain.ports import MarketDataSourceError
from aqros_feature_store.domain.services import FeatureEngineeringService

router = APIRouter(prefix="/v1/pipeline", tags=["pipeline"])


@router.post("/compute", response_model=ComputationRunResponse, status_code=status.HTTP_201_CREATED)
async def trigger_computation(
    payload: ComputationRequest,
    service: FeatureEngineeringService = Depends(get_engineering_service),
) -> ComputationRunResponse:
    """Run the feature-engineering pipeline for one symbol (full or incremental).

    Reads validated OHLCV bars from the Market Data Service, computes every
    registered feature, validates the results, and persists the valid ones.
    Every run — success or failure — is recorded for audit.
    """
    try:
        run = await service.run_computation(payload.symbol, mode=payload.mode)
    except MarketDataSourceError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Upstream Market Data Service failed: {exc}",
        ) from exc
    return ComputationRunResponse.from_domain(run)


@router.get("/runs/{run_id}", response_model=ComputationRunResponse)
async def get_run(
    run_id: int,
    service: FeatureEngineeringService = Depends(get_engineering_service),
) -> ComputationRunResponse:
    """Fetch one computation run by id."""
    run = await service.get_run(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown computation run '{run_id}'"
        )
    return ComputationRunResponse.from_domain(run)


@router.get("/runs", response_model=list[ComputationRunResponse])
async def list_runs(
    symbol: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    service: FeatureEngineeringService = Depends(get_engineering_service),
) -> list[ComputationRunResponse]:
    """List computation runs, most recent first, optionally filtered by symbol."""
    runs = await service.list_runs(symbol=symbol, limit=limit, offset=offset)
    return [ComputationRunResponse.from_domain(r) for r in runs]
