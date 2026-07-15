"""Governance endpoints: lifecycle transitions, approvals, rejections, and rollback.

``{model_name}`` in these paths is the full composite
``{dataset_name}__{model_type}`` string carried over from the Training Pipeline.

Every mutating endpoint requires a client-supplied ``Idempotency-Key`` header
(Requirement 19.11); a governed transition into ``PRODUCTION`` (including
rollback) is additionally gated by ``Four_Eyes`` in the domain service
(Requirements 14, 15.2). ``request_transition``/``rollback`` take the acting
identity from the request (the ``X-Actor`` header for a transition, since
``TransitionRequestSchema`` carries no requester field; the ``requester``
field of the body for a rollback); ``approve``/``reject`` take the approver
identity from :class:`ApprovalRequestSchema` directly. A correlation id is
read from ``X-Correlation-Id`` where supplied, else a fresh one is generated
per request so every privileged action is still traceable (Requirement 18.1).

Each domain failure is mapped to its HTTP surface per design.md Section 13:
an unknown ``Model_Version``/``Promotion_Request`` to ``404``, an illegal
lifecycle transition or a settled (non-``PENDING``) promotion request to
``409``, missing validation evidence to ``422``, an automated principal
attempting a ``PRODUCTION`` approval to ``403``, and a rollback of a version
that was never in ``PRODUCTION`` to ``409``. A transition or approval that
is parked ``PENDING`` awaiting further approval (rather than applied) is
returned as a :class:`PromotionRequestResponse` with ``202 Accepted``; once
applied, the endpoints return the updated :class:`ModelVersionResponse` with
``200 OK``. This module mirrors ``aqros_training_pipeline``'s route style and
never imports ``aqros_training_pipeline``.
"""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status

from aqros_model_registry.api.deps import get_clock, get_model_registry_service
from aqros_model_registry.api.schemas import (
    ApprovalRequestSchema,
    ModelVersionResponse,
    PromotionRequestResponse,
    RollbackRequestSchema,
    TransitionRequestSchema,
)
from aqros_model_registry.domain.lifecycle import IllegalTransitionError
from aqros_model_registry.domain.models import PromotionRequest, ValidationEvidence
from aqros_model_registry.domain.ports import Clock
from aqros_model_registry.domain.services import (
    AutomatedApprovalNotPermittedError,
    ModelRegistryService,
    ModelVersionNotFoundError,
    NeverInProductionError,
    PromotionRequestNotFoundError,
    PromotionRequestNotPendingError,
    ValidationEvidenceRequiredError,
)

router = APIRouter(prefix="/v1/models", tags=["transitions"])


def _resolve_correlation_id(correlation_id: str | None) -> str:
    """Return ``correlation_id`` if supplied, else a freshly generated uuid."""
    return correlation_id or str(uuid4())


@router.post("/{model_name}/versions/{version}/transition")
async def request_transition(
    model_name: str,
    version: int,
    body: TransitionRequestSchema,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    actor: str = Header(default="system", alias="X-Actor"),
    correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    service: ModelRegistryService = Depends(get_model_registry_service),
    clock: Clock = Depends(get_clock),
) -> ModelVersionResponse | PromotionRequestResponse:
    """Request a lifecycle transition for a Model_Version (Requirements 13.1, 19.6).

    Requires a client ``Idempotency-Key`` header (Requirement 19.11); the
    requesting principal is taken from ``X-Actor`` (defaulting to ``system``)
    and the correlation id from ``X-Correlation-Id`` (defaulting to a
    generated uuid). ``body.validation_evidence``, when supplied, is stamped
    with the current time and attached only if the target transition actually
    requires it (``REGISTERED -> VALIDATED``, Requirement 12).

    Returns a ``200`` :class:`ModelVersionResponse` when the transition is
    applied immediately (an ungated or evidence-gated edge), or a ``202``
    :class:`PromotionRequestResponse` when it is parked ``PENDING`` awaiting
    one authorized approval or Four_Eyes (Requirements 13.2, 14.1, 15.2).
    Domain failures map per design.md Section 13: unknown Model_Version to
    ``404``, an illegal transition to ``409``, and missing validation
    evidence for ``REGISTERED -> VALIDATED`` to ``422``.
    """
    evidence: ValidationEvidence | None = None
    if body.validation_evidence is not None:
        evidence = ValidationEvidence(
            kind=body.validation_evidence.kind,
            reference=body.validation_evidence.reference,
            attached_at=clock.now(),
        )
    try:
        result = await service.request_transition(
            model_name=model_name,
            version=version,
            to_state=body.to_state,
            requester=actor,
            justification=body.justification,
            correlation_id=_resolve_correlation_id(correlation_id),
            validation_evidence=evidence,
        )
    except ModelVersionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValidationEvidenceRequiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except IllegalTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    if isinstance(result, PromotionRequest):
        response.status_code = status.HTTP_202_ACCEPTED
        return PromotionRequestResponse.from_domain(result)
    return ModelVersionResponse.from_domain(result)


