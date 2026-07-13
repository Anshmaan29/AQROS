"""Pydantic request/response models for the feature-store HTTP API.

Kept separate from ``domain/models.py`` so the wire format can evolve
independently of the internal domain representation, mirroring
``aqros_market_data.api.schemas``.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from aqros_feature_store.domain.models import (
    ComputationMode,
    ComputationStatus,
    FeatureCategory,
    FeatureComputationRun,
    FeatureDefinition,
    FeatureStatistics,
    FeatureValue,
)


class ComputationRequest(BaseModel):
    """Request body to trigger a feature-computation run for one symbol."""

    symbol: str = Field(..., min_length=1, max_length=32, examples=["AAPL"])
    mode: ComputationMode = ComputationMode.INCREMENTAL

    @field_validator("symbol")
    @classmethod
    def _normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()


class ComputationRunResponse(BaseModel):
    """A feature-computation run, as returned by the API."""

    id: int | None
    symbol: str
    mode: ComputationMode
    status: ComputationStatus
    started_at: datetime
    completed_at: datetime | None
    bars_read: int
    features_computed: int
    features_persisted: int
    features_rejected: int
    rejection_reasons: list[str]
    error_message: str | None

    @classmethod
    def from_domain(cls, run: FeatureComputationRun) -> ComputationRunResponse:
        return cls(
            id=run.id,
            symbol=run.symbol,
            mode=run.mode,
            status=run.status,
            started_at=run.started_at,
            completed_at=run.completed_at,
            bars_read=run.bars_read,
            features_computed=run.features_computed,
            features_persisted=run.features_persisted,
            features_rejected=run.features_rejected,
            rejection_reasons=run.rejection_reasons,
            error_message=run.error_message,
        )


class FeatureDefinitionResponse(BaseModel):
    """A registered feature definition, as returned by the API."""

    name: str
    version: int
    category: FeatureCategory
    description: str
    parameters: dict[str, float]
    min_bars_required: int

    @classmethod
    def from_domain(cls, definition: FeatureDefinition) -> FeatureDefinitionResponse:
        return cls(
            name=definition.name,
            version=definition.version,
            category=definition.category,
            description=definition.description,
            parameters=definition.parameters,
            min_bars_required=definition.min_bars_required,
        )


class FeatureValueResponse(BaseModel):
    """A single computed feature value, as returned by the API."""

    symbol: str
    feature_name: str
    feature_version: int
    event_time: datetime
    value: float
    knowledge_time: datetime

    @classmethod
    def from_domain(cls, value: FeatureValue) -> FeatureValueResponse:
        return cls(
            symbol=value.symbol,
            feature_name=value.feature_name,
            feature_version=value.feature_version,
            event_time=value.event_time,
            value=value.value,
            knowledge_time=value.knowledge_time,
        )


class PaginatedFeatureValuesResponse(BaseModel):
    """Paginated feature-value listing."""

    symbol: str
    feature_name: str
    total: int
    limit: int
    offset: int
    values: list[FeatureValueResponse]


class FeatureStatisticsResponse(BaseModel):
    """Summary statistics for a feature, as returned by the API."""

    symbol: str
    feature_name: str
    feature_version: int
    count: int
    mean: float | None
    std: float | None
    minimum: float | None
    maximum: float | None

    @classmethod
    def from_domain(cls, stats: FeatureStatistics) -> FeatureStatisticsResponse:
        return cls(
            symbol=stats.symbol,
            feature_name=stats.feature_name,
            feature_version=stats.feature_version,
            count=stats.count,
            mean=stats.mean,
            std=stats.std,
            minimum=stats.minimum,
            maximum=stats.maximum,
        )


class ErrorResponse(BaseModel):
    """Typed error envelope (CLAUDE.md §5: typed, coded error responses)."""

    error: str
    detail: str
