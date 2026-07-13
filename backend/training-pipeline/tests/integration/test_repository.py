"""Repository integration tests against a real Postgres (task 9.3)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from aqros_training_pipeline.adapters.repository import (
    SqlAlchemyTrainedModelRepository,
    SqlAlchemyTrainingRunRepository,
)
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

pytestmark = pytest.mark.integration

_AGG = AggregatedMetrics(0.6, 0.1, 0.6, 0.1, 0.6, 0.1, 0.6, 0.1, 0.7, 0.05, 2, 2)


def _run() -> TrainingRun:
    return TrainingRun(
        dataset_name="aapl_5d_direction",
        build_run_id=42,
        requested_model_types=(ModelType.RANDOM_FOREST,),
        status=TrainingRunStatus.RUNNING,
        started_at=datetime.now(UTC),
    )


def _model(
    training_run_id: int, version: int, name: str = "aapl_5d_direction__random_forest"
) -> TrainedModel:
    meta = ReproducibilityMetadata(
        model_version=version,
        dataset_name="aapl_5d_direction",
        dataset_version=1,
        dataset_checksum="c",
        manifest_reference="42",
        git_commit=None,
        trained_at=datetime.now(UTC),
        hyperparameters={"n_estimators": 200},
        aggregated_metrics=_AGG,
    )
    per_fold = (PerFoldMetrics(0, 0.6, 0.6, 0.6, 0.6, 0.7, ConfusionMatrix(1, 1, 1, 1), 4),)
    return TrainedModel(
        model_name=name,
        model_type=ModelType.RANDOM_FOREST,
        model_version=version,
        training_run_id=training_run_id,
        dataset_name="aapl_5d_direction",
        dataset_version=1,
        artifact_path="p",
        per_fold_metrics=per_fold,
        aggregated_metrics=_AGG,
        feature_importance={"f0": 0.5},
        reproducibility_metadata=meta,
        created_at=datetime.now(UTC),
    )


async def test_run_round_trip(db_session: AsyncSession) -> None:
    repo = SqlAlchemyTrainingRunRepository(db_session)
    created = await repo.create_run(_run())
    assert created.id is not None
    fetched = await repo.get_run(created.id)
    assert fetched is not None
    assert fetched.dataset_name == "aapl_5d_direction"


async def test_complete_run_updates_status(db_session: AsyncSession) -> None:
    repo = SqlAlchemyTrainingRunRepository(db_session)
    created = await repo.create_run(_run())
    from dataclasses import replace

    completed = replace(
        created,
        status=TrainingRunStatus.SUCCEEDED,
        completed_at=datetime.now(UTC),
        outcomes=(
            ModelTypeOutcome(ModelType.RANDOM_FOREST, trained_model_id=1, error_message=None),
        ),
    )
    await repo.complete_run(completed)
    fetched = await repo.get_run(created.id)  # type: ignore[arg-type]
    assert fetched is not None
    assert fetched.status is TrainingRunStatus.SUCCEEDED
    assert len(fetched.outcomes) == 1


async def test_trained_model_round_trip_and_versioning(db_session: AsyncSession) -> None:
    run_repo = SqlAlchemyTrainingRunRepository(db_session)
    model_repo = SqlAlchemyTrainedModelRepository(db_session)
    run = await run_repo.create_run(_run())
    assert run.id is not None

    assert await model_repo.get_latest_version("aapl_5d_direction__random_forest") is None
    await model_repo.create_trained_model(_model(run.id, 1))
    await model_repo.create_trained_model(_model(run.id, 2))
    assert await model_repo.get_latest_version("aapl_5d_direction__random_forest") == 2

    fetched = await model_repo.get_trained_model("aapl_5d_direction__random_forest", 1)
    assert fetched is not None
    assert fetched.per_fold_metrics[0].accuracy == 0.6
    assert fetched.feature_importance == {"f0": 0.5}


async def test_unique_constraint_rejects_duplicate_version(db_session: AsyncSession) -> None:
    run_repo = SqlAlchemyTrainingRunRepository(db_session)
    model_repo = SqlAlchemyTrainedModelRepository(db_session)
    run = await run_repo.create_run(_run())
    assert run.id is not None
    await model_repo.create_trained_model(_model(run.id, 1))
    with pytest.raises(IntegrityError):
        await model_repo.create_trained_model(_model(run.id, 1))
