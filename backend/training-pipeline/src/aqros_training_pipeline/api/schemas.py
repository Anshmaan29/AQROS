"""Pydantic request/response schemas for the Training API.

``TrainingRequestSchema`` validates ``model_types`` against the four
supported values at the Pydantic layer, so an unsupported value produces a
422 naming the offending value before the domain layer is ever reached
(Requirement 7.6). Every response schema has a ``from_domain`` converter,
mirroring ``aqros_dataset_builder.api.schemas``.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from aqros_training_pipeline.domain.models import (
    AggregatedMetrics,
    ModelType,
    ModelTypeOutcome,
    PerFoldMetrics,
    ReproducibilityMetadata,
    TrainedModel,
    TrainingRun,
)
from aqros_training_pipeline.domain.reports import MetricsReport, TrainingReport


class ErrorResponse(BaseModel):
    """Shared error envelope returned with non-2xx responses (Requirement 14.8)."""

    error: str
    detail: str


class TrainingRequestSchema(BaseModel):
    """Request body for ``POST /v1/training-runs``."""

    model_config = ConfigDict(protected_namespaces=())

    dataset_name: str = Field(min_length=1, max_length=64)
    build_run_id: int
    model_types: list[ModelType] = Field(min_length=1)
    hyperparameters: dict[ModelType, dict[str, object]] = Field(default_factory=dict)


class ModelTypeOutcomeResponse(BaseModel):
    """One requested model type's outcome within a run."""

    model_config = ConfigDict(protected_namespaces=())

    model_type: ModelType
    trained_model_id: int | None
    error_message: str | None

    @classmethod
    def from_domain(cls, outcome: ModelTypeOutcome) -> ModelTypeOutcomeResponse:
        return cls(
            model_type=outcome.model_type,
            trained_model_id=outcome.trained_model_id,
            error_message=outcome.error_message,
        )


class TrainingRunResponse(BaseModel):
    """Status + report for a training run (Requirements 14.1, 14.2, 11.3)."""

    model_config = ConfigDict(protected_namespaces=())

    id: int | None
    dataset_name: str
    build_run_id: int
    requested_model_types: list[ModelType]
    status: str
    started_at: datetime
    completed_at: datetime | None
    trained_model_ids: list[int]
    outcomes: list[ModelTypeOutcomeResponse]
    error_message: str | None

    @classmethod
    def from_domain(cls, run: TrainingRun, report: TrainingReport) -> TrainingRunResponse:
        return cls(
            id=run.id,
            dataset_name=run.dataset_name,
            build_run_id=run.build_run_id,
            requested_model_types=list(run.requested_model_types),
            status=run.status.value,
            started_at=run.started_at,
            completed_at=run.completed_at,
            trained_model_ids=list(report.trained_model_ids),
            outcomes=[ModelTypeOutcomeResponse.from_domain(o) for o in run.outcomes],
            error_message=run.error_message,
        )


class ConfusionMatrixResponse(BaseModel):
    true_negative: int
    false_positive: int
    false_negative: int
    true_positive: int


class PerFoldMetricsResponse(BaseModel):
    """Per-fold metrics (Requirement 9.1)."""

    fold: int
    accuracy: float
    precision: float
    recall: float
    f1_score: float
    roc_auc: float | None
    confusion_matrix: ConfusionMatrixResponse
    test_row_count: int

    @classmethod
    def from_domain(cls, metrics: PerFoldMetrics) -> PerFoldMetricsResponse:
        cm = metrics.confusion_matrix
        return cls(
            fold=metrics.fold,
            accuracy=metrics.accuracy,
            precision=metrics.precision,
            recall=metrics.recall,
            f1_score=metrics.f1_score,
            roc_auc=metrics.roc_auc,
            confusion_matrix=ConfusionMatrixResponse(
                true_negative=cm.true_negative,
                false_positive=cm.false_positive,
                false_negative=cm.false_negative,
                true_positive=cm.true_positive,
            ),
            test_row_count=metrics.test_row_count,
        )


class AggregatedMetricsResponse(BaseModel):
    """Cross-fold aggregated metrics (Requirements 6.2, 9.2)."""

    accuracy_mean: float
    accuracy_std: float
    precision_mean: float
    precision_std: float
    recall_mean: float
    recall_std: float
    f1_mean: float
    f1_std: float
    roc_auc_mean: float | None
    roc_auc_std: float | None
    evaluated_fold_count: int
    roc_auc_evaluated_fold_count: int

    @classmethod
    def from_domain(cls, metrics: AggregatedMetrics) -> AggregatedMetricsResponse:
        return cls(**{k: getattr(metrics, k) for k in cls.model_fields})


class ReproducibilityMetadataResponse(BaseModel):
    """Reproducibility metadata (Requirement 12.1)."""

    model_config = ConfigDict(protected_namespaces=())

    model_version: int
    dataset_name: str
    dataset_version: int
    dataset_checksum: str
    manifest_reference: str
    git_commit: str | None
    trained_at: datetime
    hyperparameters: dict[str, object]
    aggregated_metrics: AggregatedMetricsResponse

    @classmethod
    def from_domain(cls, metadata: ReproducibilityMetadata) -> ReproducibilityMetadataResponse:
        return cls(
            model_version=metadata.model_version,
            dataset_name=metadata.dataset_name,
            dataset_version=metadata.dataset_version,
            dataset_checksum=metadata.dataset_checksum,
            manifest_reference=metadata.manifest_reference,
            git_commit=metadata.git_commit,
            trained_at=metadata.trained_at,
            hyperparameters=metadata.hyperparameters,
            aggregated_metrics=AggregatedMetricsResponse.from_domain(metadata.aggregated_metrics),
        )


class TrainedModelResponse(BaseModel):
    """A trained model's summary (Requirement 14.3)."""

    model_config = ConfigDict(protected_namespaces=())

    id: int | None
    model_name: str
    model_type: ModelType
    model_version: int
    training_run_id: int
    dataset_name: str
    dataset_version: int
    artifact_path: str
    aggregated_metrics: AggregatedMetricsResponse
    feature_importance: dict[str, float]

    @classmethod
    def from_domain(cls, model: TrainedModel) -> TrainedModelResponse:
        return cls(
            id=model.id,
            model_name=model.model_name,
            model_type=model.model_type,
            model_version=model.model_version,
            training_run_id=model.training_run_id,
            dataset_name=model.dataset_name,
            dataset_version=model.dataset_version,
            artifact_path=model.artifact_path,
            aggregated_metrics=AggregatedMetricsResponse.from_domain(model.aggregated_metrics),
            feature_importance=model.feature_importance,
        )


class MetricsReportResponse(BaseModel):
    """A model's full metrics report (Requirements 11.2, 14.5)."""

    model_config = ConfigDict(protected_namespaces=())

    model_name: str
    model_version: int
    per_fold_metrics: list[PerFoldMetricsResponse]
    aggregated_metrics: AggregatedMetricsResponse
    feature_importance: dict[str, float]

    @classmethod
    def from_domain(cls, report: MetricsReport) -> MetricsReportResponse:
        return cls(
            model_name=report.model_name,
            model_version=report.model_version,
            per_fold_metrics=[
                PerFoldMetricsResponse.from_domain(m) for m in report.per_fold_metrics
            ],
            aggregated_metrics=AggregatedMetricsResponse.from_domain(report.aggregated_metrics),
            feature_importance=report.feature_importance,
        )
