"""Domain models for the Inference Service.

Defines prediction-related value objects, enums, and data structures used
throughout the service. All models are Pydantic or frozen dataclasses —
immutable by contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class PredictionStatus(StrEnum):
    """Outcome of a single prediction request."""

    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    REJECTED = "rejected"
    ERROR = "error"


class PredictionType(StrEnum):
    """Kind of prediction the model produces."""

    REGRESSION = "regression"
    BINARY_CLASSIFICATION = "binary_classification"
    MULTI_CLASS = "multi_class"
    SCORE = "score"


@dataclass(frozen=True)
class PredictionConfidence:
    """Confidence estimate for a single prediction.

    The ``score`` is a float in [0, 1] where 1 = most confident.
    ``calibrated`` indicates whether the score has been calibration-adjusted.
    """

    score: float
    calibrated: bool = False


@dataclass(frozen=True)
class PredictionExplanation:
    """Explanation for a single prediction.

    ``feature_importance`` maps feature names to their contribution to the
    prediction (positive = pushes prediction up, negative = pushes down).
    Only the top ``k`` features are included.
    """

    feature_importance: dict[str, float] = field(default_factory=dict)
    method: str = "unknown"


@dataclass(frozen=True)
class PredictionError:
    """Details of a prediction failure."""

    code: str
    message: str
    detail: str | None = None


@dataclass(frozen=True)
class PredictionRequest:
    """A request to run inference for one instrument."""

    symbol: str
    model_name: str
    model_version: int | None = None
    features: dict[str, Any] | None = None
    correlation_id: str | None = None
    request_id: str | None = None


@dataclass(frozen=True)
class PredictionResult:
    """The result of running inference for one instrument."""

    symbol: str
    value: float
    confidence: PredictionConfidence
    explanation: PredictionExplanation | None = None
    model_name: str = ""
    model_version: int = 0
    feature_version: int = 0
    latency_ms: float = 0.0
    status: PredictionStatus = PredictionStatus.SUCCESS
    error: PredictionError | None = None


@dataclass(frozen=True)
class PredictionMetadata:
    """Metadata attached to every prediction request and response."""

    request_id: str
    correlation_id: str | None = None
    model_name: str = ""
    model_version: int = 0
    feature_version: int = 0
    schema_version: str = "1.0"
    timestamp: datetime | None = None


@dataclass
class InferenceStatistics:
    """Aggregate inference statistics."""

    total_requests: int = 0
    total_success: int = 0
    total_failures: int = 0
    total_timeouts: int = 0
    total_rejected: int = 0
    total_latency_ms: float = 0.0
    average_latency_ms: float = 0.0
    model_cache_hits: int = 0
    model_cache_misses: int = 0
    model_reload_count: int = 0
    loaded_models: int = 0
    feature_validation_failures: int = 0
