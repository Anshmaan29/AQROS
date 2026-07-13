"""Property tests for the Report_Generator (task 8.8)."""

from __future__ import annotations

from datetime import UTC, datetime

from hypothesis import given, settings
from hypothesis import strategies as st

from aqros_training_pipeline.domain import reports
from aqros_training_pipeline.domain.models import (
    AggregatedMetrics,
    ConfusionMatrix,
    ModelType,
    ModelTypeOutcome,
    PerFoldMetrics,
    ReproducibilityMetadata,
    TrainedModel,
    TrainingRun,
    TrainingRunStatus,
)

_AGG = AggregatedMetrics(0.6, 0.1, 0.6, 0.1, 0.6, 0.1, 0.6, 0.1, 0.7, 0.05, 2, 2)


# Feature: training-pipeline, Property 17: Training_Report lists exactly the
# Trained_Model ids the run produced.
@settings(max_examples=100)
@given(
    ids=st.lists(st.integers(min_value=1, max_value=1000), min_size=0, max_size=4, unique=True),
    with_failure=st.booleans(),
)
def test_property_17_report_lists_produced_ids(ids: list[int], with_failure: bool) -> None:
    outcomes = [
        ModelTypeOutcome(model_type=ModelType.RANDOM_FOREST, trained_model_id=i, error_message=None)
        for i in ids
    ]
    if with_failure:
        outcomes.append(
            ModelTypeOutcome(
                model_type=ModelType.XGBOOST, trained_model_id=None, error_message="boom"
            )
        )
    run = TrainingRun(
        dataset_name="ds",
        build_run_id=42,
        requested_model_types=(ModelType.RANDOM_FOREST,),
        status=TrainingRunStatus.SUCCEEDED,
        started_at=datetime.now(UTC),
        outcomes=tuple(outcomes),
        id=1,
    )
    report = reports.build_training_report(run)
    assert set(report.trained_model_ids) == set(ids)


# Feature: training-pipeline, Property 18: Metrics_Report round-trips a
# Trained_Model's data unchanged.
@settings(max_examples=100)
@given(
    accuracy=st.floats(0.0, 1.0),
    importance=st.floats(-5.0, 5.0),
)
def test_property_18_metrics_report_round_trip(accuracy: float, importance: float) -> None:
    per_fold = (
        PerFoldMetrics(
            fold=0,
            accuracy=accuracy,
            precision=accuracy,
            recall=accuracy,
            f1_score=accuracy,
            roc_auc=None,
            confusion_matrix=ConfusionMatrix(1, 0, 0, 1),
            test_row_count=2,
        ),
    )
    meta = ReproducibilityMetadata(
        model_version=1,
        dataset_name="ds",
        dataset_version=1,
        dataset_checksum="c",
        manifest_reference="42",
        git_commit=None,
        trained_at=datetime.now(UTC),
        hyperparameters={},
        aggregated_metrics=_AGG,
    )
    model = TrainedModel(
        model_name="ds__random_forest",
        model_type=ModelType.RANDOM_FOREST,
        model_version=1,
        training_run_id=1,
        dataset_name="ds",
        dataset_version=1,
        artifact_path="p",
        per_fold_metrics=per_fold,
        aggregated_metrics=_AGG,
        feature_importance={"f0": importance},
        reproducibility_metadata=meta,
        created_at=datetime.now(UTC),
    )
    report = reports.build_metrics_report(model)
    assert report.per_fold_metrics == per_fold
    assert report.aggregated_metrics == _AGG
    assert report.feature_importance == {"f0": importance}
