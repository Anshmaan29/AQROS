from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Protocol


class PositionStatus(Enum):
    PENDING = "pending"
    OPEN = "open"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class TradeDirection(Enum):
    LONG = "long"
    SHORT = "short"


class PortfolioStatus(Enum):
    ACTIVE = "active"
    FROZEN = "frozen"
    CLOSED = "closed"


@dataclass(frozen=True)
class CashBalance:
    total: Decimal = Decimal("0")
    reserved: Decimal = Decimal("0")

    @property
    def available(self) -> Decimal:
        return self.total - self.reserved

    def deposit(self, amount: Decimal) -> CashBalance:
        return CashBalance(total=self.total + amount, reserved=self.reserved)

    def withdraw(self, amount: Decimal) -> CashBalance:
        if amount > self.available:
            raise ValueError(f"Insufficient available cash: {self.available} < {amount}")
        return CashBalance(total=self.total - amount, reserved=self.reserved)

    def reserve(self, amount: Decimal) -> CashBalance:
        if amount > self.available:
            raise ValueError(f"Insufficient available cash to reserve: {self.available} < {amount}")
        return CashBalance(total=self.total, reserved=self.reserved + amount)

    def release(self, amount: Decimal) -> CashBalance:
        if amount > self.reserved:
            amount = self.reserved
        return CashBalance(total=self.total, reserved=self.reserved - amount)


@dataclass
class Position:
    position_id: str
    portfolio_id: str
    symbol: str
    direction: TradeDirection
    quantity: Decimal
    avg_entry_price: Decimal
    current_price: Decimal
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    status: PositionStatus = PositionStatus.PENDING
    sector: str | None = None
    beta: float = 1.0
    volatility: float = 0.0
    avg_daily_volume: float = 0.0
    realized_pnl: Decimal = Decimal("0")
    fees_paid: Decimal = Decimal("0")
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    correlation_id: str = ""

    @property
    def market_value(self) -> Decimal:
        return self.quantity * self.current_price

    @property
    def entry_value(self) -> Decimal:
        return self.quantity * self.avg_entry_price

    @property
    def unrealized_pnl(self) -> Decimal:
        if self.direction == TradeDirection.LONG:
            return (self.current_price - self.avg_entry_price) * self.quantity
        return (self.avg_entry_price - self.current_price) * self.quantity

    @property
    def unrealized_pnl_pct(self) -> float:
        if self.entry_value == 0:
            return 0.0
        return float(self.unrealized_pnl / self.entry_value) * 100.0

    @property
    def total_pnl(self) -> Decimal:
        return self.realized_pnl + self.unrealized_pnl

    @property
    def is_open(self) -> bool:
        return self.status == PositionStatus.OPEN

    @property
    def is_closed(self) -> bool:
        return self.status == PositionStatus.CLOSED

    def close(self, close_price: Decimal, now: datetime | None = None) -> Position:
        realized = self._calculate_realized_pnl(close_price)
        self.realized_pnl += realized
        self.current_price = close_price
        self.status = PositionStatus.CLOSED
        self.closed_at = now
        return self

    def update_price(self, new_price: Decimal) -> Position:
        self.current_price = new_price
        return self

    def update_stop_loss(self, stop_loss: Decimal) -> Position:
        self.stop_loss = stop_loss
        return self

    def update_take_profit(self, take_profit: Decimal) -> Position:
        self.take_profit = take_profit
        return self

    def modify_quantity(self, new_quantity: Decimal, new_price: Decimal) -> Position:
        if new_quantity == self.quantity:
            return self
        if new_quantity > self.quantity:
            delta = new_quantity - self.quantity
            total_cost = self.avg_entry_price * self.quantity + new_price * delta
            self.quantity = new_quantity
            self.avg_entry_price = total_cost / self.quantity
        else:
            delta = self.quantity - new_quantity
            realized = self._calculate_realized_pnl_at_quantity(new_price, delta)
            self.realized_pnl += realized
            self.quantity = new_quantity
        self.current_price = new_price
        return self

    def _calculate_realized_pnl(self, close_price: Decimal) -> Decimal:
        if self.direction == TradeDirection.LONG:
            return (close_price - self.avg_entry_price) * self.quantity
        return (self.avg_entry_price - close_price) * self.quantity

    def _calculate_realized_pnl_at_quantity(self, price: Decimal, qty: Decimal) -> Decimal:
        if self.direction == TradeDirection.LONG:
            return (price - self.avg_entry_price) * qty
        return (self.avg_entry_price - price) * qty


