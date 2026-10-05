from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class PortfolioCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    initial_cash: Decimal = Field(default=Decimal("1000000"), gt=0)
    correlation_id: str = ""


class PortfolioCreateResponse(BaseModel):
    portfolio_id: str
    name: str
    total_equity: Decimal
    cash: Decimal
    status: str


class PortfolioResponse(BaseModel):
    portfolio_id: str
    name: str
    status: str
    total_equity: Decimal
    cash_available: Decimal
    cash_reserved: Decimal
    market_value: Decimal
    gross_exposure: Decimal
    gross_exposure_pct: float
    net_exposure: Decimal
    net_exposure_pct: float
    leverage: float
    position_count: int
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    daily_pnl: Decimal
    daily_return_pct: float
    drawdown_pct: float
    peak_equity: Decimal
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class PositionCreateRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=32)
    direction: str = Field(default="long", pattern="^(long|short)$")
    quantity: Decimal = Field(..., gt=0)
    entry_price: Decimal = Field(..., gt=0)
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    sector: str | None = None
    beta: float = 1.0
    volatility: float = 0.0
    avg_daily_volume: float = 0.0
    correlation_id: str = ""


class PositionCloseRequest(BaseModel):
    exit_price: Decimal = Field(..., gt=0)
    correlation_id: str = ""


class PositionModifyRequest(BaseModel):
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    correlation_id: str = ""


class PositionResponse(BaseModel):
    position_id: str
    portfolio_id: str
    symbol: str
    direction: str
    quantity: Decimal
    avg_entry_price: Decimal
    current_price: Decimal
    market_value: Decimal
    unrealized_pnl: Decimal
    unrealized_pnl_pct: float
    realized_pnl: Decimal
    total_pnl: Decimal
    status: str
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    sector: str | None = None
    beta: float = 1.0
    volatility: float = 0.0
    opened_at: datetime | None = None
    closed_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class PnLResponse(BaseModel):
    portfolio_id: str
    total_equity: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    daily_pnl: Decimal
    total_pnl: Decimal
    daily_return_pct: float
    as_of: datetime


class EquityCurveResponse(BaseModel):
    points: list[EquityCurvePointResponse]


class EquityCurvePointResponse(BaseModel):
    timestamp: datetime
    equity: Decimal
    cash: Decimal
    market_value: Decimal
    daily_pnl: Decimal


class PortfolioStatisticsResponse(BaseModel):
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl: Decimal = Decimal("0")
    gross_profit: Decimal = Decimal("0")
    gross_loss: Decimal = Decimal("0")
    max_drawdown_pct: float = 0.0
    current_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    profit_factor: float = 0.0
    win_rate: float = 0.0
    avg_win: Decimal = Decimal("0")
    avg_loss: Decimal = Decimal("0")
    largest_win: Decimal = Decimal("0")
    largest_loss: Decimal = Decimal("0")
    total_fees: Decimal = Decimal("0")


class ExposureResponse(BaseModel):
    total_equity: Decimal
    cash: Decimal
    gross_exposure: Decimal
    net_exposure: Decimal
    gross_exposure_pct: float
    net_exposure_pct: float
    leverage: float
    position_count: int
    sector_exposures: dict[str, Decimal]
    symbol_exposures: dict[str, Decimal]
    concentration_risk: float


class CashUpdateRequest(BaseModel):
    amount: Decimal = Field(..., description="Positive for deposit, negative for withdrawal")
    correlation_id: str = ""


class RebalanceRequest(BaseModel):
    target_allocations: dict[str, float] | None = None
    reason: str = "manual"
    correlation_id: str = ""


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "portfolio-engine"
    database: bool = True
