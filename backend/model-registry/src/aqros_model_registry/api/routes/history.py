"""Read-side governance endpoints: production resolution, promotion history, and audit history.

``{model_name}`` in these paths is the full composite
``{dataset_name}__{model_type}`` string carried over from the Training Pipeline.

These endpoints are pure reads with no idempotency-key requirement. Each
domain failure is mapped to its HTTP surface per design.md Section 13: an
unknown ``Model_Version`` to ``404``. This module mirrors
``aqros_training_pipeline``'s route style and never imports
``aqros_training_pipeline``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from aqros_model_registry.api.deps import get_registry_query_service
from aqros_model_registry.api.schemas import (
    AuditEventResponse,
    ProductionResolutionResponse,
    PromotionHistoryResponse,
)
from aqros_model_registry.domain.services import (
    ModelVersionNotFoundError,
    RegistryQueryService,
)

router = APIRouter(prefix="/v1/models", tags=["history"])


@router.get("/{model_name}/production", response_model=ProductionResolutionResponse)
async def get_production(
    model_name: str,
    service: RegistryQueryService = Depends(get_registry_query_service),
) -> ProductionResolutionResponse:
    """Resolve the current Production_Model for a Registered_Model (Requirements 16.3, 19.7).

    Always responds ``200``: when no Model_Version of ``model_name`` is
    currently ``PRODUCTION``, the response reports ``exists=False`` with
    ``production=None`` rather than a ``404`` (Requirement 16.4).
    """
    production = await service.resolve_production(model_name)
    return ProductionResolutionResponse.from_domain(model_name, production)


@router.get(
    "/{model_name}/versions/{version}/promotion-history",
    response_model=list[PromotionHistoryResponse],
)
async def get_promotion_history(
    model_name: str,
    version: int,
    service: RegistryQueryService = Depends(get_registry_query_service),
) -> list[PromotionHistoryResponse]:
    """Return the ordered Promotion_History of one Model_Version (Requirements 17.3, 19.8).

    404 if no such ``(model_name, version)`` exists (Requirement 19.10); an
    empty list is a valid result meaning no transition has yet been applied.
    """
    try:
        await service.get_model_version(model_name, version)
    except ModelVersionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    history = await service.get_promotion_history(model_name, version)
    return [PromotionHistoryResponse.from_domain(entry) for entry in history]


@router.get("/audit", response_model=list[AuditEventResponse])
async def get_audit_history(
    model_name: str | None = Query(default=None),
    correlation_id: str | None = Query(default=None),
    service: RegistryQueryService = Depends(get_registry_query_service),
) -> list[AuditEventResponse]:
    """Return the append-only Audit_History, optionally filtered (Requirements 18.3, 19.8).

    With no filters, returns every recorded privileged action; ``model_name``
    restricts the trail to one Registered_Model and ``correlation_id`` to a
    single request correlation identifier. An empty list is a valid result.
    """
    events = await service.get_audit_history(model_name=model_name, correlation_id=correlation_id)
    return [AuditEventResponse.from_domain(event) for event in events]
