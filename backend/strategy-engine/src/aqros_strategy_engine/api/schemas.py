"""API request/response schemas for the Strategy Engine."""

from __future__ import annotations

from pydantic import BaseModel, Field


class EvaluateRequest(BaseModel):
    symbol: str
    model_name: str = "default"
    model_version: int | None = None
    features: dict[str, float] | None = None
    prediction: float | None = None
    prediction_confidence: float | None = None
    strategy_name: str | None = None
    correlation_id: str | None = None


class EvaluateResponse(BaseModel):
    symbol: str
    signal: str
    confidence: float
    strength: str
    reason: str
    explanation: str
    latency_ms: float
    strategy_name: str = ""
    strategy_version: str = ""
    prediction: float | None = None
    model_version: int | None = None
    error: str | None = None


class BatchEvaluateRequest(BaseModel):
    requests: list[EvaluateRequest] = Field(..., min_length=1, max_length=100)


class BatchEvaluateResponse(BaseModel):
    results: list[EvaluateResponse]
    total_latency_ms: float
    success_count: int
    failure_count: int


class StrategyInfo(BaseModel):
    name: str
    version: str
    description: str = ""
    model_family: str = ""
    active: bool = False
    tags: list[str] = Field(default_factory=list)


class ReloadStrategyRequest(BaseModel):
    strategy_name: str
    strategy_version: str | None = None


class StrategyStatisticsResponse(BaseModel):
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


class StrategyHealthResponse(BaseModel):
    inference_reachable: bool
    feature_store_reachable: bool
    strategy_loaded: bool
    loaded_strategy_count: int
