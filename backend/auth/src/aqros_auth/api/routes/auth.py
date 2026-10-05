"""Auth service HTTP surface: login, identity, RBAC checks, four-eyes approvals."""

from __future__ import annotations

from datetime import timedelta
from typing import cast

import structlog
from fastapi import APIRouter, HTTPException, Request, status

from aqros_auth.api.deps import AuthServiceDep, CurrentPrincipal
from aqros_auth.api.schemas import (
    ApprovalDecisionBody,
    ApprovalRequestBody,
    ApprovalResponse,
    AuthorizationCheckBody,
    AuthorizationCheckResponse,
    CreateUserRequest,
    LoginRequest,
    PrincipalResponse,
    TokenResponse,
    UserResponse,
)
from aqros_auth.domain.approvals import (
    ApprovalStatus,
    InvalidTransitionError,
    SeparationOfDutiesError,
)
from aqros_auth.domain.policy import Permission, Role
from aqros_auth.domain.ports import UserRecord
from aqros_auth.domain.service import (
    AuthError,
    InvalidCredentialsError,
    UserExistsError,
    UserNotFoundError,
)

_logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/v1")


def _correlation_id(request: Request) -> str:
    return str(getattr(request.state, "correlation_id", ""))


async def _audit(
    request: Request,
    event_type: str,
    principal_id: str,
    action: str,
    resource: str,
    outcome: str,
    reason: str = "",
    details: dict[str, object] | None = None,
) -> None:
    """Record an audit event.

    Awaited rather than fire-and-forget: an authorization decision that is not
    recorded is not auditable, and "we logged it eventually" is not a defensible
    answer to "who approved this?". Failures are swallowed — a ledger outage
    must not lock every human out of the platform, including out of the steps
    needed to recover — but they are logged loudly.
    """
    sink = getattr(request.app.state, "audit_sink", None)
    if sink is None:
        return
    try:
        await sink.record(
            event_type=event_type,
            principal_id=principal_id,
            action=action,
            resource=resource,
            outcome=outcome,
            reason=reason,
            correlation_id=_correlation_id(request),
            details=details or {},
        )
    except Exception as exc:
        _logger.error("audit.record_failed", event_type=event_type, error=str(exc))


def default_ttl(request: Request) -> timedelta:
    """The configured default approval TTL."""
    return cast(timedelta, request.app.state.default_approval_ttl)


@router.post("/auth/login", response_model=TokenResponse, tags=["auth"])
async def login(payload: LoginRequest, request: Request, service: AuthServiceDep) -> TokenResponse:
    """Exchange credentials for an access token."""
    try:
        issued = await service.authenticate(payload.username, payload.password)
    except InvalidCredentialsError as exc:
        await _audit(
            request,
            "auth.login",
            payload.username,
            "login",
            payload.username,
            outcome="DENIED",
            reason=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    await _audit(
        request, "auth.login", issued.principal.principal_id, "login", payload.username, "ALLOWED"
    )
    return TokenResponse(
        access_token=issued.token,
        expires_at=issued.expires_at,
        principal_id=issued.principal.principal_id,
        roles=sorted(r.value for r in issued.principal.roles),
    )


@router.get("/auth/me", response_model=PrincipalResponse, tags=["auth"])
async def me(principal: CurrentPrincipal, service: AuthServiceDep) -> PrincipalResponse:
    """Return the authenticated principal and its effective permissions."""
    permissions = service.permissions_of(principal)
    return PrincipalResponse(
        principal_id=principal.principal_id,
        display_name=principal.display_name,
        roles=sorted(r.value for r in principal.roles),
        is_service=principal.is_service,
        permissions=sorted(p.value for p in permissions),
        four_eyes_permissions=sorted(
            p.value for p in permissions if service.authorize(principal, p).requires_four_eyes
        ),
    )


@router.post("/users", response_model=UserResponse, status_code=201, tags=["identity"])
async def create_user(
    payload: CreateUserRequest,
    request: Request,
    principal: CurrentPrincipal,
    service: AuthServiceDep,
) -> UserResponse:
    """Create a principal. Requires ``identity:manage_users``."""
    decision = service.authorize(principal, Permission.MANAGE_USERS)
    if not decision.allowed:
        await _audit(
            request,
            "identity.create_user",
            principal.principal_id,
            "create_user",
            payload.username,
            "DENIED",
            decision.reason,
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=decision.reason)

    try:
        roles = [Role(r) for r in payload.roles]
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"unknown role in {payload.roles}",
        ) from exc

    try:
        record = await service.create_user(
            username=payload.username,
            display_name=payload.display_name,
            password=payload.password,
            roles=roles,
            is_service=payload.is_service,
        )
    except UserExistsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    await _audit(
        request,
        "identity.create_user",
        principal.principal_id,
        "create_user",
        record.username,
        "ALLOWED",
        details={"roles": [r.value for r in roles]},
    )
    return _user_response(record)


