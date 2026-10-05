"""Configuration for the audit-ledger service."""

from __future__ import annotations

from pydantic import Field, PostgresDsn

from aqros_core.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    """audit-ledger settings (override defaults via AQROS_* env vars)."""

    service_name: str = "audit-ledger"
    port: int = 8007

    database_url: PostgresDsn = Field(
        default=PostgresDsn("postgresql+asyncpg://aqros:aqros@localhost:5439/aqros_audit_ledger"),
    )
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_echo: bool = False

    # How often the chain is verified proactively. Verification is cheap relative
    # to the cost of discovering tampering during an incident.
    verify_interval_seconds: int = 900

    # Maximum entries returned by a single verify call, so a very large ledger
    # cannot produce an unbounded response.
    verify_max_entries: int = 100_000
