"""Request/response schemas for the auth service."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    """Credentials presented by a human or service."""

    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=1, max_length=1024)


class TokenResponse(BaseModel):
    """A successfully issued access token."""

    access_token: str
    token_type: str = "Bearer"
    expires_at: datetime
    principal_id: str
    roles: list[str]


class PrincipalResponse(BaseModel):
    """The authenticated principal and its effective permissions."""

    principal_id: str
    display_name: str
    roles: list[str]
    is_service: bool
    permissions: list[str]
    four_eyes_permissions: list[str] = Field(
        default_factory=list,
        description="Permissions this principal may hold but can never exercise alone.",
    )


class CreateUserRequest(BaseModel):
    """Admin request to create a principal."""

    username: str = Field(..., min_length=3, max_length=64)
    display_name: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=1, max_length=1024)
    roles: list[str] = Field(..., min_length=1)
    is_service: bool = False


class UserResponse(BaseModel):
    """A user record, without any secret material."""

    model_config = ConfigDict(from_attributes=True)

    principal_id: str
    username: str
    display_name: str
    roles: list[str]
    is_service: bool
    is_active: bool


class ApprovalRequestBody(BaseModel):
    """Open a four-eyes approval request."""

    action: str = Field(..., description="Protected action, e.g. promote_model.")
    resource: str = Field(..., min_length=1, max_length=256)
    payload: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="", max_length=1024)
    ttl_hours: int | None = Field(default=None, ge=1, le=720)


class ApprovalDecisionBody(BaseModel):
    """Approve or reject a pending request."""

    reason: str = Field(default="", max_length=1024)


class ApprovalResponse(BaseModel):
    """A four-eyes approval request."""

    request_id: str
    action: str
    resource: str
    payload: dict[str, Any]
    requester_id: str
    approver_id: str | None
    status: str
    created_at: str
    expires_at: str
    decided_at: str | None
    executed_at: str | None
    history: list[dict[str, Any]] = Field(default_factory=list)


class AuthorizationCheckBody(BaseModel):
    """Ask whether a principal may exercise a permission."""

    principal_id: str
    permission: str


class AuthorizationCheckResponse(BaseModel):
    """The authorization decision and its reasoning."""

    allowed: bool
    reason: str
    requires_four_eyes: bool


class ErrorResponse(BaseModel):
    """Uniform error envelope."""

    error: str
    detail: str
    correlation_id: str | None = None