@router.get("/users", response_model=list[UserResponse], tags=["identity"])
async def list_users(principal: CurrentPrincipal, service: AuthServiceDep) -> list[UserResponse]:
    """List principals. Requires ``identity:manage_users``."""
    decision = service.authorize(principal, Permission.MANAGE_USERS)
    if not decision.allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=decision.reason)
    return [_user_response(r) for r in await service.list_users()]


def _user_response(record: UserRecord) -> UserResponse:
    return UserResponse(
        principal_id=record.principal_id,
        username=record.username,
        display_name=record.display_name,
        roles=sorted(r.value for r in record.roles),
        is_service=record.is_service,
        is_active=record.is_active,
    )


@router.post("/authz/check", response_model=AuthorizationCheckResponse, tags=["authz"])
async def check_authorization(
    payload: AuthorizationCheckBody,
    principal: CurrentPrincipal,
    service: AuthServiceDep,
) -> AuthorizationCheckResponse:
    """Report whether a principal may exercise a permission.

    Always returns a decision with a reason rather than 403, so a caller can
    explain *why* an action is unavailable — including "this needs a second
    approver", which is the answer an operator needs at a four-eyes wall.
    """
    try:
        permission = Permission(payload.permission)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"unknown permission: {payload.permission}",
        ) from exc

    target = principal
    # "self" is the common case (a client asking "may I do this?") and must not
    # require admin rights to ask about its own permissions.
    wants_self = payload.principal_id in {"self", principal.principal_id}
    if not wants_self:
        # Checking someone else's permissions requires admin.
        if not service.authorize(principal, Permission.MANAGE_USERS).allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="may only inspect your own permissions",
            )
        users = await service.list_users()
        match = next((u for u in users if u.principal_id == payload.principal_id), None)
        if match is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"unknown principal: {payload.principal_id}",
            )
        target = match.to_principal()

    decision = service.authorize(target, permission)
    return AuthorizationCheckResponse(
        allowed=decision.allowed,
        reason=decision.reason,
        requires_four_eyes=decision.requires_four_eyes,
    )


# ---------------------------------------------------------------------------
# Four-eyes approvals
# ---------------------------------------------------------------------------
@router.post("/approvals", response_model=ApprovalResponse, status_code=201, tags=["approvals"])
async def create_approval(
    payload: ApprovalRequestBody,
    request: Request,
    principal: CurrentPrincipal,
    service: AuthServiceDep,
) -> ApprovalResponse:
    """Open a four-eyes approval request."""
    approval = await service.request_approval(
        requester=principal,
        action=payload.action,
        resource=payload.resource,
        payload={**payload.payload, "reason": payload.reason},
        ttl=timedelta(hours=payload.ttl_hours) if payload.ttl_hours else default_ttl(request),
    )
    await _audit(
        request,
        "approval.requested",
        principal.principal_id,
        payload.action,
        payload.resource,
        "PENDING",
    )
    return ApprovalResponse(**approval.snapshot())


