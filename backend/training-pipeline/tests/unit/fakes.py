"""In-memory fakes for every port, shared across unit tests (task 8.1).

No real HTTP, filesystem, or database access — these implement the same
port ABCs as the real adapters so the domain logic can be exercised in
isolation (Requirement 16.1).
"""

from __future__ import annotations

from dataclasses import replace

from aqros_training_pipeline.domain.models import (
    DatasetBuildRun,
    DatasetManifest,
    TrainedModel,
    TrainingRun,
)
from aqros_training_pipeline.domain.ports import (
    ArtifactAlreadyExistsError,
    ArtifactStore,
    DatasetBuilderClient,
    DatasetBuildRunNotFoundError,
    GitInfoProvider,
    TrainedModelRepository,
    TrainingRunRepository,
    UpstreamSourceError,
)


class FakeDatasetBuilderClient(DatasetBuilderClient):
    """Serves a preloaded build run / manifest / artifact, or raises to order."""

    def __init__(
        self,
        *,
        build_run: DatasetBuildRun | None = None,
        manifest: DatasetManifest | None = None,
        artifact: bytes = b"",
        not_found: bool = False,
        upstream_error: bool = False,
    ) -> None:
        self._build_run = build_run
        self._manifest = manifest
        self._artifact = artifact
        self._not_found = not_found
        self._upstream_error = upstream_error
        self.calls: list[str] = []

    def _guard(self) -> None:
        if self._not_found:
            raise DatasetBuildRunNotFoundError("fake: not found")
        if self._upstream_error:
            raise UpstreamSourceError("fake: upstream error")

    async def get_build_run(self, build_run_id: int) -> DatasetBuildRun:
        self.calls.append("get_build_run")
        self._guard()
        assert self._build_run is not None
        return self._build_run

    async def get_manifest(self, build_run_id: int) -> DatasetManifest:
        self.calls.append("get_manifest")
        self._guard()
        assert self._manifest is not None
        return self._manifest

    async def download_dataset(self, build_run_id: int) -> bytes:
        self.calls.append("download_dataset")
        self._guard()
        return self._artifact


class FakeArtifactStore(ArtifactStore):
    """Dict-backed artifact store enforcing write-once semantics."""

    def __init__(self, *, fail_on: set[str] | None = None) -> None:
        self._store: dict[tuple[str, int], bytes] = {}
        self._fail_on = fail_on or set()

    async def write_artifact(self, model_name: str, model_version: int, data: bytes) -> str:
        if model_name in self._fail_on:
            raise RuntimeError(f"fake: artifact write failed for {model_name}")
        key = (model_name, model_version)
        if key in self._store:
            raise ArtifactAlreadyExistsError(f"fake: {key} exists")
        self._store[key] = data
        return f"memory://{model_name}/v{model_version}"

    async def read_artifact(self, model_name: str, model_version: int) -> bytes:
        key = (model_name, model_version)
        if key not in self._store:
            raise FileNotFoundError(f"fake: no artifact for {key}")
        return self._store[key]


class FakeTrainingRunRepository(TrainingRunRepository):
    """List-backed run repository assigning sequential ids."""

    def __init__(self) -> None:
        self._runs: dict[int, TrainingRun] = {}
        self._next_id = 1

    async def create_run(self, run: TrainingRun) -> TrainingRun:
        run_id = self._next_id
        self._next_id += 1
        stored = replace(run, id=run_id)
        self._runs[run_id] = stored
        return stored

    async def complete_run(self, run: TrainingRun) -> None:
        assert run.id is not None
        self._runs[run.id] = run

    async def get_run(self, run_id: int) -> TrainingRun | None:
        return self._runs.get(run_id)

    async def list_runs(
        self, *, dataset_name: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[TrainingRun]:
        runs = list(self._runs.values())
        if dataset_name is not None:
            runs = [r for r in runs if r.dataset_name == dataset_name]
        return runs[offset : offset + limit]


class FakeTrainedModelRepository(TrainedModelRepository):
    """List-backed trained-model repository with per-name version tracking."""

    def __init__(self) -> None:
        self._models: list[TrainedModel] = []
        self._next_id = 1

    async def create_trained_model(self, model: TrainedModel) -> TrainedModel:
        key = (model.model_name, model.model_version)
        if any((m.model_name, m.model_version) == key for m in self._models):
            raise ValueError(f"duplicate (model_name, version): {key}")
        stored = replace(model, id=self._next_id)
        self._next_id += 1
        self._models.append(stored)
        return stored

    async def get_trained_model(self, model_name: str, model_version: int) -> TrainedModel | None:
        for m in self._models:
            if m.model_name == model_name and m.model_version == model_version:
                return m
        return None

    async def list_trained_models(self, model_name: str | None = None) -> list[TrainedModel]:
        if model_name is None:
            return list(self._models)
        return [m for m in self._models if m.model_name == model_name]

    async def get_latest_version(self, model_name: str) -> int | None:
        versions = [m.model_version for m in self._models if m.model_name == model_name]
        return max(versions) if versions else None


class FakeGitInfoProvider(GitInfoProvider):
    """Returns a fixed commit SHA (or ``None`` to simulate a non-git environment)."""

    def __init__(self, commit_sha: str | None = "0" * 40) -> None:
        self._commit_sha = commit_sha

    async def get_commit_sha(self) -> str | None:
        return self._commit_sha
