"""API request/response schemas for the inference service."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class PredictRequest(BaseModel):
    """Single prediction request."""

    symbol: str
    model_name: str = "default"
    model_version: int | None = None
    features: dict[str, float | int] | None = None
    correlation_id: str | None = None


class PredictResponse(BaseModel):
    """Single prediction response."""

    symbol: str
    value: float
    confidence: float
    model_name: str
    model_version: int
    feature_version: int = 0
    latency_ms: float
    status: str
    request_id: str = ""
    explanation: dict[str, float] | None = None
    error: str | None = None


class BatchPredictRequest(BaseModel):
    """Batch prediction request."""

    requests: list[PredictRequest] = Field(..., min_length=1, max_length=100)


class BatchPredictResponse(BaseModel):
    """Batch prediction response."""

    results: list[PredictResponse]
    total_latency_ms: float
    success_count: int
    failure_count: int


class ModelInfo(BaseModel):
    """Summary info about a loaded model."""

    name: str
    version: int
    model_type: str
    checksum: str
    loaded_at: datetime
    production: bool = False


class ReloadRequest(BaseModel):
    """Request to reload a model."""

    model_name: str
    model_version: int | None = None


class RollbackRequest(BaseModel):
    """Request to roll back to the previous production version."""

    model_name: str


class InferenceStatisticsResponse(BaseModel):
    """Aggregate inference statistics."""

    total_requests: int = 0
    total_success: int = 0
    total_failures: int = 0
    average_latency_ms: float = 0.0
    loaded_models: int = 0
    model_cache_hits: int = 0
    model_cache_misses: int = 0
    model_reload_count: int = 0
    feature_validation_failures: int = 0


class InferenceHealthResponse(BaseModel):
    """Inference subsystem health."""

    feature_store_reachable: bool
    model_loaded: bool
    loaded_model_count: int
    last_prediction_timestamp: datetime | None = None
