from __future__ import annotations

from pydantic import AnyHttpUrl, Field, PostgresDsn

from aqros_core.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "risk-engine"
    port: int = 8005

    database_url: PostgresDsn = Field(
        default=PostgresDsn("postgresql+asyncpg://aqros:aqros@localhost:5437/aqros_risk_engine"),
    )
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_echo: bool = False

    portfolio_base_url: AnyHttpUrl = Field(
        default=AnyHttpUrl("http://localhost:8006"),
    )
    portfolio_request_timeout_seconds: float = 10.0

    market_data_base_url: AnyHttpUrl = Field(
        default=AnyHttpUrl("http://localhost:8002"),
    )
    market_data_request_timeout_seconds: float = 10.0

    default_account_id: str = "default"
    default_portfolio_equity: float = 1_000_000.0

    max_position_size_pct: float = Field(
        default=5.0, description="Maximum position size as % of portfolio equity."
    )
    max_portfolio_exposure_pct: float = Field(
        default=80.0, description="Maximum gross portfolio exposure as % of equity."
    )
    max_daily_loss_pct: float = Field(
        default=2.0, description="Maximum daily loss as % of portfolio equity."
    )
    max_drawdown_pct: float = Field(
        default=15.0, description="Maximum drawdown from peak as % of equity."
    )
    max_leverage: float = Field(default=2.0, description="Maximum leverage multiple.")
    max_concurrent_positions: int = Field(
        default=20, description="Maximum number of concurrent positions."
    )
    max_symbol_exposure_pct: float = Field(
        default=10.0, description="Maximum single-symbol exposure as % of equity."
    )
    max_sector_exposure_pct: float = Field(
        default=25.0, description="Maximum single-sector exposure as % of equity."
    )
    min_volatility: float = Field(
        default=0.001, description="Minimum annualized volatility threshold."
    )
    max_volatility: float = Field(
        default=0.6, description="Maximum annualized volatility threshold."
    )
    min_avg_daily_volume: float = Field(
        default=100_000.0, description="Minimum average daily volume in USD."
    )
    min_confidence: float = Field(
        default=0.3, description="Minimum prediction confidence to trade."
    )
    min_prediction_quality: float = Field(
        default=0.1, description="Minimum prediction quality score."
    )
    trading_start_hour: int = Field(
        default=9, description="Trading session start hour (24h, exchange timezone)."
    )
    trading_end_hour: int = Field(
        default=16, description="Trading session end hour (24h, exchange timezone)."
    )
    cooldown_minutes: int = Field(
        default=5, description="Cooldown between trades on same instrument (minutes)."
    )
    kelly_fraction: float = Field(
        default=0.25, description="Kelly fraction (fraction of full Kelly to use)."
    )
    risk_per_trade_pct: float = Field(
        default=0.5, description="Maximum risk per trade as % of equity."
    )
    default_stop_loss_pct: float = Field(
        default=2.0, description="Default stop-loss as % of entry price."
    )
    default_atr_period: int = Field(default=14, description="Default ATR period.")

    circuit_breaker_loss_pct: float = Field(
        default=3.0, description="Daily loss % that triggers circuit breaker."
    )
    circuit_breaker_consecutive_losses: int = Field(
        default=3, description="Consecutive losing trades that trigger circuit breaker."
    )
    circuit_breaker_cooldown_minutes: int = Field(
        default=60, description="Circuit breaker cooldown in minutes."
    )

    outbox_poll_interval_seconds: float = 1.0
    outbox_batch_size: int = 50
    outbox_max_retries: int = 5
    outbox_retention_hours: int = 72
