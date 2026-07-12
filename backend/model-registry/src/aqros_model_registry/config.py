"""Configuration for the model-registry service."""

from __future__ import annotations

from aqros_core.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    """model-registry settings (override defaults via AQROS_* env vars)."""

    service_name: str = "model-registry"
    port: int = 8004
