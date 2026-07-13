"""SQLAlchemy implementations of the domain repository ports.

Each repository maps ORM rows to/from the pure domain types in
``domain/models.py`` — business logic never sees an ORM row or session.
Mirrors ``aqros_dataset_builder.adapters.repository``.

Nested value objects (per-fold metrics, aggregated metrics, feature
importance, reproducibility metadata, per-type outcomes) are serialized to
JSON text columns, keeping the schema portable to SQLite for fast unit
tests — the same tradeoff the Dataset Builder makes with its ``*_json``
columns.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aqros_training_pipeline.adapters.orm import TrainedModelORM, TrainingRunORM
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
from aqros_training_pipeline.domain.ports import (
    TrainedModelRepository,
    TrainingRunRepository,
)


def _confusion_from_dict(data: dict[str, Any]) -> ConfusionMatrix:
    return ConfusionMatrix(
        true_negative=int(data["true_negative"]),
        false_positive=int(data["false_positive"]),
        false_negative=int(data["false_negative"]),
        true_positive=int(data["true_positive"]),
    )


def _per_fold_from_dict(data: dict[str, Any]) -> PerFoldMetrics:
    return PerFoldMetrics(
        fold=int(data["fold"]),
        accuracy=float(data["accuracy"]),
        precision=float(data["precision"]),
        recall=float(data["recall"]),
        f1_score=float(data["f1_score"]),
        roc_auc=None if data["roc_auc"] is None else float(data["roc_auc"]),
        confusion_matrix=_confusion_from_dict(data["confusion_matrix"]),
        test_row_count=int(data["test_row_count"]),
    )


def _aggregated_from_dict(data: dict[str, Any]) -> AggregatedMetrics:
    def opt(key: str) -> float | None:
        value = data[key]
        return None if value is None else float(value)

    return AggregatedMetrics(
        accuracy_mean=float(data["accuracy_mean"]),
        accuracy_std=float(data["accuracy_std"]),
        precision_mean=float(data["precision_mean"]),
        precision_std=float(data["precision_std"]),
        recall_mean=float(data["recall_mean"]),
        recall_std=float(data["recall_std"]),
        f1_mean=float(data["f1_mean"]),
        f1_std=float(data["f1_std"]),
        roc_auc_mean=opt("roc_auc_mean"),
        roc_auc_std=opt("roc_auc_std"),
        evaluated_fold_count=int(data["evaluated_fold_count"]),
        roc_auc_evaluated_fold_count=int(data["roc_auc_evaluated_fold_count"]),
    )


def _metadata_from_dict(data: dict[str, Any]) -> ReproducibilityMetadata:
    return ReproducibilityMetadata(
        model_version=int(data["model_version"]),
        dataset_name=str(data["dataset_name"]),
        dataset_version=int(data["dataset_version"]),
        dataset_checksum=str(data["dataset_checksum"]),
        manifest_reference=str(data["manifest_reference"]),
        git_commit=None if data["git_commit"] is None else str(data["git_commit"]),
        trained_at=datetime.fromisoformat(str(data["trained_at"])),
        hyperparameters=dict(data["hyperparameters"]),
        aggregated_metrics=_aggregated_from_dict(data["aggregated_metrics"]),
    )


def _to_domain_model(row: TrainedModelORM) -> TrainedModel:
    per_fold = tuple(_per_fold_from_dict(item) for item in json.loads(row.per_fold_metrics_json))
    aggregated = _aggregated_from_dict(json.loads(row.aggregated_metrics_json))
    return TrainedModel(
        model_name=row.model_name,
        model_type=ModelType(row.model_type),
        model_version=row.model_version,
        training_run_id=row.training_run_id,
        dataset_name=row.dataset_name,
        dataset_version=row.dataset_version,
        artifact_path=row.artifact_path,
        per_fold_metrics=per_fold,
        aggregated_metrics=aggregated,
        feature_importance=dict(json.loads(row.feature_importance_json)),
        reproducibility_metadata=_metadata_from_dict(json.loads(row.reproducibility_metadata_json)),
        created_at=row.created_at,
        id=row.id,
    )


def _outcome_from_dict(data: dict[str, Any]) -> ModelTypeOutcome:
    return ModelTypeOutcome(
        model_type=ModelType(str(data["model_type"])),
        trained_model_id=(
            None if data["trained_model_id"] is None else int(data["trained_model_id"])
        ),
        error_message=None if data["error_message"] is None else str(data["error_message"]),
    )


def _to_domain_run(row: TrainingRunORM) -> TrainingRun:
    return TrainingRun(
        dataset_name=row.dataset_name,
        build_run_id=row.build_run_id,
        requested_model_types=tuple(ModelType(v) for v in json.loads(row.model_types_json)),
        status=TrainingRunStatus(row.status),
        started_at=row.started_at,
        outcomes=tuple(_outcome_from_dict(o) for o in json.loads(row.outcomes_json)),
        completed_at=row.completed_at,
        error_message=row.error_message,
        id=row.id,
    )


class SqlAlchemyTrainingRunRepository(TrainingRunRepository):
    """Postgres-backed training-run audit trail repository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_run(self, run: TrainingRun) -> TrainingRun:
        orm_row = TrainingRunORM(
            dataset_name=run.dataset_name,
            build_run_id=run.build_run_id,
            model_types_json=json.dumps([mt.value for mt in run.requested_model_types]),
            hyperparameters_json=json.dumps({}),
            status=run.status.value,
            started_at=run.started_at,
            completed_at=run.completed_at,
            outcomes_json=json.dumps([asdict(o) for o in run.outcomes], default=str),
            error_message=run.error_message,
        )
        self._session.add(orm_row)
        await self._session.flush()
        return _to_domain_run(orm_row)

    async def complete_run(self, run: TrainingRun) -> None:
        if run.id is None:
            raise ValueError("Cannot complete a run that has no id (call create_run first)")
        stmt = select(TrainingRunORM).where(TrainingRunORM.id == run.id)
        orm_row = (await self._session.execute(stmt)).scalar_one()
        orm_row.status = run.status.value
        orm_row.completed_at = run.completed_at
        orm_row.outcomes_json = json.dumps([asdict(o) for o in run.outcomes], default=str)
        orm_row.error_message = run.error_message
        await self._session.flush()

    async def get_run(self, run_id: int) -> TrainingRun | None:
        stmt = select(TrainingRunORM).where(TrainingRunORM.id == run_id)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_domain_run(row) if row is not None else None

    async def list_runs(
        self, *, dataset_name: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[TrainingRun]:
        stmt = select(TrainingRunORM)
        if dataset_name is not None:
            stmt = stmt.where(TrainingRunORM.dataset_name == dataset_name)
        stmt = stmt.order_by(TrainingRunORM.started_at.desc()).limit(limit).offset(offset)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_domain_run(row) for row in rows]


