"""Configuration for the portfolio service."""

from __future__ import annotations

from aqros_core.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    """portfolio settings (override defaults via AQROS_* env vars)."""

    service_name: str = "portfolio"
    port: int = 8006
