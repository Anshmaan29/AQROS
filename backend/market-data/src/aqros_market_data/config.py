"""Configuration for the market-data service."""

from __future__ import annotations

from aqros_core.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    """market-data settings (override defaults via AQROS_* env vars)."""

    service_name: str = "market-data"
    port: int = 8002