class SqlAlchemyTrainedModelRepository(TrainedModelRepository):
    """Postgres-backed trained-model repository (write-once, never updates a row)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_trained_model(self, model: TrainedModel) -> TrainedModel:
        orm_row = TrainedModelORM(
            model_name=model.model_name,
            model_type=model.model_type.value,
            model_version=model.model_version,
            training_run_id=model.training_run_id,
            dataset_name=model.dataset_name,
            dataset_version=model.dataset_version,
            build_run_id=int(model.reproducibility_metadata.manifest_reference),
            artifact_path=model.artifact_path,
            per_fold_metrics_json=json.dumps([asdict(m) for m in model.per_fold_metrics]),
            aggregated_metrics_json=json.dumps(asdict(model.aggregated_metrics)),
            feature_importance_json=json.dumps(model.feature_importance),
            reproducibility_metadata_json=json.dumps(
                asdict(model.reproducibility_metadata), default=str
            ),
            created_at=model.created_at,
        )
        self._session.add(orm_row)
        await self._session.flush()
        return _to_domain_model(orm_row)

    async def get_trained_model(self, model_name: str, model_version: int) -> TrainedModel | None:
        stmt = select(TrainedModelORM).where(
            TrainedModelORM.model_name == model_name,
            TrainedModelORM.model_version == model_version,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_domain_model(row) if row is not None else None

    async def list_trained_models(self, model_name: str | None = None) -> list[TrainedModel]:
        stmt = select(TrainedModelORM)
        if model_name is not None:
            stmt = stmt.where(TrainedModelORM.model_name == model_name)
        stmt = stmt.order_by(TrainedModelORM.model_name.asc(), TrainedModelORM.model_version.asc())
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_domain_model(row) for row in rows]

    async def get_latest_version(self, model_name: str) -> int | None:
        stmt = select(func.max(TrainedModelORM.model_version)).where(
            TrainedModelORM.model_name == model_name
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()
