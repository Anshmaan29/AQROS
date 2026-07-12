"""ASGI application for the audit-ledger service (health-only in Phase 0)."""

from __future__ import annotations

from aqros_audit_ledger.config import Settings
from aqros_core.app import create_app

settings = Settings()
app = create_app(settings)
