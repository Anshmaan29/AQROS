"""Domain models for the Strategy Engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class SignalType(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    EXIT = "EXIT"
    REDUCE = "REDUCE"
    INCREASE = "INCREASE"


class SignalStrength(StrEnum):
    STRONG = "STRONG"
    MODERATE = "MODERATE"
    WEAK = "WEAK"


@dataclass(frozen=True)
class SignalConfidence:
    score: float
    calibrated: bool = False


class SignalReason(StrEnum):
    THRESHOLD_BREACH = "THRESHOLD_BREACH"
    MOMENTUM_SIGNAL = "MOMENTUM_SIGNAL"
    MEAN_REVERSION = "MEAN_REVERSION"
    ENSEMBLE_CONSENSUS = "ENSEMBLE_CONSENSUS"
    WEIGHTED_VOTE = "WEIGHTED_VOTE"
    RULE_TRIGGERED = "RULE_TRIGGERED"
    MODEL_PREDICTION = "MODEL_PREDICTION"
    CONFIDENCE_TOO_LOW = "CONFIDENCE_TOO_LOW"
    RISK_REJECTED = "RISK_REJECTED"
    STRATEGY_ERROR = "STRATEGY_ERROR"
    MANUAL_OVERRIDE = "MANUAL_OVERRIDE"


class SignalPriority(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class StrategyContext(StrEnum):
    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    LIVE = "LIVE"


class StrategyState(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    ERROR = "ERROR"
    DEGRADED = "DEGRADED"


@dataclass(frozen=True)
class StrategyDecision:
    signal: SignalType
    confidence: SignalConfidence
    strength: SignalStrength
    reason: SignalReason
    explanation: str = ""
    prediction_value: float | None = None
    model_version: int | None = None
    strategy_version: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StrategyResult:
    symbol: str
    decision: StrategyDecision
    latency_ms: float
    feature_snapshot: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class StrategyEvaluationRequest:
    symbol: str
    model_name: str = "default"
    model_version: int | None = None
    features: dict[str, Any] | None = None
    prediction: float | None = None
    prediction_confidence: float | None = None
    strategy_name: str | None = None
    correlation_id: str | None = None


@dataclass(frozen=True)
class StrategyMetadata:
    name: str
    version: str
    description: str = ""
    model_family: str = ""
    author: str = ""
    tags: list[str] = field(default_factory=list)
    created_at: datetime | None = None


@dataclass
class StrategyStatistics:
    total_evaluations: int = 0
    total_signals: int = 0
    total_rejected: int = 0
    total_errors: int = 0
    average_latency_ms: float = 0.0
    buy_count: int = 0
    sell_count: int = 0
    hold_count: int = 0
    exit_count: int = 0
    reduce_count: int = 0
    increase_count: int = 0
    loaded_strategies: int = 0
