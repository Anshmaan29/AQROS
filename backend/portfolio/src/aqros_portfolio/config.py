from __future__ import annotations

from pydantic import AnyHttpUrl, Field, PostgresDsn

from aqros_core.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "portfolio-engine"
    port: int = 8006

    database_url: PostgresDsn = Field(
        default=PostgresDsn("postgresql+asyncpg://aqros:aqros@localhost:5438/aqros_portfolio"),
    )
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_echo: bool = False

    risk_base_url: AnyHttpUrl = Field(default=AnyHttpUrl("http://localhost:8005"))
    risk_request_timeout_seconds: float = 10.0

    market_data_base_url: AnyHttpUrl = Field(default=AnyHttpUrl("http://localhost:8002"))
    market_data_request_timeout_seconds: float = 10.0

    default_initial_cash: float = 1_000_000.0

    max_positions: int = Field(default=50, description="Maximum number of open positions.")
    max_symbol_exposure_pct: float = Field(
        default=15.0, description="Max single-symbol exposure as % of equity."
    )
    max_sector_exposure_pct: float = Field(
        default=30.0, description="Max single-sector exposure as % of equity."
    )
    max_portfolio_exposure_pct: float = Field(
        default=90.0, description="Max total exposure as % of equity."
    )
    max_position_size_pct: float = Field(
        default=8.0, description="Max position size as % of equity."
    )
    max_daily_loss_pct: float = Field(default=5.0, description="Max daily loss as % of equity.")
    min_cash_balance: float = Field(
        default=10_000.0, description="Minimum cash balance to maintain."
    )

    outbox_poll_interval_seconds: float = 1.0
    outbox_batch_size: int = 50
    outbox_max_retries: int = 5
    outbox_retention_hours: int = 72
