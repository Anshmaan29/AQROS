"""Trained-model endpoints: list, metadata, metrics, and artifact download.

``{model_name}`` in these paths is the full composite
``{dataset_name}__{model_type}`` string (Key Design Decision 3).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from aqros_training_pipeline.api.deps import (
    get_artifact_store,
    get_training_query_service,
)
from aqros_training_pipeline.api.schemas import (
    MetricsReportResponse,
    ReproducibilityMetadataResponse,
    TrainedModelResponse,
)
from aqros_training_pipeline.domain.ports import ArtifactStore
from aqros_training_pipeline.domain.services import TrainingQueryService

router = APIRouter(prefix="/v1/trained-models", tags=["trained-models"])


@router.get("", response_model=list[TrainedModelResponse])
async def list_trained_models(
    model_name: str | None = Query(default=None),
    service: TrainingQueryService = Depends(get_training_query_service),
) -> list[TrainedModelResponse]:
    """List trained models, optionally filtered by composite ``model_name`` (Req 14.3)."""
    models = await service.list_trained_models(model_name)
    return [TrainedModelResponse.from_domain(m) for m in models]


@router.get(
    "/{model_name}/versions/{version}/metadata",
    response_model=ReproducibilityMetadataResponse,
)
async def get_metadata(
    model_name: str,
    version: int,
    service: TrainingQueryService = Depends(get_training_query_service),
) -> ReproducibilityMetadataResponse:
    """Retrieve a model version's reproducibility metadata, 404 if missing (Req 14.4)."""
    model = await service.get_trained_model(model_name, version)
    if model is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown trained model '{model_name}' v{version}",
        )
    return ReproducibilityMetadataResponse.from_domain(model.reproducibility_metadata)


@router.get(
    "/{model_name}/versions/{version}/metrics",
    response_model=MetricsReportResponse,
)
async def get_metrics(
    model_name: str,
    version: int,
    service: TrainingQueryService = Depends(get_training_query_service),
) -> MetricsReportResponse:
    """Retrieve a model version's metrics report, 404 if missing (Req 14.5)."""
    report = await service.get_metrics_report(model_name, version)
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown trained model '{model_name}' v{version}",
        )
    return MetricsReportResponse.from_domain(report)


@router.get("/{model_name}/versions/{version}/artifact")
async def download_artifact(
    model_name: str,
    version: int,
    service: TrainingQueryService = Depends(get_training_query_service),
    artifact_store: ArtifactStore = Depends(get_artifact_store),
) -> Response:
    """Download a model version's serialized artifact bytes, 404 if missing (Req 14.6)."""
    model = await service.get_trained_model(model_name, version)
    if model is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown trained model '{model_name}' v{version}",
        )
    try:
        data = await artifact_store.read_artifact(model_name, version)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No artifact bytes for '{model_name}' v{version}",
        ) from exc
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{model_name}_v{version}.joblib"'},
    )
