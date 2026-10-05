from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field


class SignalRequest(BaseModel):
    signal_id: str = Field(..., description="Unique signal identifier.")
    symbol: str = Field(..., description="Ticker symbol.")
    side: str = Field(..., description="Trade side: buy or sell.")
    quantity: Decimal = Field(..., description="Requested quantity.")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="Prediction confidence.")
    prediction: float = Field(default=0.0, description="Predicted return.")
    prediction_quality: float = Field(
        default=0.5, ge=0.0, le=1.0, description="Prediction quality score."
    )
    current_price: Decimal = Field(..., description="Current market price.")
    sector: str | None = Field(default=None, description="Symbol sector.")
    volatility: float = Field(default=0.0, ge=0.0, description="Annualized volatility.")
    avg_daily_volume: float = Field(default=0.0, ge=0.0, description="Average daily volume in USD.")
    strategy: str = Field(default="unknown", description="Strategy name.")
    correlation_id: str = Field(default="", description="Correlation ID for tracing.")


class SizingConfigRequest(BaseModel):
    method: str = Field(default="percentage_of_equity", description="Sizing method name.")
    parameter: float | None = Field(
        default=None, description="Sizing parameter (e.g., percentage, Kelly fraction)."
    )


class EvaluateRequest(BaseModel):
    signal: SignalRequest
    sizing: SizingConfigRequest | None = None


class EvaluateResponse(BaseModel):
    signal_id: str
    symbol: str
    side: str
    decision: str
    reasons: list[str]
    message: str
    risk_level: str
    requested_quantity: str
    approved_quantity: str
    stop_loss: str | None
    latency_ms: float


class BatchEvaluateRequest(BaseModel):
    signals: list[EvaluateRequest]


class BatchEvaluateResponse(BaseModel):
    results: list[EvaluateResponse]


class RiskStatisticsResponse(BaseModel):
    total_evaluations: int
    approved_count: int
    rejected_count: int
    modified_count: int
    reduced_count: int
    avg_latency_ms: float
    max_latency_ms: float
    approved_rate: float
    limit_hits: dict[str, int]


class RiskLimitResponse(BaseModel):
    id: int
    limit_type: str
    limit_value: float
    is_kernel: bool
    scope: str
    scope_ref: str | None
    created_by: str
    approved_by: str | None
    created_at: str


class RiskLimitCreateRequest(BaseModel):
    limit_type: str = Field(..., description="Limit type name (e.g., max_position_size_pct).")
    limit_value: float = Field(..., description="Limit value.")
    scope: str = Field(
        default="global", description="Scope: global, account, strategy, instrument."
    )
    scope_ref: str | None = Field(default=None, description="Scope reference (e.g., account ID).")
    is_kernel: bool = Field(default=True, description="Whether this is a hard kernel ceiling.")
    created_by: str = Field(default="system", description="Creator identifier.")


class RiskLimitsResponse(BaseModel):
    limits: list[RiskLimitResponse]


class HealthResponse(BaseModel):
    status: str
    database: bool
    portfolio: bool
    market_data: bool
    check_count: int
