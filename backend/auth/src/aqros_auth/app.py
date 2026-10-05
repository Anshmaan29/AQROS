"""ASGI application for the auth service.

Auth owns identity and authorization for the platform: RBAC plus the four-eyes
workflow that gates risk-limit changes, model promotion, and arming live
capital (CLAUDE.md §7.3, §7.4). The architecture review calls this a V1
blocker — you cannot safely arm capital without a single authority for who may
approve what.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from aqros_auth.adapters.db import create_engine, create_session_factory, ping
from aqros_auth.adapters.inmemory import RecordingAuditSink
from aqros_auth.adapters.repository import (
    SqlAlchemyApprovalRepository,
    SqlAlchemyUserRepository,
)
from aqros_auth.api.routes import auth
from aqros_auth.config import Settings
from aqros_auth.domain.policy import Role
from aqros_auth.domain.ports import ApprovalRepository, AuditSink, UserRepository
from aqros_auth.domain.service import AuthService, TokenSettings
from aqros_core.app import create_app
from aqros_core.db import schema_check
from aqros_core.health import HealthRegistry

_logger = structlog.get_logger(__name__)

settings = Settings()

engine = create_engine(settings)

health_registry = HealthRegistry()
health_registry.register("database", lambda: ping(engine))


# Connectivity alone is not readiness: an unmigrated database answers
# SELECT 1 happily and then 500s on every real request.
health_registry.register("schema", schema_check(engine))


def _build_app(
    *,
    users: UserRepository | None = None,
    approvals: ApprovalRepository | None = None,
    audit_sink: AuditSink | None = None,
    bootstrap_users: bool | None = None,
) -> FastAPI:
    """Build the auth app.

    The repositories are injectable so tests can supply the in-memory adapters
    without a database, while production uses the Postgres ones. Injection
    happens     here rather than inside the lifespan so there is exactly one place
    that decides which adapter serves.

    Uses the module-level ``health_registry``. An earlier version created a
    second, local registry here, which silently shadowed the module-level one —
    so checks registered at import time never reached the health router.
    """
    base_app = create_app(settings, health=health_registry)
    base_lifespan = base_app.router.lifespan_context

    @asynccontextmanager
    async def combined_lifespan(app: FastAPI) -> AsyncIterator[None]:
        session_factory = create_session_factory(engine)
        resolved_users = users if users is not None else SqlAlchemyUserRepository(session_factory)
        resolved_approvals = (
            approvals if approvals is not None else SqlAlchemyApprovalRepository(session_factory)
        )
        resolved_audit = audit_sink if audit_sink is not None else RecordingAuditSink()

        service = AuthService(
            users=resolved_users,
            approvals=resolved_approvals,
            audit=resolved_audit,
            token_settings=TokenSettings(
                secret=settings.jwt_secret,
                issuer=settings.jwt_issuer,
                audience=settings.jwt_audience,
                ttl=settings.access_token_ttl,
            ),
        )

        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.users = resolved_users
        app.state.approvals = resolved_approvals
        app.state.audit_sink = resolved_audit
        app.state.auth_service = service
        app.state.default_approval_ttl = settings.default_approval_ttl

        # Bootstrap the two roles the platform cannot run without, so the
        # four-eyes workflow is usable immediately. Dev only: a real deployment
        # seeds principals from Vault.
        should_bootstrap = (
            settings.environment.value == "dev" if bootstrap_users is None else bootstrap_users
        )
        if should_bootstrap:
            try:
                for username, role in (("admin", Role.ADMIN), ("committee", Role.COMMITTEE)):
                    if not await resolved_users.exists(username):
                        await service.create_user(
                            username=username,
                            display_name=username.title(),
                            password=f"{username}-dev-password-1234",
                            roles=[role],
                        )
                        _logger.warning(
                            "auth.dev_user_created",
                            username=username,
                            role=role.value,
                            note="dev-only bootstrap user; never do this in staging/prod",
                        )
            except Exception as exc:
                # Startup must not hard-fail just because migrations have not run
                # yet. A service that refuses to boot cannot report that it is
                # not ready, and cannot recover once `make migrate` finishes —
                # it would be stuck crash-looping. The `schema` readiness check
                # already reports the true state; this only skips the convenience
                # bootstrap until the tables exist.
                _logger.warning(
                    "auth.dev_bootstrap_skipped",
                    error=str(exc),
                    hint="run `make migrate` then restart to seed dev users",
                )

        async with base_lifespan(app):
            yield

        await engine.dispose()

    base_app.router.lifespan_context = combined_lifespan
    base_app.include_router(auth.router)
    return base_app


app = _build_app()
