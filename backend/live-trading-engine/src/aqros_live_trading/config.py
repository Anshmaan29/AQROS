from __future__ import annotations

from pydantic import Field, PostgresDsn

from aqros_core.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "live-trading-engine"
    port: int = 8013

    database_url: PostgresDsn = Field(
        default=PostgresDsn("postgresql+asyncpg://aqros:aqros@localhost:5443/aqros_live_trading"),
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

    paper_trading_base_url: str = Field(default="http://localhost:8012")
    paper_trading_request_timeout_seconds: float = 10.0

    broker_adapter: str = Field(
        default="paper", description="Broker adapter to use: paper, alpaca, ibkr"
    )
    broker_api_key: str = Field(default="", description="Broker API key")
    broker_api_secret: str = Field(default="", description="Broker API secret")
    broker_base_url: str = Field(default="", description="Broker base URL")
    broker_websocket_url: str = Field(default="", description="Broker WebSocket URL")

    heartbeat_interval_seconds: float = Field(
        default=5.0, description="Connection heartbeat interval"
    )
    heartbeat_timeout_seconds: float = Field(
        default=15.0, description="Heartbeat timeout before reconnect"
    )
    reconnect_base_delay_seconds: float = Field(default=1.0, description="Initial reconnect delay")
    reconnect_max_delay_seconds: float = Field(default=60.0, description="Maximum reconnect delay")
    reconnect_max_attempts: int = Field(default=10, description="Maximum reconnect attempts")
    reconnect_jitter: float = Field(default=0.1, description="Reconnect jitter factor")

    kill_switch_enabled: bool = Field(default=True, description="Enable kill switch")
    kill_switch_auto_trigger_on_disconnect_seconds: float = Field(
        default=0.0,
        description="Auto-trigger kill switch after N seconds of disconnect (0 = disabled)",
    )

    max_position_size: int = Field(default=1_000_000, description="Maximum position size")
    max_order_rate_per_second: float = Field(default=10.0, description="Max orders per second")
    max_daily_orders: int = Field(default=10000, description="Max daily orders")

    position_sync_interval_seconds: float = Field(
        default=30.0, description="Position sync interval"
    )
    account_sync_interval_seconds: float = Field(default=60.0, description="Account sync interval")

    trading_calendar_timezone: str = Field(default="America/New_York")

    outbox_poll_interval_seconds: float = 1.0
    outbox_batch_size: int = 50
    outbox_max_retries: int = 5
    outbox_retention_hours: int = 72
