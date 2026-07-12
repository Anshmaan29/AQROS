"""ASGI application for the api-gateway service (health-only in Phase 0)."""

from __future__ import annotations

from aqros_api_gateway.config import Settings
from aqros_core.app import create_app

settings = Settings()
app = create_app(settings)
