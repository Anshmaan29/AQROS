"""Domain services: TrainingPipelineService (write) and TrainingQueryService (read).

``TrainingPipelineService.create_training_run`` orchestrates the full
pipeline synchronously (Key Design Decision 7): fetch build run + manifest
(404 halts immediately, Requirement 2.3), download artifact (zero-retry
fail-fast, Key Design Decision 6), verify checksum + leakage, partition
folds, then for each requested ``ModelType`` fit -> evaluate -> aggregate ->
extract feature importance -> assign version -> write artifact -> record
reproducibility metadata -> persist ``TrainedModel``.

Run status follows the all-or-nothing rule (Key Design Decision 8, revised):
``succeeded`` iff every requested ``ModelType`` produced a persisted
``TrainedModel``; ``failed`` if any failed or if verification / zero-folds
rejected the run earlier. Successfully-persisted siblings are never rolled
back when another type fails.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime

import joblib  # type: ignore[import-untyped]
import numpy as np
import pandas as pd

from aqros_training_pipeline.domain import (
    evaluation,
    feature_importance,
    partitioning,
    reports,
    trainers,
    verification,
    versioning,
)
from aqros_training_pipeline.domain.models import (
    DatasetManifest,
    FoldFrames,
    ModelType,
    ModelTypeOutcome,
    PerFoldMetrics,
    ReproducibilityMetadata,
    TrainedModel,
    TrainingRequest,
    TrainingRun,
    TrainingRunStatus,
)
from aqros_training_pipeline.domain.ports import (
    ArtifactStore,
    DatasetBuilderClient,
    DatasetBuildRunNotFoundError,
    GitInfoProvider,
    TrainedModelRepository,
    TrainingRunRepository,
    UpstreamSourceError,
)


class TrainingRunRejectedError(RuntimeError):
    """Raised internally when pre-training gates reject a run (verification / zero folds).

    Carries a human-readable ``reason`` recorded on the ``TrainingRun``. Not
    a per-``ModelType`` failure — this aborts the whole run before any model
    is trained.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class TrainingPipelineService:
    """Orchestrates one training run end-to-end (design.md Section 9)."""

    def __init__(
        self,
        *,
        dataset_builder_client: DatasetBuilderClient,
        artifact_store: ArtifactStore,
        training_run_repository: TrainingRunRepository,
        trained_model_repository: TrainedModelRepository,
        git_info_provider: GitInfoProvider,
    ) -> None:
        self._client = dataset_builder_client
        self._artifacts = artifact_store
        self._runs = training_run_repository
        self._models = trained_model_repository
        self._git = git_info_provider

    async def create_training_run(self, request: TrainingRequest) -> TrainingRun:
        """Create, execute, and persist one ``TrainingRun`` for ``request``.

        Every run is recorded ``succeeded`` or ``failed`` (never left
        ``running``): the body is wrapped so any unexpected error still
        completes the run as ``failed``.

        Raises ``DatasetBuildRunNotFoundError`` (mapped to 404 by the route)
        or ``UpstreamSourceError`` (mapped to 502) before a run is persisted;
        all later failures are captured on the persisted run.
        """
        started_at = datetime.now(UTC)
        run = TrainingRun(
            dataset_name=request.dataset_name,
            build_run_id=request.build_run_id,
            requested_model_types=request.model_types,
            status=TrainingRunStatus.RUNNING,
            started_at=started_at,
        )
        run = await self._runs.create_run(run)
        assert run.id is not None

        # Upstream retrieval happens before the run is considered recoverable;
        # a 404/upstream error fails the run and re-raises for the route layer.
        try:
            build_run = await self._client.get_build_run(request.build_run_id)
            manifest = await self._client.get_manifest(request.build_run_id)
            downloaded = await self._client.download_dataset(request.build_run_id)
        except (DatasetBuildRunNotFoundError, UpstreamSourceError) as exc:
            await self._fail_run(run, reason=str(exc))
            raise

        try:
            verify_result = verification.verify(manifest, downloaded, build_run)
            if not verify_result.passed:
                raise TrainingRunRejectedError(verify_result.reason or "verification failed")

            dataframe = pd.read_parquet(io.BytesIO(downloaded))
            folds: dict[int, FoldFrames] = partitioning.partition(dataframe)

            outcomes: list[ModelTypeOutcome] = []
            for model_type in request.model_types:
                outcome = await self._train_one_type(
                    model_type=model_type,
                    request=request,
                    manifest=manifest,
                    folds=folds,
                    training_run_id=run.id,
                )
                outcomes.append(outcome)

            all_succeeded = all(o.trained_model_id is not None for o in outcomes)
            if all_succeeded:
                status = TrainingRunStatus.SUCCEEDED
                error_message = None
            else:
                status = TrainingRunStatus.FAILED
                if all(o.trained_model_id is None for o in outcomes):
                    error_message = "all requested model types failed"
                else:
                    error_message = "one or more requested model types failed"

            completed = self._with_completion(
                run, status=status, outcomes=tuple(outcomes), error_message=error_message
            )
            await self._runs.complete_run(completed)
            return completed
        except (partitioning.NoEvaluableFoldsError, TrainingRunRejectedError) as exc:
            return await self._fail_run(run, reason=str(exc))
        except Exception as exc:  # every run must reach a terminal state
            return await self._fail_run(run, reason=f"unexpected training error: {exc}")

    async def _train_one_type(
        self,
        *,
        model_type: ModelType,
        request: TrainingRequest,
        manifest: DatasetManifest,
        folds: dict[int, FoldFrames],
        training_run_id: int,
    ) -> ModelTypeOutcome:
        """Train, evaluate, and persist one ``ModelType``; never raises.

        Returns a ``ModelTypeOutcome`` with ``trained_model_id`` set on
        success or ``error_message`` set on failure — a single type's failure
        is isolated here so sibling types keep their persisted results
        (Key Design Decision 8, revised).
        """
        feature_names = manifest.feature_names
        try:
            overrides = request.hyperparameters.get(model_type, {})
            fitted_per_fold = trainers.fit_per_fold(model_type, folds, overrides, feature_names)

            per_fold_metrics: list[PerFoldMetrics] = []
            for fold_id, fold_frames in folds.items():
                test_frame = fold_frames.test
                if test_frame.empty:
                    continue
                estimator = fitted_per_fold[fold_id]
                features = test_frame.loc[:, list(feature_names)]
                y_true = test_frame[trainers.LABEL_COLUMN].to_numpy()
                y_pred = np.asarray(estimator.predict(features))
                proba = np.asarray(estimator.predict_proba(features))
                y_proba = proba[:, 1] if proba.ndim == 2 and proba.shape[1] >= 2 else proba
                per_fold_metrics.append(evaluation.evaluate_fold(fold_id, y_true, y_pred, y_proba))

            aggregated = evaluation.aggregate(per_fold_metrics)

            representative_fold = max(fitted_per_fold)
            representative_model = fitted_per_fold[representative_fold]
            importance = feature_importance.extract(representative_model, model_type, feature_names)

            model_name = versioning.build_model_name(request.dataset_name, model_type)
            version = await versioning.assign_version(model_name, self._models)

            artifact_bytes = self._serialize(representative_model)
            artifact_path = await self._artifacts.write_artifact(
                model_name, version, artifact_bytes
            )

            git_commit = await self._git.get_commit_sha()
            merged_hyperparameters = trainers.merge_hyperparameters(model_type, dict(overrides))
            trained_at = datetime.now(UTC)
            metadata = ReproducibilityMetadata(
                model_version=version,
                dataset_name=manifest.dataset_name,
                dataset_version=manifest.dataset_version,
                dataset_checksum=manifest.checksum,
                manifest_reference=str(manifest.build_run_id),
                git_commit=git_commit,
                trained_at=trained_at,
                hyperparameters=merged_hyperparameters,
                aggregated_metrics=aggregated,
            )

            trained_model = TrainedModel(
                model_name=model_name,
                model_type=model_type,
                model_version=version,
                training_run_id=training_run_id,
                dataset_name=manifest.dataset_name,
                dataset_version=manifest.dataset_version,
                artifact_path=artifact_path,
                per_fold_metrics=tuple(per_fold_metrics),
                aggregated_metrics=aggregated,
                feature_importance=importance,
                reproducibility_metadata=metadata,
                created_at=trained_at,
            )
            persisted = await self._models.create_trained_model(trained_model)
            return ModelTypeOutcome(
                model_type=model_type,
                trained_model_id=persisted.id,
                error_message=None,
            )
        except Exception as exc:  # isolate this type's failure
            return ModelTypeOutcome(
                model_type=model_type,
                trained_model_id=None,
                error_message=str(exc),
            )

    @staticmethod
    def _serialize(model: object) -> bytes:
        """Serialize any of the four estimator types to bytes via joblib (Decision 1)."""
        buffer = io.BytesIO()
        joblib.dump(model, buffer)
        return buffer.getvalue()

    @staticmethod
    def _with_completion(
        run: TrainingRun,
        *,
        status: TrainingRunStatus,
        outcomes: tuple[ModelTypeOutcome, ...],
        error_message: str | None,
    ) -> TrainingRun:
        """Return a copy of ``run`` with terminal status/outcomes/timestamp set."""
        return TrainingRun(
            dataset_name=run.dataset_name,
            build_run_id=run.build_run_id,
            requested_model_types=run.requested_model_types,
            status=status,
            started_at=run.started_at,
            outcomes=outcomes,
            completed_at=datetime.now(UTC),
            error_message=error_message,
            id=run.id,
        )

    async def _fail_run(self, run: TrainingRun, *, reason: str) -> TrainingRun:
        """Mark ``run`` failed with ``reason`` and persist the completion."""
        failed = self._with_completion(
            run,
            status=TrainingRunStatus.FAILED,
            outcomes=run.outcomes,
            error_message=reason,
        )
        await self._runs.complete_run(failed)
        return failed


