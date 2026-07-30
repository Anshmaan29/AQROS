"""Retrieval endpoints: feature definitions, values, statistics, and online serving."""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status

from aqros_feature_store.api.deps import get_online_service, get_query_service
from aqros_feature_store.api.schemas import (
    FeatureDefinitionResponse,
    FeatureStatisticsResponse,
    FeatureValueResponse,
    OnlineFeatureSnapshotResponse,
    OnlineFeatureValueResponse,
    PaginatedFeatureValuesResponse,
)
from aqros_feature_store.domain.online_service import OnlineFeatureService
from aqros_feature_store.domain.services import FeatureQueryService

router = APIRouter(prefix="/v1", tags=["features"])


@router.get("/definitions", response_model=list[FeatureDefinitionResponse])
async def list_definitions(
    service: FeatureQueryService = Depends(get_query_service),
) -> list[FeatureDefinitionResponse]:
    """List every registered feature definition (the catalog)."""
    definitions = await service.list_definitions()
    return [FeatureDefinitionResponse.from_domain(d) for d in definitions]


@router.get("/definitions/{name}", response_model=FeatureDefinitionResponse)
async def get_latest_definition(
    name: str,
    service: FeatureQueryService = Depends(get_query_service),
) -> FeatureDefinitionResponse:
    """Fetch the latest registered version of a named feature definition."""
    definition = await service.get_latest_definition(name)
    if definition is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown feature '{name}'"
        )
    return FeatureDefinitionResponse.from_domain(definition)


@router.get(
    "/instruments/{symbol}/features/{feature_name}",
    response_model=PaginatedFeatureValuesResponse,
)
async def get_feature_values(
    symbol: str,
    feature_name: str,
    feature_version: int | None = Query(default=None),
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    as_of: datetime | None = Query(
        default=None,
        description=(
            "Point-in-time cutoff: only values with knowledge_time <= as_of are "
            "returned. Omit for the latest known state."
        ),
    ),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    service: FeatureQueryService = Depends(get_query_service),
) -> PaginatedFeatureValuesResponse:
    """Retrieve stored feature values for a symbol, optionally point-in-time filtered."""
    values, total = await service.get_values(
        symbol,
        feature_name,
        feature_version=feature_version,
        start=start,
        end=end,
        as_of=as_of,
        limit=limit,
        offset=offset,
    )
    return PaginatedFeatureValuesResponse(
        symbol=symbol.upper(),
        feature_name=feature_name,
        total=total,
        limit=limit,
        offset=offset,
        values=[FeatureValueResponse.from_domain(v) for v in values],
    )


# ---------------------------------------------------------------------------
# Online (Redis-backed) feature endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/online/instruments/{symbol}/features",
    response_model=OnlineFeatureSnapshotResponse,
)
async def get_online_snapshot(
    symbol: str,
    service: OnlineFeatureService = Depends(get_online_service),
) -> OnlineFeatureSnapshotResponse:
    """Return all latest-known feature values for ``symbol`` (Redis-backed)."""
    features = await service.get_snapshot(symbol)
    return OnlineFeatureSnapshotResponse(
        symbol=symbol.upper(),
        feature_count=len(features),
        features=features,
    )


@router.get(
    "/online/instruments/{symbol}/features/{feature_name}",
    response_model=OnlineFeatureValueResponse | None,
)
async def get_online_feature(
    symbol: str,
    feature_name: str,
    service: OnlineFeatureService = Depends(get_online_service),
) -> OnlineFeatureValueResponse | None:
    """Return the latest-known value for a single feature (Redis-backed).

    Returns ``{"symbol": ..., "feature_name": ..., "value": ...}`` if a value
    exists, or ``null`` (``404``) if no value has been computed yet.
    """
    value = await service.get_latest(symbol, feature_name)
    if value is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No online value for '{symbol}' feature '{feature_name}'",
        )
    return OnlineFeatureValueResponse(
        symbol=symbol.upper(),
        feature_name=feature_name,
        value=value,
    )


@router.get(
    "/instruments/{symbol}/features/{feature_name}/statistics",
    response_model=FeatureStatisticsResponse,
)
async def get_feature_statistics(
    symbol: str,
    feature_name: str,
    feature_version: int = Query(default=1),
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    service: FeatureQueryService = Depends(get_query_service),
) -> FeatureStatisticsResponse:
    """Compute count/mean/std/min/max for a feature over an optional date range."""
    stats = await service.get_statistics(
        symbol, feature_name, feature_version, start=start, end=end
    )
    return FeatureStatisticsResponse.from_domain(stats)
