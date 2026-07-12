"""Configuration for the feature-store service."""

from __future__ import annotations

from aqros_core.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    """feature-store settings (override defaults via AQROS_* env vars)."""

    service_name: str = "feature-store"
    port: int = 8003
