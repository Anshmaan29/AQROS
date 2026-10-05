from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from aqros_risk_engine.domain.models import RiskReason


@dataclass(frozen=True)
class RiskEvaluationStarted:
    signal_id: str
    symbol: str
    side: str
    quantity: str
    strategy: str
    correlation_id: str = ""
    timestamp: datetime | None = None


@dataclass(frozen=True)
class RiskApproved:
    signal_id: str
    symbol: str
    side: str
    approved_quantity: str
    strategy: str
    correlation_id: str = ""
    timestamp: datetime | None = None


@dataclass(frozen=True)
class RiskRejected:
    signal_id: str
    symbol: str
    side: str
    reasons: tuple[RiskReason, ...] = ()
    correlation_id: str = ""
    timestamp: datetime | None = None


@dataclass(frozen=True)
class RiskModified:
    signal_id: str
    symbol: str
    side: str
    original_quantity: str
    modified_quantity: str
    stop_loss: str | None = None
    reasons: tuple[RiskReason, ...] = ()
    correlation_id: str = ""
    timestamp: datetime | None = None


@dataclass(frozen=True)
class RiskReduced:
    signal_id: str
    symbol: str
    side: str
    original_quantity: str
    reduced_quantity: str
    reasons: tuple[RiskReason, ...] = ()
    correlation_id: str = ""
    timestamp: datetime | None = None


@dataclass(frozen=True)
class RiskLimitBreached:
    limit_type: str
    current_value: float
    limit_value: float
    scope: str = "global"
    correlation_id: str = ""
    timestamp: datetime | None = None


@dataclass(frozen=True)
class CircuitBreakerTriggered:
    reason: str
    daily_loss_pct: float = 0.0
    consecutive_losses: int = 0
    cooldown_minutes: int = 60
    correlation_id: str = ""
    timestamp: datetime | None = None
