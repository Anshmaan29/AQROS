"""Property tests for the Model_Versioner (task 8.7)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aqros_training_pipeline.domain import versioning
from aqros_training_pipeline.domain.models import (
    AggregatedMetrics,
    ModelType,
    ReproducibilityMetadata,
    TrainedModel,
)

from .fakes import FakeTrainedModelRepository

_AGG = AggregatedMetrics(0.5, 0.0, 0.5, 0.0, 0.5, 0.0, 0.5, 0.0, None, None, 1, 0)


def _model(model_name: str, model_type: ModelType, version: int) -> TrainedModel:
    meta = ReproducibilityMetadata(
        model_version=version,
        dataset_name="ds",
        dataset_version=1,
        dataset_checksum="c",
        manifest_reference="42",
        git_commit=None,
        trained_at=datetime.now(UTC),
        hyperparameters={},
        aggregated_metrics=_AGG,
    )
    return TrainedModel(
        model_name=model_name,
        model_type=model_type,
        model_version=version,
        training_run_id=1,
        dataset_name="ds",
        dataset_version=1,
        artifact_path="p",
        per_fold_metrics=(),
        aggregated_metrics=_AGG,
        feature_importance={},
        reproducibility_metadata=meta,
        created_at=datetime.now(UTC),
    )


def test_build_model_name_is_composite() -> None:
    assert (
        versioning.build_model_name("aapl_5d_direction", ModelType.RANDOM_FOREST)
        == "aapl_5d_direction__random_forest"
    )


# Feature: training-pipeline, Property 12: version assignment is monotonic, unique,
# and immutable per composite model name, and independent across dataset names.
@settings(max_examples=100)
@given(n=st.integers(min_value=1, max_value=8))
async def test_property_12_monotonic_and_independent(n: int) -> None:
    repo = FakeTrainedModelRepository()
    name_a = versioning.build_model_name("aapl", ModelType.RANDOM_FOREST)
    name_b = versioning.build_model_name("msft", ModelType.RANDOM_FOREST)

    assigned_a: list[int] = []
    for _ in range(n):
        v = await versioning.assign_version(name_a, repo)
        assigned_a.append(v)
        await repo.create_trained_model(_model(name_a, ModelType.RANDOM_FOREST, v))

    # Monotonic 1..n, unique, starting at 1.
    assert assigned_a == list(range(1, n + 1))

    # A different dataset name starts its own independent sequence at 1.
    v_b = await versioning.assign_version(name_b, repo)
    assert v_b == 1


async def test_duplicate_version_rejected_by_repository() -> None:
    repo = FakeTrainedModelRepository()
    name = versioning.build_model_name("aapl", ModelType.XGBOOST)
    await repo.create_trained_model(_model(name, ModelType.XGBOOST, 1))
    with pytest.raises(ValueError):
        await repo.create_trained_model(_model(name, ModelType.XGBOOST, 1))
