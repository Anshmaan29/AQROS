"""Dataset endpoints: register definitions, trigger builds, retrieve artifacts."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status

from aqros_dataset_builder.api.deps import get_builder_service, get_query_service
from aqros_dataset_builder.api.schemas import (
    BuildRequest,
    DatasetBuildRunResponse,
    DatasetDefinitionRequest,
    DatasetDefinitionResponse,
)
from aqros_dataset_builder.domain.models import DatasetDefinition
from aqros_dataset_builder.domain.services import DatasetBuilderService, DatasetQueryService

router = APIRouter(prefix="/v1/datasets", tags=["datasets"])


@router.post("", response_model=DatasetDefinitionResponse, status_code=status.HTTP_201_CREATED)
async def create_definition(
    payload: DatasetDefinitionRequest,
    service: DatasetBuilderService = Depends(get_builder_service),
    query_service: DatasetQueryService = Depends(get_query_service),
) -> DatasetDefinitionResponse:
    """Register a new, immutable dataset definition version.

    The version number auto-increments per name: the first registration of
    a given name is version 1, and re-registering the same name (even with
    changed parameters) always creates the *next* version rather than
    overwriting — definitions are immutable once created (CLAUDE.md §10).
    """
    existing = await query_service.get_latest_definition(payload.name)
    next_version = (existing.version + 1) if existing is not None else 1

    definition = DatasetDefinition(
        name=payload.name,
        version=next_version,
        symbols=tuple(payload.symbols),
        feature_names=tuple(payload.feature_names),
        label_type=payload.label_type,
        horizon=payload.horizon,
        split_strategy=payload.split_strategy,
        split_params=payload.to_split_params(),
        start_date=payload.start_date,
        end_date=payload.end_date,
        created_at=datetime.now(UTC),
    )
    created = await service.create_definition(definition)
    return DatasetDefinitionResponse.from_domain(created)


@router.get("", response_model=list[DatasetDefinitionResponse])
async def list_definitions(
    service: DatasetQueryService = Depends(get_query_service),
) -> list[DatasetDefinitionResponse]:
    """List every registered dataset definition (all versions)."""
    definitions = await service.list_definitions()
    return [DatasetDefinitionResponse.from_domain(d) for d in definitions]


@router.get("/{name}", response_model=DatasetDefinitionResponse)
async def get_latest_definition(
    name: str,
    service: DatasetQueryService = Depends(get_query_service),
) -> DatasetDefinitionResponse:
    """Fetch the latest registered version of a named dataset definition."""
    definition = await service.get_latest_definition(name)
    if definition is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown dataset '{name}'"
        )
    return DatasetDefinitionResponse.from_domain(definition)


@router.get("/{name}/versions/{version}", response_model=DatasetDefinitionResponse)
async def get_definition_version(
    name: str,
    version: int,
    service: DatasetQueryService = Depends(get_query_service),
) -> DatasetDefinitionResponse:
    """Fetch one specific version of a dataset definition."""
    definition = await service.get_definition(name, version)
    if definition is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown dataset '{name}'@{version}",
        )
    return DatasetDefinitionResponse.from_domain(definition)


@router.post(
    "/{name}/build", response_model=DatasetBuildRunResponse, status_code=status.HTTP_201_CREATED
)
async def trigger_build(
    name: str,
    payload: BuildRequest,
    service: DatasetBuilderService = Depends(get_builder_service),
) -> DatasetBuildRunResponse:
    """Run the dataset-generation pipeline for a registered definition version.

    Reads OHLCV bars (labels) and feature values (the X matrix) via their
    respective upstream REST APIs, aligns and labels the data, applies the
    split strategy, runs the automated leakage audit, and — only if the
    audit passes — persists the resulting Parquet artifact. Every run is
    recorded for audit regardless of outcome.
    """
    try:
        run = await service.build_dataset(name, payload.version)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return DatasetBuildRunResponse.from_domain(run)


@router.get("/{name}/runs/{run_id}/preview")
async def preview_run_rows(
    name: str,
    run_id: int,
    limit: int = Query(default=20, ge=1, le=1000),
    service: DatasetQueryService = Depends(get_query_service),
) -> list[dict[str, object]]:
    """Preview the first rows of a completed build run's generated dataset."""
    return await service.preview_rows(run_id, limit=limit)
