"""Ports (abstract interfaces) the domain depends on.

These are the seams between pure business logic and I/O. ``domain/`` and
``api/`` depend only on these protocols; concrete implementations live in
``adapters/`` and are wired in via dependency injection (``api/deps.py``) —
the same pattern ``aqros_dataset_builder.domain.ports``,
``aqros_market_data.domain.ports``, and ``aqros_feature_store.domain.ports``
establish.

``DatasetBuilderClient`` is the sole channel to the Dataset Builder's
published REST API (CLAUDE.md §7.9, Requirements 1.1-1.4, 2.1-2.2) — the
Training Pipeline never opens a database connection or issues any other
HTTP call to the Dataset Builder, Market Data, or Feature Store services.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from aqros_training_pipeline.domain.models import (
    DatasetBuildRun,
    DatasetManifest,
    TrainedModel,
    TrainingRun,
)


class DatasetBuildRunNotFoundError(RuntimeError):
    """Raised when the Dataset Builder returns 404 for a requested build run.

    Returned by any of ``get_build_run``, ``get_manifest``, or
    ``download_dataset`` when the Dataset Builder has no record of the
    given ``build_run_id`` — the caller halts immediately on this error
    without invoking any of the other two calls (Requirement 2.3).
    """


class UpstreamSourceError(RuntimeError):
    """Raised when the Dataset Builder is unreachable or returns a non-404 error.

    Covers connection failures, timeouts, and any HTTP error status other
    than 404. The ``DatasetBuilderClient`` performs zero retries of any
    kind before raising this (Key Design Decision 6) — the
    ``TrainingRun`` is failed immediately, with no fallback to another
    data source (Requirement 1.4).
    """


class DatasetBuilderClient(ABC):
    """Port for the Training Pipeline's sole upstream dependency: the Dataset Builder.

    Exactly three REST calls are ever made through this port —
    ``GET /v1/runs/{run_id}``, ``GET /v1/runs/{run_id}/manifest``, and
    ``GET /v1/runs/{run_id}/download`` — never a direct database
    connection to the Dataset Builder's own store (Requirement 1.2).
    """

    @abstractmethod
    async def get_build_run(self, build_run_id: int) -> DatasetBuildRun:
        """Fetch the ``Dataset_Build_Run`` (incl. leakage-audit fields) by id.

        Raises ``DatasetBuildRunNotFoundError`` on a 404 response and
        ``UpstreamSourceError`` on any other error or connection failure.
        """

    @abstractmethod
    async def get_manifest(self, build_run_id: int) -> DatasetManifest:
        """Fetch the ``Dataset_Manifest`` for a given build run id.

        Raises ``DatasetBuildRunNotFoundError`` on a 404 response and
        ``UpstreamSourceError`` on any other error or connection failure.
        """

    @abstractmethod
    async def download_dataset(self, build_run_id: int) -> bytes:
        """Download the raw ``Dataset_Artifact`` bytes for a given build run id.

        Raises ``DatasetBuildRunNotFoundError`` on a 404 response and
        ``UpstreamSourceError`` on any other error or connection failure.
        """


class ArtifactAlreadyExistsError(RuntimeError):
    """Raised by ``ArtifactStore.write_artifact`` when the (model_name, model_version)
    pair has already been persisted (Requirement 13.2) — artifacts are
    versioned and immutable, never overwritten.
    """


class ArtifactStore(ABC):
    """Port for versioned, immutable, swappable persistence of ``Model_Artifact`` bytes.

    Deliberately simplified relative to the Dataset Builder's
    ``DatasetStorage`` port — no manifest responsibility here, since
    ``Reproducibility_Metadata`` already lives in ``TrainedModelRepository``.
    Takes/returns plain ``bytes`` only, so a filesystem-backed
    implementation is a drop-in swap for an S3-backed one with no change
    to any caller (Requirement 13.4).
    """

    @abstractmethod
    async def write_artifact(self, model_name: str, model_version: int, data: bytes) -> str:
        """Persist ``data`` under ``(model_name, model_version)`` and return its storage path.

        Raises ``ArtifactAlreadyExistsError`` if that pair has already been
        written, rather than overwriting it (Requirement 13.2).
        """

    @abstractmethod
    async def read_artifact(self, model_name: str, model_version: int) -> bytes:
        """Return the exact bytes previously written for ``(model_name, model_version)``."""


class TrainingRunRepository(ABC):
    """Port for persisting the audit trail of training-pipeline executions."""

    @abstractmethod
    async def create_run(self, run: TrainingRun) -> TrainingRun:
        """Persist a new run record (typically ``pending``/``running``) and return it with its id."""

    @abstractmethod
    async def complete_run(self, run: TrainingRun) -> None:
        """Update an existing run record with its final status, outcomes, and metadata."""

    @abstractmethod
    async def get_run(self, run_id: int) -> TrainingRun | None:
        """Fetch one training run by id."""

    @abstractmethod
    async def list_runs(
        self, *, dataset_name: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[TrainingRun]:
        """List training runs, most recent first, optionally filtered by dataset name."""


class TrainedModelRepository(ABC):
    """Port for persisting and retrieving trained-model metadata.

    ``Trained_Model`` rows are write-once: no method here ever updates an
    existing row (Requirement 8.3's immutability) — ``create_trained_model``
    is the only write operation.
    """

    @abstractmethod
    async def create_trained_model(self, model: TrainedModel) -> TrainedModel:
        """Persist a new, immutable ``Trained_Model`` row and return it with its id."""

    @abstractmethod
    async def get_trained_model(self, model_name: str, model_version: int) -> TrainedModel | None:
        """Fetch one trained model by its composite ``(model_name, model_version)`` key."""

    @abstractmethod
    async def list_trained_models(self, model_name: str | None = None) -> list[TrainedModel]:
        """List trained models, optionally filtered by ``model_name``."""

    @abstractmethod
    async def get_latest_version(self, model_name: str) -> int | None:
        """Return the highest ``model_version`` already recorded for ``model_name``.

        Returns ``None`` if no version has ever been recorded for that
        name. Called by ``Model_Versioner`` before assigning the next
        version (Requirement 8.1/8.2).
        """


class GitInfoProvider(ABC):
    """Port for looking up the current git commit SHA, for reproducibility metadata.

    Behind a port so tests can inject a fixed SHA and so the "not in a
    git repo" case (e.g. a stripped container image) is handled uniformly
    as "unavailable" rather than a hard failure (Requirement 12.3).
    """

    @abstractmethod
    async def get_commit_sha(self) -> str | None:
        """Return the current commit SHA, or ``None`` if unavailable."""
