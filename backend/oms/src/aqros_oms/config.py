from __future__ import annotations

from pydantic import Field, PostgresDsn

from aqros_core.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "oms"
    port: int = 8015

    database_url: PostgresDsn = Field(
        default=PostgresDsn("postgresql+asyncpg://aqros:aqros@localhost:5444/aqros_oms"),
    )
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_echo: bool = False

    risk_base_url: str = Field(default="http://localhost:8005")
    risk_request_timeout_seconds: float = 10.0

    portfolio_base_url: str = Field(default="http://localhost:8006")
    portfolio_request_timeout_seconds: float = 10.0

    max_order_quantity: int = Field(default=1_000_000, description="Maximum order quantity.")
    max_order_value: float = Field(
        default=100_000_000.0, description="Maximum order notional value."
    )
    max_open_orders_per_portfolio: int = Field(
        default=100, description="Maximum open orders per portfolio."
    )

    outbox_poll_interval_seconds: float = 1.0
    outbox_batch_size: int = 50
    outbox_max_retries: int = 5
    outbox_retention_hours: int = 72
