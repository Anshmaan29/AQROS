"""Training-run endpoints: create/execute a run and retrieve its status + report."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from aqros_training_pipeline.api.deps import (
    get_training_pipeline_service,
    get_training_query_service,
)
from aqros_training_pipeline.api.schemas import TrainingRequestSchema, TrainingRunResponse
from aqros_training_pipeline.domain.models import TrainingRequest
from aqros_training_pipeline.domain.ports import (
    DatasetBuildRunNotFoundError,
    UpstreamSourceError,
)
from aqros_training_pipeline.domain.reports import build_training_report
from aqros_training_pipeline.domain.services import (
    TrainingPipelineService,
    TrainingQueryService,
)

router = APIRouter(prefix="/v1/training-runs", tags=["training-runs"])


@router.post("", response_model=TrainingRunResponse, status_code=status.HTTP_201_CREATED)
async def create_training_run(
    body: TrainingRequestSchema,
    service: TrainingPipelineService = Depends(get_training_pipeline_service),
) -> TrainingRunResponse:
    """Create and synchronously execute a training run (Key Design Decision 7).

    A missing build run (404 upstream) maps to 404; any other upstream
    failure maps to 502 (design.md Section 14). Verification / training
    failures still create the run and return 201 with a ``failed`` status.
    """
    request = TrainingRequest(
        dataset_name=body.dataset_name,
        build_run_id=body.build_run_id,
        model_types=tuple(body.model_types),
        hyperparameters={mt: dict(hp) for mt, hp in body.hyperparameters.items()},
    )
    try:
        run = await service.create_training_run(request)
    except DatasetBuildRunNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except UpstreamSourceError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return TrainingRunResponse.from_domain(run, build_training_report(run))


@router.get("/{run_id}", response_model=TrainingRunResponse)
async def get_training_run(
    run_id: int,
    service: TrainingQueryService = Depends(get_training_query_service),
) -> TrainingRunResponse:
    """Retrieve a training run's status + report, 404 if missing (Requirement 14.2)."""
    run = await service.get_run(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown training run '{run_id}'"
        )
    return TrainingRunResponse.from_domain(run, build_training_report(run))