class TrainingQueryService:
    """Read-only wrappers over the run and trained-model repositories (Req 14)."""

    def __init__(
        self,
        *,
        training_run_repository: TrainingRunRepository,
        trained_model_repository: TrainedModelRepository,
    ) -> None:
        self._runs = training_run_repository
        self._models = trained_model_repository

    async def get_run(self, run_id: int) -> TrainingRun | None:
        """Return the ``TrainingRun`` with ``run_id`` or ``None``."""
        return await self._runs.get_run(run_id)

    async def list_runs(
        self, *, dataset_name: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[TrainingRun]:
        """List training runs, optionally filtered by dataset name."""
        return await self._runs.list_runs(dataset_name=dataset_name, limit=limit, offset=offset)

    async def get_trained_model(self, model_name: str, version: int) -> TrainedModel | None:
        """Return one ``TrainedModel`` by composite key or ``None``."""
        return await self._models.get_trained_model(model_name, version)

    async def list_trained_models(self, model_name: str | None = None) -> list[TrainedModel]:
        """List trained models, optionally filtered by ``model_name``."""
        return await self._models.list_trained_models(model_name)

    async def get_metrics_report(
        self, model_name: str, version: int
    ) -> reports.MetricsReport | None:
        """Return the ``MetricsReport`` for one trained model, or ``None`` if missing."""
        model = await self._models.get_trained_model(model_name, version)
        if model is None:
            return None
        return reports.build_metrics_report(model)
