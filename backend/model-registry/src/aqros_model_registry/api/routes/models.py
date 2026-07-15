"""Registration and read endpoints for ``Model_Version`` records.

``{model_name}`` in these paths is the full composite
``{dataset_name}__{model_type}`` string carried over from the Training Pipeline.

Every mutating call requires a client-supplied ``Idempotency-Key`` header
(Requirement 19.11); registration itself is additionally idempotent on
``(model_name, version, training_run_id)`` in the domain service (Requirement
2.4). Missing-resource reads surface a typed ``404`` (Requirement 19.10), and
the registration path maps each domain failure to its HTTP surface per
design.md Section 13. This module mirrors ``aqros_training_pipeline``'s route
style and never imports ``aqros_training_pipeline``.
"""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from aqros_model_registry.api.deps import (
    get_model_registry_service,
    get_registry_query_service,
)
from aqros_model_registry.api.schemas import (
    LineageResponse,
    MetricsResponse,
    ModelVersionResponse,
    RegisterModelRequest,
)
from aqros_model_registry.domain.integrity import ChecksumMismatchError
from aqros_model_registry.domain.models import LifecycleState
from aqros_model_registry.domain.ports import (
    TrainedModelNotFoundError,
    UpstreamSourceError,
)
from aqros_model_registry.domain.services import (
    MandatoryMetadataIncompleteError,
    ModelRegistryService,
    ModelVersionNotFoundError,
    RegistryQueryService,
)

router = APIRouter(prefix="/v1/models", tags=["models"])


@router.post("", response_model=ModelVersionResponse, status_code=status.HTTP_201_CREATED)
async def register_model(
    body: RegisterModelRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    actor: str = Header(default="system", alias="X-Actor"),
    correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    service: ModelRegistryService = Depends(get_model_registry_service),
) -> ModelVersionResponse:
    """Register a Model_Version from a Training Pipeline reference (Requirements 2.1, 19.1).

    Requires a client ``Idempotency-Key`` header — a request lacking it is
    rejected with ``422`` by header validation (Requirement 19.11); the acting
    principal is taken from ``X-Actor`` (defaulting to ``system``) and the
    correlation id from ``X-Correlation-Id`` (defaulting to a generated uuid).
    The domain failures map to their HTTP surface per design.md Section 13:
    an unknown upstream trained model to ``404``, any other upstream failure to
    ``502``, and incomplete mandatory metadata or a checksum mismatch on ingest
    to ``422`` — none of which persist a partial ``Model_Version``.
    """
    resolved_correlation_id = correlation_id or str(uuid4())
    try:
        model_version = await service.register(
            model_name=body.model_name,
            version=body.version,
            training_run_id=body.training_run_id,
            actor=actor,
            correlation_id=resolved_correlation_id,
        )
    except TrainedModelNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except UpstreamSourceError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except MandatoryMetadataIncompleteError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except ChecksumMismatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return ModelVersionResponse.from_domain(model_version)


@router.get("", response_model=list[ModelVersionResponse])
async def list_models(
    model_name: str | None = Query(default=None),
    lifecycle_state: LifecycleState | None = Query(default=None),
    service: RegistryQueryService = Depends(get_registry_query_service),
) -> list[ModelVersionResponse]:
    """List Model_Versions, optionally filtered by name and/or lifecycle state (Requirement 19.2)."""
    model_versions = await service.list_model_versions(
        model_name=model_name, lifecycle_state=lifecycle_state
    )
    return [ModelVersionResponse.from_domain(mv) for mv in model_versions]


@router.get("/{model_name}/versions/{version}", response_model=ModelVersionResponse)
async def get_model_version(
    model_name: str,
    version: int,
    service: RegistryQueryService = Depends(get_registry_query_service),
) -> ModelVersionResponse:
    """Return a Model_Version's full metadata, lineage, and reproducibility metadata.

    404 if no such ``(model_name, version)`` exists (Requirements 19.3, 19.10).
    """
    try:
        model_version = await service.get_model_version(model_name, version)
    except ModelVersionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ModelVersionResponse.from_domain(model_version)


@router.get("/{model_name}/versions/{version}/metrics", response_model=MetricsResponse)
async def get_model_metrics(
    model_name: str,
    version: int,
    service: RegistryQueryService = Depends(get_registry_query_service),
) -> MetricsResponse:
    """Return a Model_Version's Metrics_Record, 404 if missing (Requirements 10.2, 19.4, 19.10)."""
    try:
        metrics = await service.get_metrics(model_name, version)
    except ModelVersionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return MetricsResponse.from_domain(model_name, version, metrics)


@router.get("/{model_name}/versions/{version}/lineage", response_model=LineageResponse)
async def get_model_lineage(
    model_name: str,
    version: int,
    service: RegistryQueryService = Depends(get_registry_query_service),
) -> LineageResponse:
    """Return a Model_Version's full Lineage chain, 404 if missing (Requirements 9.2, 19.10)."""
    try:
        lineage = await service.get_lineage(model_name, version)
    except ModelVersionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return LineageResponse.from_domain(lineage)
