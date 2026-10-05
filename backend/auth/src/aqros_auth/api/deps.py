"""FastAPI dependency wiring for the auth service.

Every dependency reads from ``app.state``, set once in the lifespan, so tests
can substitute fakes without touching route code (CLAUDE.md §3).
"""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aqros_auth.domain.policy import Principal
from aqros_auth.domain.service import AuthError, AuthService


def get_auth_service(request: Request) -> AuthService:
    """Return the configured auth service."""
    return cast(AuthService, request.app.state.auth_service)


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    """Return the session factory.

    Repositories own their own sessions, so routes depend on the factory rather
    than a session they would have to close.
    """
    return cast("async_sessionmaker[AsyncSession]", request.app.state.session_factory)


async def get_current_principal(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    """Resolve the bearer token to an authenticated principal.

    Fails closed: any missing, malformed, expired, or deactivated-credential
    token results in 401. The specific reason is intentionally not disclosed.
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="expected a Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    service: AuthService = request.app.state.auth_service
    try:
        return await service.authenticate_token(token)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


CurrentPrincipal = Annotated[Principal, Depends(get_current_principal)]
AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
