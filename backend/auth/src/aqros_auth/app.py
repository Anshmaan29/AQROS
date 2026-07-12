"""ASGI application for the auth service (health-only in Phase 0)."""

from __future__ import annotations

from aqros_auth.config import Settings
from aqros_core.app import create_app

settings = Settings()
app = create_app(settings)
