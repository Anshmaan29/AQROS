from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum, StrEnum
from typing import Any, Protocol


class RiskDecision(Enum):
    APPROVE = "approve"
    MODIFY = "modify"
    REJECT = "reject"
    REDUCE = "reduce"


class RiskLevel(Enum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4

    @property
    def label(self) -> str:
        return self.name.lower()


class RiskReason(StrEnum):
    MAX_POSITION_EXCEEDED = "max_position_exceeded"
    MAX_EXPOSURE_EXCEEDED = "max_exposure_exceeded"
    MAX_DAILY_LOSS_EXCEEDED = "max_daily_loss_exceeded"
    MAX_DRAWDOWN_EXCEEDED = "max_drawdown_exceeded"
    MAX_LEVERAGE_EXCEEDED = "max_leverage_exceeded"
    MAX_CONCURRENT_POSITIONS_EXCEEDED = "max_concurrent_positions_exceeded"
    MAX_SYMBOL_EXPOSURE_EXCEEDED = "max_symbol_exposure_exceeded"
    SECTOR_EXPOSURE_EXCEEDED = "sector_exposure_exceeded"
    VOLATILITY_TOO_LOW = "volatility_too_low"
    VOLATILITY_TOO_HIGH = "volatility_too_high"
    INSUFFICIENT_LIQUIDITY = "insufficient_liquidity"
    LOW_CONFIDENCE = "low_confidence"
    LOW_PREDICTION_QUALITY = "low_prediction_quality"
    OUTSIDE_TRADING_SESSION = "outside_trading_session"
    COOLDOWN_ACTIVE = "cooldown_active"
    CIRCUIT_BREAKER_ACTIVE = "circuit_breaker_active"
    POSITION_SIZE_REDUCED = "position_size_reduced"
    APPROVED = "approved"
    KERNEL_CEILING = "kernel_ceiling"


@dataclass(frozen=True)
class PositionExposure:
    symbol: str
    quantity: Decimal
    avg_entry_price: Decimal
    current_price: Decimal
    market_value: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    sector: str | None = None
    beta: float = 1.0
    volatility: float = 0.0
    avg_daily_volume: float = 0.0


@dataclass(frozen=True)
class PortfolioExposure:
    total_equity: Decimal
    total_market_value: Decimal
    gross_exposure: Decimal
    net_exposure: Decimal
    cash: Decimal
    leverage: float
    positions: tuple[PositionExposure, ...] = ()
    sector_exposures: dict[str, Decimal] = field(default_factory=dict)
    daily_pnl: Decimal = Decimal("0")
    portfolio_beta: float = 1.0
    var_95: float = 0.0
    var_99: float = 0.0

    @property
    def position_count(self) -> int:
        return len(self.positions)

    @property
    def gross_exposure_pct(self) -> float:
        if self.total_equity == 0:
            return 0.0
        return float(self.gross_exposure / self.total_equity) * 100.0

    @property
    def daily_loss_pct(self) -> float:
        if self.total_equity == 0:
            return 0.0
        return abs(min(float(self.daily_pnl / self.total_equity) * 100.0, 0.0))


@dataclass(frozen=True)
class AccountLimits:
    account_id: str
    max_position_size_pct: float
    max_portfolio_exposure_pct: float
    max_daily_loss_pct: float
    max_drawdown_pct: float
    max_leverage: float
    max_concurrent_positions: int
    max_symbol_exposure_pct: float
    max_sector_exposure_pct: float
    min_confidence: float
    min_prediction_quality: float
    is_kernel: bool = True


@dataclass
class DrawdownTracker:
    peak_equity: Decimal
    current_drawdown_pct: float = 0.0
    max_drawdown_pct: float = 0.0

    def update(self, current_equity: Decimal) -> None:
        if current_equity > self.peak_equity:
            self.peak_equity = current_equity
        self.current_drawdown_pct = (
            float((self.peak_equity - current_equity) / self.peak_equity) * 100.0
            if self.peak_equity > 0
            else 0.0
        )
        if self.current_drawdown_pct > self.max_drawdown_pct:
            self.max_drawdown_pct = self.current_drawdown_pct

    def is_drawdown_exceeded(self, limit_pct: float) -> bool:
        """Whether current drawdown breaches the supplied limit.

        A method rather than a property: the limit is supplied by the caller
        (it comes from the risk kernel's ceilings, which are human-owned).
        """
        return self.current_drawdown_pct > limit_pct


@dataclass
class CircuitBreaker:
    daily_loss_limit_pct: float
    consecutive_loss_limit: int
    cooldown_timedelta: timedelta
    daily_loss_pct: float = 0.0
    consecutive_losses: int = 0
    is_tripped: bool = False
    tripped_at: datetime | None = None
    last_trade_result: str | None = None

    def record_trade_result(self, pnl: Decimal, equity: Decimal, now: datetime) -> None:
        trade_loss_pct = max(0.0, float(-pnl / equity) * 100.0) if equity > 0 else 0.0
        self.daily_loss_pct += trade_loss_pct
        if pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0
        if (
            self.daily_loss_pct >= self.daily_loss_limit_pct
            or self.consecutive_losses >= self.consecutive_loss_limit
        ):
            self.is_tripped = True
            self.tripped_at = now

    def check(self, now: datetime) -> bool:
        if not self.is_tripped:
            return False
        if self.tripped_at is not None and now - self.tripped_at >= self.cooldown_timedelta:
            self.reset()
            return False
        return True

    def reset(self) -> None:
        self.is_tripped = False
        self.tripped_at = None
        self.daily_loss_pct = 0.0
        self.consecutive_losses = 0


@dataclass
class RiskStatistics:
    total_evaluations: int = 0
    approved_count: int = 0
    rejected_count: int = 0
    modified_count: int = 0
    reduced_count: int = 0
    total_latency_ms: float = 0.0
    max_latency_ms: float = 0.0
    limit_hits: dict[str, int] = field(default_factory=dict)

    @property
    def avg_latency_ms(self) -> float:
        if self.total_evaluations == 0:
            return 0.0
        return self.total_latency_ms / self.total_evaluations

    @property
    def approved_rate(self) -> float:
        if self.total_evaluations == 0:
            return 0.0
        return self.approved_count / self.total_evaluations * 100.0

    def record_evaluation(
        self, decision: RiskDecision, latency_ms: float, reasons: Sequence[RiskReason]
    ) -> None:
        self.total_evaluations += 1
        self.total_latency_ms += latency_ms
        self.max_latency_ms = max(self.max_latency_ms, latency_ms)
        if decision == RiskDecision.APPROVE:
            self.approved_count += 1
        elif decision == RiskDecision.REJECT:
            self.rejected_count += 1
        elif decision == RiskDecision.MODIFY:
            self.modified_count += 1
        elif decision == RiskDecision.REDUCE:
            self.reduced_count += 1
        for reason in reasons:
            if reason.value.startswith("max_") or reason.value.startswith("circuit"):
                self.limit_hits[reason.value] = self.limit_hits.get(reason.value, 0) + 1

    def snapshot(self) -> dict[str, Any]:
        return {
            "total_evaluations": self.total_evaluations,
            "approved_count": self.approved_count,
            "rejected_count": self.rejected_count,
            "modified_count": self.modified_count,
            "reduced_count": self.reduced_count,
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "max_latency_ms": round(self.max_latency_ms, 2),
            "approved_rate": round(self.approved_rate, 1),
            "limit_hits": dict(sorted(self.limit_hits.items())),
        }


@dataclass(frozen=True)
class RiskVerdict:
    decision: RiskDecision
    reasons: tuple[RiskReason, ...]
    message: str
    risk_level: RiskLevel
    suggested_quantity: Decimal | None = None
    suggested_stop_loss: Decimal | None = None
    modified_params: dict[str, Any] | None = None


@dataclass
class RiskLimits:
    max_position_size_pct: float
    max_portfolio_exposure_pct: float
    max_daily_loss_pct: float
    max_drawdown_pct: float
    max_leverage: float
    max_concurrent_positions: int
    max_symbol_exposure_pct: float
    max_sector_exposure_pct: float
    min_volatility: float
    max_volatility: float
    min_avg_daily_volume: float
    min_confidence: float
    min_prediction_quality: float
    is_kernel: bool = True

    def with_overrides(self, **kwargs: float | int) -> RiskLimits:
        overridden = {k: v for k, v in self.__dict__.items() if not k.startswith("_")}
        overridden.update(kwargs)
        return RiskLimits(**overridden)

    def snapshot(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}


@dataclass(frozen=True)
class Signal:
    signal_id: str
    symbol: str
    side: str
    quantity: Decimal
    confidence: float
    prediction: float
    prediction_quality: float
    current_price: Decimal
    sector: str | None = None
    volatility: float = 0.0
    avg_daily_volume: float = 0.0
    strategy: str = "unknown"
    correlation_id: str = ""
    timestamp: datetime | None = None


@dataclass(frozen=True)
class EvaluationContext:
    signal: Signal
    portfolio: PortfolioExposure
    limits: RiskLimits
    drawdown: DrawdownTracker
    circuit_breaker: CircuitBreaker
    now: datetime
    account_id: str = "default"


@dataclass(frozen=True)
class PositionSizingResult:
    quantity: Decimal
    sizing_method: str
    reason: str
    risk_per_trade: Decimal = Decimal("0")
    stop_loss: Decimal | None = None


class RiskRule(Protocol):
    name: str

    async def evaluate(self, ctx: EvaluationContext) -> RiskVerdict | None: ...


class PositionSizingStrategy(Protocol):
    name: str

    async def size(self, signal: Signal, ctx: EvaluationContext) -> PositionSizingResult: ...
