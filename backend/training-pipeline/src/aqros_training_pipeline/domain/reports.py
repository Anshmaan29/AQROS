"""The Report_Generator — builds Training_Report and Metrics_Report.

Pure domain logic, no I/O. ``build_training_report`` summarizes a
``TrainingRun``'s request parameters and every resulting ``TrainedModel`` id
(Requirement 11.1). ``build_metrics_report`` echoes a ``TrainedModel``'s
per-fold metrics, aggregated metrics, and feature importance unmodified
(Requirement 11.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aqros_training_pipeline.domain.models import (
    AggregatedMetrics,
    ModelType,
    ModelTypeOutcome,
    PerFoldMetrics,
    TrainedModel,
    TrainingRun,
    TrainingRunStatus,
)


@dataclass(frozen=True, slots=True)
class TrainingReport:
    """Summary of a ``TrainingRun``: its request parameters and produced models."""

    training_run_id: int | None
    dataset_name: str
    build_run_id: int
    requested_model_types: tuple[ModelType, ...]
    status: TrainingRunStatus
    trained_model_ids: tuple[int, ...]
    outcomes: tuple[ModelTypeOutcome, ...]
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class MetricsReport:
    """A ``TrainedModel``'s metrics, echoed unmodified (Requirement 11.2)."""

    model_name: str
    model_version: int
    per_fold_metrics: tuple[PerFoldMetrics, ...]
    aggregated_metrics: AggregatedMetrics
    feature_importance: dict[str, float] = field(default_factory=dict)


def build_training_report(run: TrainingRun) -> TrainingReport:
    """Build a ``TrainingReport`` listing exactly the ``TrainedModel`` ids produced.

    The reported ids are exactly the non-``None`` ``trained_model_id`` values
    across ``run.outcomes`` (Requirement 11.1) — a ``failed`` run may still
    list the ids of sibling model types that trained successfully
    (Key Design Decision 8, revised).
    """
    trained_model_ids = tuple(
        outcome.trained_model_id for outcome in run.outcomes if outcome.trained_model_id is not None
    )
    return TrainingReport(
        training_run_id=run.id,
        dataset_name=run.dataset_name,
        build_run_id=run.build_run_id,
        requested_model_types=run.requested_model_types,
        status=run.status,
        trained_model_ids=trained_model_ids,
        outcomes=run.outcomes,
        error_message=run.error_message,
    )


def build_metrics_report(trained_model: TrainedModel) -> MetricsReport:
    """Build a ``MetricsReport`` echoing the model's metrics unmodified (Req 11.2)."""
    return MetricsReport(
        model_name=trained_model.model_name,
        model_version=trained_model.model_version,
        per_fold_metrics=trained_model.per_fold_metrics,
        aggregated_metrics=trained_model.aggregated_metrics,
        feature_importance=dict(trained_model.feature_importance),
    )
