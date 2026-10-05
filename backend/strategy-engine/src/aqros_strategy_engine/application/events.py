"""Event schemas for the Strategy Engine."""

from __future__ import annotations

from pydantic import BaseModel


class SignalGenerated(BaseModel):
    symbol: str
    strategy_name: str
    strategy_version: str
    signal: str
    confidence: float
    strength: str
    reason: str
    prediction: float
    model_version: int
    request_id: str
    correlation_id: str | None = None
    latency_ms: float = 0.0


class SignalRejected(BaseModel):
    symbol: str
    strategy_name: str
    signal: str
    reason: str
    request_id: str
    correlation_id: str | None = None


class StrategyEvaluated(BaseModel):
    symbol: str
    strategy_name: str
    request_id: str
    correlation_id: str | None = None


class StrategyLoaded(BaseModel):
    strategy_name: str
    strategy_version: str


class StrategyReloaded(BaseModel):
    strategy_name: str
    strategy_version: str


class StrategyFailed(BaseModel):
    symbol: str
    strategy_name: str
    error_code: str
    error_message: str
    request_id: str
    correlation_id: str | None = None
