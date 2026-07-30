"""Configuration for the backtesting-engine service.

Extends the shared :class:`BaseServiceSettings` with this service's own
database, the three upstream services it depends on (Market Data, Model
Registry, Feature Store), and the local result-artifact directory.
"""

from __future__ import annotations

from pydantic import AnyHttpUrl, Field, PostgresDsn

from aqros_core.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    """backtesting-engine settings (override defaults via AQROS_* env vars)."""

    service_name: str = "backtesting-engine"
    port: int = 8010

    database_url: PostgresDsn = Field(
        default=PostgresDsn(
            "postgresql+asyncpg://aqros:aqros@localhost:5437/aqros_backtesting_engine"
        ),
        description="Async SQLAlchemy DSN for the backtesting-engine database.",
    )
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_echo: bool = False

    market_data_base_url: AnyHttpUrl = Field(
        default=AnyHttpUrl("http://localhost:8002"),
        description="Base URL of the Market Data Service REST API.",
    )
    model_registry_base_url: AnyHttpUrl = Field(
        default=AnyHttpUrl("http://localhost:8004"),
        description="Base URL of the Model Registry Service REST API.",
    )
    feature_store_base_url: AnyHttpUrl = Field(
        default=AnyHttpUrl("http://localhost:8003"),
        description="Base URL of the Feature Store Service REST API.",
    )
    upstream_request_timeout_seconds: float = 30.0

    artifact_dir: str = "/data/backtesting-engine/artifacts"
