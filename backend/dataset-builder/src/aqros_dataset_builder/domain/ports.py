"""Ports (abstract interfaces) the domain depends on.

These are the seams between pure business logic and I/O. ``domain/`` and
``api/`` depend only on these protocols; concrete implementations live in
``adapters/`` and are wired in via dependency injection (``api/deps.py``) —
the same pattern ``aqros_market_data.domain.ports`` and
``aqros_feature_store.domain.ports`` establish.

Two source ports respect the two upstream service boundaries
(CLAUDE.md §7.9 — communicate through published APIs only):
``MarketDataSource`` reads OHLCV bars (used only to compute labels from
future *prices*), and ``FeatureSource`` reads engineered feature values
(used to build the X matrix). Neither touches the other service's database.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from aqros_dataset_builder.domain.models import (
    BarInterval,
    DatasetBuildRun,
    DatasetDefinition,
    FeatureValue,
    OHLCVBar,
)


class MarketDataSource(ABC):
    """Port for reading validated OHLCV bars owned by the Market Data Service."""

    @abstractmethod
    async def get_bars(
        self,
        symbol: str,
        *,
        start: date | None = None,
        end: date | None = None,
        interval: BarInterval = BarInterval.DAILY,
    ) -> list[OHLCVBar]:
        """Fetch stored OHLCV bars for ``symbol``, ordered by ``event_time`` ascending."""


class FeatureSource(ABC):
    """Port for reading engineered feature values owned by the Feature Store Service."""

    @abstractmethod
    async def get_feature_values(
        self,
        symbol: str,
        feature_name: str,
        *,
        start: date | None = None,
        end: date | None = None,
    ) -> list[FeatureValue]:
        """Fetch stored feature values for ``symbol``/``feature_name``, ascending by event_time."""


class UpstreamSourceError(RuntimeError):
    """Raised when an upstream service is unreachable or returns an error."""


class GitInfoProvider(ABC):
    """Port for looking up the current git commit SHA, for the dataset manifest.

    A dataset manifest records the code SHA that produced it (CLAUDE.md §5)
    so that a dataset can be tied to the exact version of the generation
    logic that built it. Behind a port so tests can inject a fixed SHA and
    so the "not in a git repo" case (e.g. a stripped container image) is
    handled uniformly as "unavailable" rather than a hard failure.
    """

    @abstractmethod
    async def get_commit_sha(self) -> str | None:
        """Return the current commit SHA, or ``None`` if unavailable."""


class DatasetDefinitionRepository(ABC):
    """Port for persisting and retrieving dataset definitions (the catalog)."""

    @abstractmethod
    async def create_definition(self, definition: DatasetDefinition) -> DatasetDefinition:
        """Persist a new, immutable dataset definition version."""

    @abstractmethod
    async def get_definition(self, name: str, version: int) -> DatasetDefinition | None:
        """Fetch one dataset definition by (name, version)."""

    @abstractmethod
    async def get_latest_definition(self, name: str) -> DatasetDefinition | None:
        """Fetch the highest registered version of a named dataset."""

    @abstractmethod
    async def list_definitions(self) -> list[DatasetDefinition]:
        """List every registered dataset definition."""


class DatasetBuildRunRepository(ABC):
    """Port for persisting the audit trail of dataset build executions."""

    @abstractmethod
    async def create_run(self, run: DatasetBuildRun) -> DatasetBuildRun:
        """Persist a new run record (typically in ``RUNNING`` status) and return it with its id."""

    @abstractmethod
    async def complete_run(self, run: DatasetBuildRun) -> None:
        """Update an existing run record with its final status and metadata."""

    @abstractmethod
    async def get_run(self, run_id: int) -> DatasetBuildRun | None:
        """Fetch one build run by id."""

    @abstractmethod
    async def list_runs(
        self, *, dataset_name: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[DatasetBuildRun]:
        """List build runs, most recent first, optionally filtered by dataset name."""


class DatasetStorage(ABC):
    """Port for persisting the generated dataset's row data and its manifest."""

    @abstractmethod
    async def write_dataset(
        self,
        dataset_name: str,
        dataset_version: int,
        run_id: int,
        rows: list[dict[str, object]],
    ) -> str:
        """Write the generated rows to durable storage and return their location (e.g. a path/URI)."""

    @abstractmethod
    async def read_dataset(self, path: str) -> list[dict[str, object]]:
        """Read back a previously written dataset's rows, given its stored location."""

    @abstractmethod
    async def compute_checksum(self, path: str) -> str:
        """Compute a content hash of the artifact at ``path`` (for the manifest's checksum field)."""

    @abstractmethod
    async def write_manifest(
        self,
        dataset_name: str,
        dataset_version: int,
        run_id: int,
        manifest: dict[str, object],
    ) -> str:
        """Write the JSON-serializable manifest to durable storage and return its location."""

    @abstractmethod
    async def read_manifest(self, path: str) -> dict[str, object]:
        """Read back a previously written manifest, given its stored location."""