@dataclass
class Portfolio:
    portfolio_id: str
    name: str
    status: PortfolioStatus = PortfolioStatus.ACTIVE
    cash: CashBalance = field(default_factory=lambda: CashBalance(Decimal("0")))
    positions: dict[str, Position] = field(default_factory=dict)
    total_fees: Decimal = Decimal("0")
    total_pnl_realized: Decimal = Decimal("0")
    daily_pnl: Decimal = Decimal("0")
    peak_equity: Decimal = Decimal("0")
    created_at: datetime | None = None
    updated_at: datetime | None = None
    correlation_id: str = ""

    @property
    def position_list(self) -> list[Position]:
        return list(self.positions.values())

    @property
    def open_positions(self) -> list[Position]:
        return [p for p in self.positions.values() if p.is_open]

    @property
    def closed_positions(self) -> list[Position]:
        return [p for p in self.positions.values() if p.is_closed]

    @property
    def market_value(self) -> Decimal:
        total = Decimal("0")
        for p in self.open_positions:
            total += p.market_value
        return total

    @property
    def total_equity(self) -> Decimal:
        return self.cash.available + self.market_value

    @property
    def gross_exposure(self) -> Decimal:
        total = Decimal("0")
        for p in self.open_positions:
            total += p.market_value
        return total

    @property
    def net_exposure(self) -> Decimal:
        long_exposure = Decimal("0")
        short_exposure = Decimal("0")
        for p in self.open_positions:
            if p.direction == TradeDirection.LONG:
                long_exposure += p.market_value
            else:
                short_exposure += p.market_value
        return long_exposure - short_exposure

    @property
    def leverage(self) -> float:
        if self.cash.total == 0:
            return 0.0
        return float(self.gross_exposure / self.cash.total)

    @property
    def gross_exposure_pct(self) -> float:
        if self.total_equity == 0:
            return 0.0
        return float(self.gross_exposure / self.total_equity) * 100.0

    @property
    def net_exposure_pct(self) -> float:
        if self.total_equity == 0:
            return 0.0
        return float(abs(self.net_exposure) / self.total_equity) * 100.0

    @property
    def position_count(self) -> int:
        return len(self.open_positions)

    @property
    def daily_loss_pct(self) -> float:
        if self.total_equity == 0:
            return 0.0
        return abs(min(float(self.daily_pnl / self.total_equity) * 100.0, 0.0))

    @property
    def daily_return_pct(self) -> float:
        if self.total_equity == 0:
            return 0.0
        return float(self.daily_pnl / self.total_equity) * 100.0

    @property
    def unrealized_pnl(self) -> Decimal:
        total = Decimal("0")
        for p in self.open_positions:
            total += p.unrealized_pnl
        return total

    @property
    def total_pnl(self) -> Decimal:
        return self.total_pnl_realized + self.unrealized_pnl

    def sector_exposures(self) -> dict[str, Decimal]:
        exposures: dict[str, Decimal] = {}
        for pos in self.open_positions:
            sector = pos.sector or "Unknown"
            exposures[sector] = exposures.get(sector, Decimal("0")) + pos.market_value
        return exposures

    def sector_exposure_pct(self, sector: str) -> float:
        if self.total_equity == 0:
            return 0.0
        exposures = self.sector_exposures()
        return float(exposures.get(sector, Decimal("0")) / self.total_equity) * 100.0

    def symbol_exposure_pct(self, symbol: str) -> float:
        if self.total_equity == 0:
            return 0.0
        pos = self.positions.get(symbol)
        if pos is None or not pos.is_open:
            return 0.0
        return float(pos.market_value / self.total_equity) * 100.0

    def add_position(self, position: Position) -> None:
        if position.symbol in self.positions and self.positions[position.symbol].is_open:
            self.positions[position.symbol].modify_quantity(
                self.positions[position.symbol].quantity + position.quantity,
                position.avg_entry_price,
            )
        else:
            self.positions[position.symbol] = position

    def close_position(
        self, symbol: str, close_price: Decimal, now: datetime | None = None
    ) -> Position | None:
        pos = self.positions.get(symbol)
        if pos is None or not pos.is_open:
            return None
        pos.close(close_price, now)
        realized = pos.realized_pnl
        self.total_pnl_realized += realized
        self.daily_pnl += realized
        self.cash = self.cash.deposit(pos.market_value)
        if now:
            self.updated_at = now
        return pos

    def update_cash(self, amount: Decimal) -> None:
        self.cash = self.cash.deposit(amount)

    def reserve_cash(self, amount: Decimal) -> None:
        self.cash = self.cash.reserve(amount)

    def release_cash(self, amount: Decimal) -> None:
        self.cash = self.cash.release(amount)

    def update_peak_equity(self) -> None:
        current = self.total_equity
        if current > self.peak_equity:
            self.peak_equity = current

    @property
    def drawdown_pct(self) -> float:
        if self.peak_equity == 0:
            return 0.0
        return max(0.0, float((self.peak_equity - self.total_equity) / self.peak_equity) * 100.0)

    def revalue_positions(self, prices: dict[str, Decimal]) -> None:
        old_equity = self.total_equity
        for symbol, price in prices.items():
            pos = self.positions.get(symbol)
            if pos is not None and pos.is_open:
                pos.update_price(price)
        self.daily_pnl = self.total_equity - old_equity + self.daily_pnl
        self.update_peak_equity()


