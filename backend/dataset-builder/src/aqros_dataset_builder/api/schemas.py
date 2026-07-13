"""Pydantic request/response models for the dataset-builder HTTP API.

Kept separate from ``domain/models.py`` so the wire format can evolve
independently of the internal domain representation, mirroring
``aqros_feature_store.api.schemas``.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from aqros_dataset_builder.domain.models import (
    BuildStatus,
    DatasetBuildRun,
    DatasetDefinition,
    DatasetQualityReport,
    ExpandingWindowParams,
    FeatureStatistics,
    LabelBalance,
    LabelType,
    PredictionHorizon,
    PurgedCVParams,
    RollingWindowParams,
    SplitStrategy,
    WalkForwardParams,
)


class WalkForwardParamsRequest(BaseModel):
    train_size: int = Field(..., gt=0)
    validation_size: int = Field(..., gt=0)
    test_size: int = Field(..., gt=0)
    step_size: int = Field(..., gt=0)


class RollingWindowParamsRequest(BaseModel):
    train_size: int = Field(..., gt=0)
    validation_size: int = Field(..., gt=0)
    test_size: int = Field(..., gt=0)


class ExpandingWindowParamsRequest(BaseModel):
    validation_size: int = Field(..., gt=0)
    test_size: int = Field(..., gt=0)


class PurgedCVParamsRequest(BaseModel):
    n_splits: int = Field(..., ge=2)
    embargo_size: int = Field(..., ge=0)


class DatasetDefinitionRequest(BaseModel):
    """Request body to register a new dataset definition version."""

    name: str = Field(..., min_length=1, max_length=64, examples=["aapl_5d_direction"])
    symbols: list[str] = Field(..., min_length=1, examples=[["AAPL", "MSFT"]])
    feature_names: list[str] = Field(..., min_length=1, examples=[["sma_20", "rsi_14"]])
    label_type: LabelType
    horizon: PredictionHorizon
    split_strategy: SplitStrategy
    walk_forward_params: WalkForwardParamsRequest | None = None
    rolling_window_params: RollingWindowParamsRequest | None = None
    expanding_window_params: ExpandingWindowParamsRequest | None = None
    purged_cv_params: PurgedCVParamsRequest | None = None
    start_date: date
    end_date: date

    @field_validator("symbols")
    @classmethod
    def _normalize_symbols(cls, value: list[str]) -> list[str]:
        return [s.strip().upper() for s in value]

    @model_validator(mode="after")
    def _validate_params_match_strategy(self) -> DatasetDefinitionRequest:
        if self.end_date < self.start_date:
            raise ValueError(
                f"end_date ({self.end_date}) must not be before start_date ({self.start_date})"
            )

        required_field = {
            SplitStrategy.WALK_FORWARD: "walk_forward_params",
            SplitStrategy.ROLLING_WINDOW: "rolling_window_params",
            SplitStrategy.EXPANDING_WINDOW: "expanding_window_params",
            SplitStrategy.PURGED_CV: "purged_cv_params",
        }[self.split_strategy]
        if getattr(self, required_field) is None:
            raise ValueError(
                f"split_strategy={self.split_strategy.value} requires '{required_field}' "
                f"to be provided"
            )
        return self

    def to_split_params(
        self,
    ) -> WalkForwardParams | RollingWindowParams | ExpandingWindowParams | PurgedCVParams:
        if self.split_strategy is SplitStrategy.WALK_FORWARD:
            assert self.walk_forward_params is not None
            return WalkForwardParams(**self.walk_forward_params.model_dump())
        if self.split_strategy is SplitStrategy.ROLLING_WINDOW:
            assert self.rolling_window_params is not None
            return RollingWindowParams(**self.rolling_window_params.model_dump())
        if self.split_strategy is SplitStrategy.EXPANDING_WINDOW:
            assert self.expanding_window_params is not None
            return ExpandingWindowParams(**self.expanding_window_params.model_dump())
        assert self.purged_cv_params is not None
        return PurgedCVParams(**self.purged_cv_params.model_dump())


class DatasetDefinitionResponse(BaseModel):
    """A registered dataset definition, as returned by the API."""

    name: str
    version: int
    symbols: list[str]
    feature_names: list[str]
    label_type: LabelType
    horizon: PredictionHorizon
    split_strategy: SplitStrategy
    split_params: dict[str, int]
    start_date: date
    end_date: date
    created_at: datetime

    @classmethod
    def from_domain(cls, definition: DatasetDefinition) -> DatasetDefinitionResponse:
        return cls(
            name=definition.name,
            version=definition.version,
            symbols=list(definition.symbols),
            feature_names=list(definition.feature_names),
            label_type=definition.label_type,
            horizon=definition.horizon,
            split_strategy=definition.split_strategy,
            split_params=asdict(definition.split_params),
            start_date=definition.start_date,
            end_date=definition.end_date,
            created_at=definition.created_at,
        )


class BuildRequest(BaseModel):
    """Request body to trigger a dataset build for a registered definition."""

    version: int = Field(..., ge=1)


class LabelBalanceResponse(BaseModel):
    count: int
    mean: float | None
    std: float | None
    minimum: float | None
    maximum: float | None
    positive_fraction: float | None

    @classmethod
    def from_domain(cls, balance: LabelBalance) -> LabelBalanceResponse:
        return cls(
            count=balance.count,
            mean=balance.mean,
            std=balance.std,
            minimum=balance.minimum,
            maximum=balance.maximum,
            positive_fraction=balance.positive_fraction,
        )


class FeatureStatisticsResponse(BaseModel):
    feature_name: str
    count: int
    missing_count: int
    mean: float | None
    std: float | None
    minimum: float | None
    maximum: float | None

    @classmethod
    def from_domain(cls, stats: FeatureStatistics) -> FeatureStatisticsResponse:
        return cls(
            feature_name=stats.feature_name,
            count=stats.count,
            missing_count=stats.missing_count,
            mean=stats.mean,
            std=stats.std,
            minimum=stats.minimum,
            maximum=stats.maximum,
        )


class DatasetQualityReportResponse(BaseModel):
    """Dataset quality metrics: missing values, duplicates, class balance,
    per-feature statistics, and basic data-quality validation findings."""

    total_rows: int
    duplicate_row_count: int
    missing_value_counts: dict[str, int]
    class_balance: dict[str, float]
    feature_statistics: list[FeatureStatisticsResponse]
    validation_passed: bool
    validation_findings: list[str]

    @classmethod
    def from_domain(cls, report: DatasetQualityReport) -> DatasetQualityReportResponse:
        return cls(
            total_rows=report.total_rows,
            duplicate_row_count=report.duplicate_row_count,
            missing_value_counts=report.missing_value_counts,
            class_balance=report.class_balance,
            feature_statistics=[
                FeatureStatisticsResponse.from_domain(fs) for fs in report.feature_statistics
            ],
            validation_passed=report.validation_passed,
            validation_findings=report.validation_findings,
        )


class DatasetBuildRunResponse(BaseModel):
    """A dataset build run, as returned by the API."""

    id: int | None
    dataset_name: str
    dataset_version: int
    status: BuildStatus
    started_at: datetime
    completed_at: datetime | None
    bars_read: int
    rows_generated: int
    rows_rejected: int
    rejection_reasons: list[str]
    leakage_audit_passed: bool | None
    leakage_audit_findings: list[str]
    label_balance: LabelBalanceResponse | None
    row_counts_by_role: dict[str, int]
    quality_report: DatasetQualityReportResponse | None
    parquet_path: str | None
    manifest_path: str | None
    error_message: str | None

    @classmethod
    def from_domain(cls, run: DatasetBuildRun) -> DatasetBuildRunResponse:
        return cls(
            id=run.id,
            dataset_name=run.dataset_name,
            dataset_version=run.dataset_version,
            status=run.status,
            started_at=run.started_at,
            completed_at=run.completed_at,
            bars_read=run.bars_read,
            rows_generated=run.rows_generated,
            rows_rejected=run.rows_rejected,
            rejection_reasons=run.rejection_reasons,
            leakage_audit_passed=run.leakage_audit_passed,
            leakage_audit_findings=run.leakage_audit_findings,
            label_balance=(
                LabelBalanceResponse.from_domain(run.label_balance) if run.label_balance else None
            ),
            row_counts_by_role=run.row_counts_by_role,
            quality_report=(
                DatasetQualityReportResponse.from_domain(run.quality_report)
                if run.quality_report
                else None
            ),
            parquet_path=run.parquet_path,
            manifest_path=run.manifest_path,
            error_message=run.error_message,
        )


class ErrorResponse(BaseModel):
    """Typed error envelope (CLAUDE.md §5: typed, coded error responses)."""

    error: str
    detail: str
