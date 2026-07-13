"""Configuration for the feature-store service.

Extends the shared :class:`BaseServiceSettings` with this service's own
database connection and the Market Data Service's base URL. Everything is
loaded from environment variables (prefix ``AQROS_``) or a ``.env`` file and
validated at construction time, so a misconfigured deployment fails fast at
startup — same discipline as ``aqros_market_data.config``.
"""

from __future__ import annotations

from pydantic import AnyHttpUrl, Field, PostgresDsn

from aqros_core.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    """feature-store settings (override defaults via AQROS_* env vars)."""

    service_name: str = "feature-store"
    port: int = 8003

    # --- Database (owned exclusively by this service) -------------------
    database_url: PostgresDsn = Field(
        default=PostgresDsn("postgresql+asyncpg://aqros:aqros@localhost:5433/aqros_feature_store"),
        description="Async SQLAlchemy DSN for the feature-store database.",
    )
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_echo: bool = False

    # --- Market Data Service client --------------------------------------
    # The Feature Store never touches market-data's database (CLAUDE.md
    # §7.9): it reads OHLCV bars exclusively through market-data's published
    # REST API. This is the one seam that boundary is crossed at.
    market_data_base_url: AnyHttpUrl = Field(
        default=AnyHttpUrl("http://localhost:8002"),
        description="Base URL of the Market Data Service's REST API.",
    )
    market_data_request_timeout_seconds: float = 30.0
    market_data_max_retries: int = 3
    market_data_retry_backoff_seconds: float = 1.0
    market_data_page_size: int = 1000

    # --- Feature engineering pipeline ------------------------------------
    # How many days before an incremental run's high-water mark to re-fetch
    # bars from, so windowed indicators (e.g. a 26-bar MACD) have enough
    # trailing history to compute correctly for the newly-added bars.
    feature_lookback_buffer_days: int = 90
