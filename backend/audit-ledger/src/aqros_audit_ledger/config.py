"""Configuration for the audit-ledger service."""

from __future__ import annotations

from aqros_core.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    """audit-ledger settings (override defaults via AQROS_* env vars)."""

    service_name: str = "audit-ledger"
    port: int = 8007
