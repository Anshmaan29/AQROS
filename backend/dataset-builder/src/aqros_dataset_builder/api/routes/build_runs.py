"""Build-run endpoints: inspect the audit trail and download generated artifacts."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse

from aqros_dataset_builder.api.deps import get_query_service
from aqros_dataset_builder.api.schemas import DatasetBuildRunResponse
from aqros_dataset_builder.domain.services import DatasetQueryService

router = APIRouter(prefix="/v1/runs", tags=["build-runs"])


@router.get("/{run_id}", response_model=DatasetBuildRunResponse)
async def get_run(
    run_id: int,
    service: DatasetQueryService = Depends(get_query_service),
) -> DatasetBuildRunResponse:
    """Fetch one dataset build run by id, including its leakage-audit result."""
    run = await service.get_run(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown build run '{run_id}'"
        )
    return DatasetBuildRunResponse.from_domain(run)


@router.get("", response_model=list[DatasetBuildRunResponse])
async def list_runs(
    dataset_name: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    service: DatasetQueryService = Depends(get_query_service),
) -> list[DatasetBuildRunResponse]:
    """List dataset build runs, most recent first, optionally filtered by dataset name."""
    runs = await service.list_runs(dataset_name=dataset_name, limit=limit, offset=offset)
    return [DatasetBuildRunResponse.from_domain(r) for r in runs]


@router.get("/{run_id}/manifest")
async def get_run_manifest(
    run_id: int,
    service: DatasetQueryService = Depends(get_query_service),
) -> dict[str, object]:
    """Fetch the reproducibility manifest for a successful build run.

    Contains dataset metadata (feature versions, label definition, split
    strategy, symbols, date range, creation timestamp), the git commit that
    produced it (if available), the artifact checksum, and the full quality
    report — everything needed to understand and reproduce the dataset.
    """
    manifest = await service.get_manifest(run_id)
    if manifest is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No manifest for build run '{run_id}'",
        )
    return manifest


@router.get("/{run_id}/download")
async def download_run_artifact(
    run_id: int,
    service: DatasetQueryService = Depends(get_query_service),
) -> FileResponse:
    """Download the Parquet artifact produced by a successful build run."""
    run = await service.get_run(run_id)
    if run is None or run.parquet_path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No downloadable artifact for build run '{run_id}'",
        )
    return FileResponse(
        run.parquet_path,
        media_type="application/octet-stream",
        filename=f"{run.dataset_name}_v{run.dataset_version}_run{run_id}.parquet",
    )
