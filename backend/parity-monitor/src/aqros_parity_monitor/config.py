"""Configuration for the parity-monitor service.

Extends :class:`BaseServiceSettings` with connections to the offline feature
store (Feature Store REST API), the online feature store (Redis), event bus
(Kafka/InProcess), and all parity-check tuning parameters.
"""

from __future__ import annotations

from pydantic import AnyHttpUrl, Field, RedisDsn

from aqros_core.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    """parity-monitor settings (override defaults via AQROS_* env vars)."""

    service_name: str = "parity-monitor"
    port: int = 8021

    # --- Feature Store (offline) REST client -----------------------------
    feature_store_base_url: AnyHttpUrl = Field(
        default=AnyHttpUrl("http://localhost:8003"),
        description="Base URL of the Feature Store Service's REST API.",
    )
    feature_store_request_timeout_seconds: float = 30.0

    # --- Redis (online feature store) -------------------------------------
    redis_url: RedisDsn = Field(
        default=RedisDsn("redis://localhost:6379/1"),
        description="Redis connection string for reading online features.",
    )
    redis_pool_size: int = 5
    redis_connection_timeout_s: float = 5.0

    # --- Parity check tuning ----------------------------------------------
    default_tolerance: float = Field(
        default=1e-6,
        description="Default relative tolerance for numeric feature comparison.",
    )
    max_feature_age_seconds: float = Field(
        default=300.0,
        description="Max age of an online feature value before it is considered stale (seconds).",
    )
    batch_size: int = Field(
        default=100,
        description="Number of symbols to check in a single batch-parity run.",
    )
    parallel_workers: int = Field(
        default=4,
        description="Number of parallel workers for batch parity checks.",
    )

    # --- History retention ------------------------------------------------
    history_retention_days: int = Field(
        default=90,
        description="Number of days to retain parity report history.",
    )

    # --- Feature toggles --------------------------------------------------
    metrics_enabled: bool = True
    event_publishing_enabled: bool = True
