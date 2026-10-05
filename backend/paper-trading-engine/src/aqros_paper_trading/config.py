from __future__ import annotations

from decimal import Decimal

from pydantic import Field, PostgresDsn

from aqros_core.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "paper-trading-engine"
    port: int = 8012

    database_url: PostgresDsn = Field(
        default=PostgresDsn("postgresql+asyncpg://aqros:aqros@localhost:5442/aqros_paper_trading"),
    )
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_echo: bool = False

    oms_base_url: str = Field(default="http://localhost:8015")
    oms_request_timeout_seconds: float = 10.0

    portfolio_base_url: str = Field(default="http://localhost:8006")
    portfolio_request_timeout_seconds: float = 10.0

    risk_base_url: str = Field(default="http://localhost:8005")
    risk_request_timeout_seconds: float = 10.0

    slippage_model: str = "linear"
    slippage_linear_pct: float = 0.001
    slippage_sqrt_pct: float = 0.0005
    slippage_base_pct: float = 0.0001
    slippage_min_pct: float = 0.00001
    slippage_max_pct: float = 0.01

    # Commission is money, so it is configured as Decimal rather than float.
    # A float commission would silently introduce binary rounding error into
    # fill P&L, which then diverges from the live engine and breaks parity.
    commission_model: str = "per_share"
    commission_per_share_rate: Decimal = Decimal("0.005")
    commission_pct_rate: Decimal = Decimal("0.001")
    commission_min: Decimal = Decimal("1.00")
    commission_max: Decimal = Decimal("100.00")

    latency_base_delay_ms: float = 50.0
    latency_jitter_ms: float = 25.0
    latency_min_delay_ms: float = 10.0

    outbox_poll_interval_seconds: float = 1.0
    outbox_batch_size: int = 50
    outbox_max_retries: int = 5
    outbox_retention_hours: int = 72
