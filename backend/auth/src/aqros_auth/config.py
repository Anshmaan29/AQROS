"""Configuration for the auth service."""

from __future__ import annotations

from aqros_core.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    """auth settings (override defaults via AQROS_* env vars)."""

    service_name: str = "auth"
    port: int = 8001
