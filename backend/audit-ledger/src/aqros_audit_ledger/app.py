"""ASGI application for the audit-ledger service.

The ledger is the platform's tamper-evident record of who did what. It is
append-only by construction: the repository port exposes no update or delete, so
CLAUDE.md §7.8 ("never modify the WORM audit ledger") is a property of the code
rather than a convention.

The service is internal-only — it is not in the gateway's public route set.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from aqros_audit_ledger.adapters.db import create_engine, create_session_factory, ping
from aqros_audit_ledger.adapters.repository import SqlAlchemyLedgerRepository
from aqros_audit_ledger.api.routes import audit
from aqros_audit_ledger.config import Settings
from aqros_audit_ledger.domain.service import AuditLedgerService
from aqros_core.app import create_app
from aqros_core.health import HealthRegistry

_logger = structlog.get_logger(__name__)

settings = Settings()

engine = create_engine(settings)

health_registry = HealthRegistry()
health_registry.register("database", lambda: ping(engine))


def build_app(repository: object | None = None) -> FastAPI:
    """Build the app.

    ``repository`` is injectable so tests can supply the in-memory adapter
    without a database. Production leaves it None and gets Postgres.
    """
    base_app = create_app(settings, health=health_registry)
    base_lifespan = base_app.router.lifespan_context

    @asynccontextmanager
    async def combined_lifespan(app: FastAPI) -> AsyncIterator[None]:
        session_factory = create_session_factory(engine)
        resolved = (
            repository if repository is not None else SqlAlchemyLedgerRepository(session_factory)
        )
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.repository = resolved
        app.state.ledger_service = AuditLedgerService(resolved)  # type: ignore[arg-type]

        async with base_lifespan(app):
            yield

        await engine.dispose()

    base_app.router.lifespan_context = combined_lifespan
    base_app.include_router(audit.router)
    return base_app


app = build_app()
