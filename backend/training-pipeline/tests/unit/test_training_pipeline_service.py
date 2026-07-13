"""Property + example tests for the TrainingPipelineService orchestration (task 8.9)."""

from __future__ import annotations

import hashlib

import pytest

from aqros_training_pipeline.domain.models import (
    ModelType,
    TrainingRequest,
    TrainingRunStatus,
)
from aqros_training_pipeline.domain.ports import (
    DatasetBuildRunNotFoundError,
    UpstreamSourceError,
)
from aqros_training_pipeline.domain.services import TrainingPipelineService
from tests.unit.builders import (
    FEATURE_NAMES,
    make_build_run,
    make_dataframe,
    make_manifest,
    to_parquet_bytes,
)
from tests.unit.fakes import (
    FakeArtifactStore,
    FakeDatasetBuilderClient,
    FakeGitInfoProvider,
    FakeTrainedModelRepository,
    FakeTrainingRunRepository,
)

DATASET_NAME = "aapl_5d_direction"


def _make_valid_upstream(*, leakage: bool | None = True):
    df = make_dataframe(n_folds=2, rows_per_role=16)
    artifact = to_parquet_bytes(df)
    checksum = hashlib.sha256(artifact).hexdigest()
    manifest = make_manifest(checksum=checksum, dataset_name=DATASET_NAME)
    build_run = make_build_run(leakage_audit_passed=leakage, dataset_name=DATASET_NAME)
    return build_run, manifest, artifact


def _make_service(client, *, artifact_store=None, git=None):
    return TrainingPipelineService(
        dataset_builder_client=client,
        artifact_store=artifact_store or FakeArtifactStore(),
        training_run_repository=FakeTrainingRunRepository(),
        trained_model_repository=FakeTrainedModelRepository(),
        git_info_provider=git or FakeGitInfoProvider(),
    )


def _request(*model_types: ModelType) -> TrainingRequest:
    return TrainingRequest(
        dataset_name=DATASET_NAME,
        build_run_id=42,
        model_types=tuple(model_types),
    )


async def test_happy_path_succeeds() -> None:
    build_run, manifest, artifact = _make_valid_upstream()
    client = FakeDatasetBuilderClient(build_run=build_run, manifest=manifest, artifact=artifact)
    service = _make_service(client)
    run = await service.create_training_run(_request(ModelType.LOGISTIC_REGRESSION))
    assert run.status is TrainingRunStatus.SUCCEEDED
    assert len(run.outcomes) == 1
    assert run.outcomes[0].trained_model_id is not None


# Feature: training-pipeline, Property 1: upstream failure is fail-closed with zero retry.
async def test_property_1_upstream_error_fails_closed() -> None:
    client = FakeDatasetBuilderClient(upstream_error=True)
    service = _make_service(client)
    with pytest.raises(UpstreamSourceError):
        await service.create_training_run(_request(ModelType.LOGISTIC_REGRESSION))
    # No fallback source contacted, at most one call attempted.
    assert client.calls == ["get_build_run"]


# Feature: training-pipeline, Property 2: 404 halts immediately, no download attempted.
async def test_property_2_not_found_halts_before_download() -> None:
    client = FakeDatasetBuilderClient(not_found=True)
    service = _make_service(client)
    with pytest.raises(DatasetBuildRunNotFoundError):
        await service.create_training_run(_request(ModelType.LOGISTIC_REGRESSION))
    assert "download_dataset" not in client.calls


async def test_checksum_mismatch_marks_failed() -> None:
    build_run, manifest, _artifact = _make_valid_upstream()
    client = FakeDatasetBuilderClient(
        build_run=build_run, manifest=manifest, artifact=b"tampered-bytes"
    )
    service = _make_service(client)
    run = await service.create_training_run(_request(ModelType.LOGISTIC_REGRESSION))
    assert run.status is TrainingRunStatus.FAILED
    assert "checksum" in (run.error_message or "").lower()


async def test_leakage_not_passed_marks_failed() -> None:
    build_run, manifest, artifact = _make_valid_upstream(leakage=False)
    client = FakeDatasetBuilderClient(build_run=build_run, manifest=manifest, artifact=artifact)
    service = _make_service(client)
    run = await service.create_training_run(_request(ModelType.LOGISTIC_REGRESSION))
    assert run.status is TrainingRunStatus.FAILED


