"""Configuration for the risk-engine service."""

from __future__ import annotations

from aqros_core.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    """risk-engine settings (override defaults via AQROS_* env vars)."""

    service_name: str = "risk-engine"
    port: int = 8005
