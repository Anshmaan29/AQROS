"""Configuration for the market-data service.

Extends the shared :class:`BaseServiceSettings` with the database connection
and ingestion-provider settings this service needs. Everything is loaded from
environment variables (prefix ``AQROS_``) or a ``.env`` file and validated at
construction time, so a misconfigured deployment fails fast at startup.
"""

from __future__ import annotations

from pydantic import Field, PostgresDsn

from aqros_core.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    """market-data settings (override defaults via AQROS_* env vars)."""

    service_name: str = "market-data"
    port: int = 8002

    # --- Database -----------------------------------------------------
    # Async DSN used by the app (asyncpg driver). Defaults to the local
    # docker-compose Postgres instance for a friction-free dev inner loop.
    database_url: PostgresDsn = Field(
        default=PostgresDsn("postgresql+asyncpg://aqros:aqros@localhost:5432/aqros_market_data"),
        description="Async SQLAlchemy DSN for the market-data database.",
    )
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_echo: bool = False

    # --- Ingestion provider ---------------------------------------------
    # Default historical OHLCV provider. "yfinance" needs no API key, which
    # is why it is the MVP default (docs/Execution_Blueprint.md §7.3: the
    # vendor sits behind a MarketDataProvider interface so it is a swap, not
    # a rewrite, when Alpaca/Polygon/TwelveData are added later).
    market_data_provider: str = "yfinance"
    ingestion_request_timeout_seconds: float = 30.0
    ingestion_max_retries: int = 3
    ingestion_retry_backoff_seconds: float = 1.0