# Feature: training-pipeline, Property 19: reproducibility metadata fields match source.
async def test_property_19_reproducibility_metadata_matches_source() -> None:
    build_run, manifest, artifact = _make_valid_upstream()
    client = FakeDatasetBuilderClient(build_run=build_run, manifest=manifest, artifact=artifact)
    models = FakeTrainedModelRepository()
    service = TrainingPipelineService(
        dataset_builder_client=client,
        artifact_store=FakeArtifactStore(),
        training_run_repository=FakeTrainingRunRepository(),
        trained_model_repository=models,
        git_info_provider=FakeGitInfoProvider(commit_sha="deadbeef"),
    )
    await service.create_training_run(_request(ModelType.LOGISTIC_REGRESSION))
    stored = await models.list_trained_models()
    assert len(stored) == 1
    meta = stored[0].reproducibility_metadata
    assert meta.dataset_name == manifest.dataset_name
    assert meta.dataset_version == manifest.dataset_version
    assert meta.dataset_checksum == manifest.checksum
    assert meta.manifest_reference == str(manifest.build_run_id)
    assert meta.git_commit == "deadbeef"


# Feature: training-pipeline, Property 20: absent git commit still allows persistence.
async def test_property_20_absent_git_commit_still_persists() -> None:
    build_run, manifest, artifact = _make_valid_upstream()
    client = FakeDatasetBuilderClient(build_run=build_run, manifest=manifest, artifact=artifact)
    models = FakeTrainedModelRepository()
    service = TrainingPipelineService(
        dataset_builder_client=client,
        artifact_store=FakeArtifactStore(),
        training_run_repository=FakeTrainingRunRepository(),
        trained_model_repository=models,
        git_info_provider=FakeGitInfoProvider(commit_sha=None),
    )
    run = await service.create_training_run(_request(ModelType.LOGISTIC_REGRESSION))
    assert run.status is TrainingRunStatus.SUCCEEDED
    stored = await models.list_trained_models()
    assert stored[0].reproducibility_metadata.git_commit is None


# Feature: training-pipeline, Property 26: all-or-nothing multi-ModelType run status;
# successful siblings survive a failure.
async def test_property_26_all_or_nothing_retains_successful_sibling() -> None:
    build_run, manifest, artifact = _make_valid_upstream()
    client = FakeDatasetBuilderClient(build_run=build_run, manifest=manifest, artifact=artifact)
    # Make xgboost's artifact write fail; logistic_regression should still persist.
    failing_name = f"{DATASET_NAME}__{ModelType.XGBOOST.value}"
    models = FakeTrainedModelRepository()
    service = TrainingPipelineService(
        dataset_builder_client=client,
        artifact_store=FakeArtifactStore(fail_on={failing_name}),
        training_run_repository=FakeTrainingRunRepository(),
        trained_model_repository=models,
        git_info_provider=FakeGitInfoProvider(),
    )
    run = await service.create_training_run(
        _request(ModelType.LOGISTIC_REGRESSION, ModelType.XGBOOST)
    )
    assert run.status is TrainingRunStatus.FAILED
    outcomes = {o.model_type: o for o in run.outcomes}
    assert outcomes[ModelType.LOGISTIC_REGRESSION].trained_model_id is not None
    assert outcomes[ModelType.XGBOOST].trained_model_id is None
    # The successful sibling is retained, not rolled back.
    retained = await models.list_trained_models(f"{DATASET_NAME}__logistic_regression")
    assert len(retained) == 1


async def test_all_types_succeed_marks_succeeded() -> None:
    build_run, manifest, artifact = _make_valid_upstream()
    client = FakeDatasetBuilderClient(build_run=build_run, manifest=manifest, artifact=artifact)
    service = _make_service(client)
    run = await service.create_training_run(
        _request(ModelType.LOGISTIC_REGRESSION, ModelType.RANDOM_FOREST)
    )
    assert run.status is TrainingRunStatus.SUCCEEDED
    assert all(o.trained_model_id is not None for o in run.outcomes)
    assert FEATURE_NAMES  # keep import referenced
