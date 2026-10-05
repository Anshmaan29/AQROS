"""Event payload schemas for inference service events.

Every event wraps a Pydantic model serialised as JSON bytes inside an
``EventEnvelope``. All events follow the ``subject.verb`` convention.
"""

from __future__ import annotations

from pydantic import BaseModel


class PredictionRequested(BaseModel):
    """Emitted when a prediction request is received."""

    schema_version: str = "1.0"
    symbol: str
    model_name: str
    model_version: int
    request_id: str
    correlation_id: str | None = None


class PredictionStarted(BaseModel):
    """Emitted when inference begins (after feature fetch + validation)."""

    schema_version: str = "1.0"
    symbol: str
    model_name: str
    model_version: int
    request_id: str
    correlation_id: str | None = None
    feature_count: int = 0


class PredictionCompleted(BaseModel):
    """Emitted when inference finishes successfully."""

    schema_version: str = "1.0"
    symbol: str
    model_name: str
    model_version: int
    request_id: str
    correlation_id: str | None = None
    value: float = 0.0
    confidence: float = 0.0
    latency_ms: float = 0.0


class PredictionFailed(BaseModel):
    """Emitted when inference fails."""

    schema_version: str = "1.0"
    symbol: str
    model_name: str
    model_version: int
    request_id: str
    correlation_id: str | None = None
    error_code: str = ""
    error_message: str = ""


class ModelLoaded(BaseModel):
    """Emitted when a model is loaded into the cache."""

    schema_version: str = "1.0"
    model_name: str
    model_version: int
    checksum: str = ""


class ModelReloaded(BaseModel):
    """Emitted when a model is reloaded (hot-reload or manual)."""

    schema_version: str = "1.0"
    model_name: str
    model_version: int
    previous_version: int | None = None


class ModelUnloaded(BaseModel):
    """Emitted when a model is evicted from the cache."""

    schema_version: str = "1.0"
    model_name: str
    model_version: int


class ModelCacheMiss(BaseModel):
    """Emitted when a requested model is not in the cache."""

    schema_version: str = "1.0"
    model_name: str
    model_version: int


class PredictionAudited(BaseModel):
    """Emitted when a prediction audit record is created."""

    schema_version: str = "1.0"
    request_id: str
    symbol: str
    model_name: str
    model_version: int
    value: float
    confidence: float
    latency_ms: float
    timestamp: str = ""