@dataclass(frozen=True)
class EquityCurvePoint:
    timestamp: datetime
    equity: Decimal
    cash: Decimal
    market_value: Decimal
    daily_pnl: Decimal


@dataclass(frozen=True)
class PortfolioStatistics:
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
    avg_holding_period_hours: float = 0.0
    total_fees: Decimal = Decimal("0")

    @property
    def total_return_pct(self) -> float:
        return 0.0

    @classmethod
    def calculate(
        cls, positions: Sequence[Position], drawdown_pct: float = 0.0
    ) -> PortfolioStatistics:
        closed = [p for p in positions if p.is_closed]
        total = len(closed)
        if total == 0:
            return cls()

        winning = [p for p in closed if p.realized_pnl > 0]
        losing = [p for p in closed if p.realized_pnl < 0]

        gross_profit = Decimal("0")
        for p in winning:
            gross_profit += p.realized_pnl
        gross_loss = Decimal("0")
        for p in losing:
            gross_loss += abs(p.realized_pnl)
        total_pnl = Decimal("0")
        for p in closed:
            total_pnl += p.realized_pnl
        total_fees = Decimal("0")
        for p in closed:
            total_fees += p.fees_paid

        win_count = len(winning)
        lose_count = total - win_count

        win_rate = (win_count / total * 100.0) if total > 0 else 0.0
        profit_factor = float(gross_profit / gross_loss) if gross_loss > 0 else float("inf")

        avg_win = gross_profit / win_count if win_count > 0 else Decimal("0")
        avg_loss = gross_loss / lose_count if lose_count > 0 else Decimal("0")

        largest_win = max((p.realized_pnl for p in winning), default=Decimal("0"))
        largest_loss = min((p.realized_pnl for p in losing), default=Decimal("0"))

        returns = [float(p.realized_pnl) for p in closed]
        avg_return = sum(returns) / len(returns) if returns else 0.0
        variance = sum((r - avg_return) ** 2 for r in returns) / len(returns) if returns else 0.0
        std_dev = variance**0.5
        sharpe = (avg_return / std_dev * (252**0.5)) if std_dev > 0 else 0.0

        neg_returns = [r for r in returns if r < 0]
        neg_variance = sum(r**2 for r in neg_returns) / len(neg_returns) if neg_returns else 0.0
        downside_dev = neg_variance**0.5
        sortino = (avg_return / downside_dev * (252**0.5)) if downside_dev > 0 else 0.0

        avg_holding = 0.0

        return cls(
            total_trades=total,
            winning_trades=win_count,
            losing_trades=lose_count,
            total_pnl=total_pnl,
            gross_profit=gross_profit,
            gross_loss=gross_loss,
            max_drawdown_pct=drawdown_pct,
            current_drawdown_pct=drawdown_pct,
            sharpe_ratio=round(sharpe, 4),
            sortino_ratio=round(sortino, 4),
            profit_factor=round(profit_factor, 4),
            win_rate=round(win_rate, 2),
            avg_win=avg_win,
            avg_loss=avg_loss,
            largest_win=largest_win,
            largest_loss=largest_loss,
            avg_holding_period_hours=round(avg_holding, 2),
            total_fees=total_fees,
        )


@dataclass(frozen=True)
class ExposureReport:
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
    var_95: float = 0.0
    var_99: float = 0.0


class PortfolioRepository(Protocol):
    async def save(self, portfolio: Portfolio) -> None: ...
    async def find_by_id(self, portfolio_id: str) -> Portfolio | None: ...
    async def find_all(self) -> list[Portfolio]: ...
    async def delete(self, portfolio_id: str) -> None: ...


class PositionRepository(Protocol):
    async def save(self, position: Position) -> None: ...
    async def find_by_id(self, position_id: str) -> Position | None: ...
    async def find_by_portfolio(self, portfolio_id: str) -> list[Position]: ...
    async def find_open_by_symbol(self, portfolio_id: str, symbol: str) -> Position | None: ...