@router.post("/{model_name}/versions/{version}/approve")
async def approve_transition(
    model_name: str,
    version: int,
    body: ApprovalRequestSchema,
    response: Response,
    request_id: int = Query(..., description="The Promotion_Request id to approve."),
    idempotency_key: str = Header(alias="Idempotency-Key"),
    correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    service: ModelRegistryService = Depends(get_model_registry_service),
) -> ModelVersionResponse | PromotionRequestResponse:
    """Approve a pending Promotion_Request for ``{model_name}``/``{version}`` (Requirement 14).

    Requires a client ``Idempotency-Key`` header (Requirement 19.11) and the
    ``request_id`` of the ``PENDING`` Promotion_Request as a query parameter.
    Returns a ``200`` :class:`ModelVersionResponse` once the gate (one
    authorized approval, or Four_Eyes for PRODUCTION/rollback) is satisfied
    and the transition is applied, or a ``202`` :class:`PromotionRequestResponse`
    while the request remains ``PENDING`` awaiting further approvals
    (Requirement 14.4). Domain failures map per design.md Section 13: an
    unknown Promotion_Request or Model_Version to ``404``, a settled
    (non-``PENDING``) request to ``409``, and an automated principal
    attempting to approve a ``PRODUCTION`` transition to ``403`` (Requirements
    14.7, 21.2).
    """
    try:
        result = await service.approve(
            request_id=request_id,
            approver=body.approver,
            approver_kind=body.approver_kind,
            correlation_id=_resolve_correlation_id(correlation_id),
            reason=body.reason,
        )
    except PromotionRequestNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PromotionRequestNotPendingError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except AutomatedApprovalNotPermittedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ModelVersionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    if isinstance(result, PromotionRequest):
        response.status_code = status.HTTP_202_ACCEPTED
        return PromotionRequestResponse.from_domain(result)
    return ModelVersionResponse.from_domain(result)


@router.post(
    "/{model_name}/versions/{version}/reject",
    response_model=PromotionRequestResponse,
)
async def reject_transition(
    model_name: str,
    version: int,
    body: ApprovalRequestSchema,
    request_id: int = Query(..., description="The Promotion_Request id to reject."),
    idempotency_key: str = Header(alias="Idempotency-Key"),
    correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    service: ModelRegistryService = Depends(get_model_registry_service),
) -> PromotionRequestResponse:
    """Reject a pending Promotion_Request for ``{model_name}``/``{version}`` (Requirement 14.5).

    Requires a client ``Idempotency-Key`` header (Requirement 19.11), the
    ``request_id`` of the ``PENDING`` Promotion_Request as a query parameter,
    and a non-empty ``reason`` in the body (recorded as the rejection reason,
    Requirement 20.5). The Model_Version's Lifecycle_State is left unchanged
    (Requirement 14.5). Domain failures map per design.md Section 13: an
    unknown Promotion_Request to ``404`` and a settled (non-``PENDING``)
    request to ``409``.
    """
    if not body.reason:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A rejection reason is required.",
        )
    try:
        result = await service.reject(
            request_id=request_id,
            approver=body.approver,
            approver_kind=body.approver_kind,
            reason=body.reason,
            correlation_id=_resolve_correlation_id(correlation_id),
        )
    except PromotionRequestNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PromotionRequestNotPendingError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return PromotionRequestResponse.from_domain(result)


@router.post(
    "/{model_name}/versions/{version}/rollback",
    response_model=PromotionRequestResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_rollback(
    model_name: str,
    version: int,
    body: RollbackRequestSchema,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    service: ModelRegistryService = Depends(get_model_registry_service),
) -> PromotionRequestResponse:
    """Request a rollback of ``{model_name}``/``{version}`` to PRODUCTION (Requirement 15).

    Requires a client ``Idempotency-Key`` header (Requirement 19.11). A
    rollback is always governed by Four_Eyes (Requirement 15.2), so it never
    applies immediately: this endpoint always parks a ``PENDING`` rollback
    Promotion_Request and returns it with ``202 Accepted``; ``approve`` later
    applies it once two distinct human approvers are recorded. Domain
    failures map per design.md Section 13: unknown Model_Version to ``404``,
    a version that is not currently ``DEPRECATED`` (the only legal source of
    the rollback edge) to ``409``, and a version that was never in
    ``PRODUCTION`` to ``409`` (Requirement 15.4).
    """
    try:
        result = await service.rollback(
            model_name=model_name,
            version=version,
            requester=body.requester,
            justification=body.justification,
            correlation_id=_resolve_correlation_id(correlation_id),
        )
    except ModelVersionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except IllegalTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except NeverInProductionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return PromotionRequestResponse.from_domain(result)
