"""ASGI application for the model-registry service (health-only in Phase 0)."""

from __future__ import annotations

from aqros_core.app import create_app
from aqros_model_registry.config import Settings

settings = Settings()
app = create_app(settings)
