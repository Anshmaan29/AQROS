"""Configuration for the api-gateway service."""

from __future__ import annotations

from aqros_core.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    """api-gateway settings (override defaults via AQROS_* env vars)."""

    service_name: str = "api-gateway"
    port: int = 8000