@router.get("/approvals", response_model=list[ApprovalResponse], tags=["approvals"])
async def list_approvals(
    principal: CurrentPrincipal,
    service: AuthServiceDep,
    status_filter: str | None = None,
) -> list[ApprovalResponse]:
    """List approval requests, optionally filtered by status."""
    if not service.permissions_of(principal) & {
        Permission.READ_AUDIT,
        Permission.CHANGE_RISK_LIMIT,
        Permission.PROMOTE_MODEL,
    }:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="not permitted to view approvals"
        )
    parsed: ApprovalStatus | None = None
    if status_filter:
        try:
            parsed = ApprovalStatus(status_filter.upper())
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"unknown status: {status_filter}",
            ) from exc
    items = await service.list_approvals(parsed)
    return [ApprovalResponse(**a.snapshot()) for a in items]


@router.get("/approvals/{request_id}", response_model=ApprovalResponse, tags=["approvals"])
async def get_approval(
    request_id: str, principal: CurrentPrincipal, service: AuthServiceDep
) -> ApprovalResponse:
    """Fetch one approval request."""
    approval = await service.get_approval(request_id)
    if approval is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="approval request not found"
        )
    # A requester can always see their own request.
    if approval.requester_id != principal.principal_id and not (
        service.permissions_of(principal) & {Permission.READ_AUDIT}
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="not permitted to view this request"
        )
    return ApprovalResponse(**approval.snapshot())


@router.post(
    "/approvals/{request_id}/approve",
    response_model=ApprovalResponse,
    tags=["approvals"],
)
async def approve(
    request_id: str,
    payload: ApprovalDecisionBody,
    request: Request,
    principal: CurrentPrincipal,
    service: AuthServiceDep,
) -> ApprovalResponse:
    """Approve a pending request.

    Refuses self-approval and requires an approver role. This is the endpoint
    that CLAUDE.md §7.3/§7.4 depend on.
    """
    try:
        approval = await service.approve_request(
            request_id=request_id, approver=principal, reason=payload.reason
        )
    except UserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except SeparationOfDutiesError as exc:
        # Denied by policy (self-approval, or no approver role) — not a state
        # conflict, so 403 rather than 409.
        await _audit(
            request,
            "approval.approved",
            principal.principal_id,
            "approve",
            request_id,
            "DENIED",
            str(exc),
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except (InvalidTransitionError, AuthError) as exc:
        await _audit(
            request,
            "approval.approved",
            principal.principal_id,
            "approve",
            request_id,
            "DENIED",
            str(exc),
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    await _audit(
        request, "approval.approved", principal.principal_id, "approve", request_id, "ALLOWED"
    )
    return ApprovalResponse(**approval.snapshot())


@router.post("/approvals/{request_id}/reject", response_model=ApprovalResponse, tags=["approvals"])
async def reject(
    request_id: str,
    payload: ApprovalDecisionBody,
    request: Request,
    principal: CurrentPrincipal,
    service: AuthServiceDep,
) -> ApprovalResponse:
    """Reject a pending request. Rejection is final."""
    try:
        approval = await service.reject_request(
            request_id=request_id, actor=principal, reason=payload.reason
        )
    except UserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (InvalidTransitionError, AuthError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await _audit(
        request, "approval.rejected", principal.principal_id, "reject", request_id, "ALLOWED"
    )
    return ApprovalResponse(**approval.snapshot())


@router.post("/approvals/{request_id}/execute", response_model=ApprovalResponse, tags=["approvals"])
async def execute(
    request_id: str,
    payload: ApprovalDecisionBody,
    request: Request,
    principal: CurrentPrincipal,
    service: AuthServiceDep,
) -> ApprovalResponse:
    """Execute an approved request.

    Only an APPROVED request can execute — this is the gate that keeps an
    unapproved limit change or model promotion away from real capital.
    """
    try:
        approval = await service.execute_approved(
            request_id=request_id, actor=principal, reason=payload.reason
        )
    except UserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (InvalidTransitionError, AuthError, ValueError) as exc:
        await _audit(
            request,
            "approval.executed",
            principal.principal_id,
            "execute",
            request_id,
            "DENIED",
            str(exc),
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    await _audit(
        request, "approval.executed", principal.principal_id, "execute", request_id, "ALLOWED"
    )
    return ApprovalResponse(**approval.snapshot())
