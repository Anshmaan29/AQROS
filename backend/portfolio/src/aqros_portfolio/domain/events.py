from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class BaseEvent:
    event_id: str
    correlation_id: str
    timestamp: datetime | None


@dataclass(frozen=True)
class PortfolioCreated(BaseEvent):
    portfolio_id: str
    name: str
    initial_cash: Decimal


@dataclass(frozen=True)
class PortfolioUpdated(BaseEvent):
    portfolio_id: str
    name: str
    total_equity: Decimal
    cash: Decimal
    market_value: Decimal
    position_count: int


@dataclass(frozen=True)
class PortfolioFrozen(BaseEvent):
    portfolio_id: str
    reason: str


@dataclass(frozen=True)
class PortfolioClosed(BaseEvent):
    portfolio_id: str
    reason: str


@dataclass(frozen=True)
class PositionOpened(BaseEvent):
    portfolio_id: str
    position_id: str
    symbol: str
    direction: str
    quantity: Decimal
    entry_price: Decimal
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None


@dataclass(frozen=True)
class PositionClosed(BaseEvent):
    portfolio_id: str
    position_id: str
    symbol: str
    direction: str
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal
    realized_pnl: Decimal
    return_pct: float
    holding_period_seconds: float | None = None


@dataclass(frozen=True)
class PositionModified(BaseEvent):
    portfolio_id: str
    position_id: str
    symbol: str
    old_quantity: Decimal
    new_quantity: Decimal
    old_stop_loss: Decimal | None = None
    new_stop_loss: Decimal | None = None
    old_take_profit: Decimal | None = None
    new_take_profit: Decimal | None = None


@dataclass(frozen=True)
class PositionStopLossHit(BaseEvent):
    portfolio_id: str
    position_id: str
    symbol: str
    stop_loss: Decimal
    exit_price: Decimal
    realized_pnl: Decimal


@dataclass(frozen=True)
class PositionTakeProfitHit(BaseEvent):
    portfolio_id: str
    position_id: str
    symbol: str
    take_profit: Decimal
    exit_price: Decimal
    realized_pnl: Decimal


@dataclass(frozen=True)
class PnLCalculated(BaseEvent):
    portfolio_id: str
    total_equity: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    daily_pnl: Decimal
    total_pnl: Decimal
    daily_return_pct: float


@dataclass(frozen=True)
class PortfolioRebalanced(BaseEvent):
    portfolio_id: str
    old_positions: int
    new_positions: int
    trades_executed: int
    reason: str


@dataclass(frozen=True)
class CashDeposited(BaseEvent):
    portfolio_id: str
    amount: Decimal
    new_balance: Decimal


@dataclass(frozen=True)
class CashWithdrawn(BaseEvent):
    portfolio_id: str
    amount: Decimal
    new_balance: Decimal
