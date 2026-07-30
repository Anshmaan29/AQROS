"""Ports (abstract interfaces) the Backtesting Engine domain depends on.

These are the seams between pure business logic and I/O. ``domain/`` and
``api/`` depend only on these abstractions; concrete implementations live in
``adapters/`` and are wired in via dependency injection (``api/deps.py``) —
the same pattern ``aqros_model_registry.domain.ports``,
``aqros_training_pipeline.domain.ports``, ``aqros_dataset_builder.domain.ports``,
``aqros_market_data.domain.ports``, and ``aqros_feature_store.domain.ports``
establish.

``MarketDataClient``, ``ModelRegistryClient``, and ``FeatureStoreClient`` are
the engine's *only* three outbound dependencies, each the sole channel to
one upstream service's published REST API (CLAUDE.md §7.9; Requirements
1.4, 1.5, 2.1, 9.1, 10.1). The engine never opens a database connection to
any other service and never contacts the Training Pipeline or Dataset
Builder through any port defined here or elsewhere. ``ResultArtifactStore``
and ``BacktestRunRepository`` are this service's own persistence ports.
``Clock`` and ``CalendarProvider`` supply the deterministic notions of "now"
and "exchange sessions" the pure domain depends on, per design.md Sections
5.1 and 6.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from aqros_backtesting_engine.domain.models import (
    BacktestConfiguration,
    BacktestResult,
    BacktestRun,
    Bar,
    CorporateAction,
    EquityPoint,
    ExchangeCalendarData,
    FeatureValue,
    Instrument,
    ResolvedModel,
    RunManifest,
    RunStatus,
    TradeLogEntry,
)


class MarketDataUnavailableError(RuntimeError):
    """Raised when the Market Data Service is unreachable or returns an error.

    Covers connection failures, timeouts, and any HTTP error status while
    fetching bars, an instrument, or corporate actions. Any data essential to
    a ``Backtest_Run`` that cannot be retrieved fails that run rather than
    proceeding with a gap silently (Requirements 2.4, 37.1).
    """


class MarketDataClient(ABC):
    """Port for the engine's sole channel to historical market data.

    Only the Market Data Service's published read endpoints are ever called
    through this port — never a direct database connection to the Market
    Data Service's own store, and never a write of any kind (Requirements
    1.4, 1.5, 2.1, 2.2; CLAUDE.md §7.9).
    """

    @abstractmethod
    async def get_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: str,
    ) -> list[Bar]:
        """Fetch OHLCV ``Bar`` rows for ``symbol`` over ``[start, end]`` at ``interval``.

        Returned bars are ordered by ``event_time`` ascending. Implementations
        paginate internally against the upstream endpoint so callers never
        see a partial page. Raises ``MarketDataUnavailableError`` on any
        error or unreachability (Requirements 2.1, 2.5, 3.1, 3.2).
        """

    @abstractmethod
    async def get_instrument(self, symbol: str) -> Instrument:
        """Fetch the ``Instrument`` record for ``symbol``.

        Raises ``MarketDataUnavailableError`` on any error or unreachability,
        including the case where the Market Data Service has no record of
        ``symbol``.
        """

    @abstractmethod
    async def get_corporate_actions(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        as_of: datetime,
    ) -> list[CorporateAction]:
        """Fetch ``CorporateAction`` rows for ``symbol`` over ``[start, end]``.

        Only actions whose ``knowledge_time`` is at or before ``as_of`` are
        returned, so callers never observe an action before it was knowable
        (Requirement 8.6). Where the Market Data Service exposes no
        corporate-actions feed for ``symbol``, implementations return an
        empty list rather than synthesizing data (Decision 6; Requirements
        8.7, 45.3) — this is distinct from ``MarketDataUnavailableError``,
        which signals an actual error or unreachability. Raises
        ``MarketDataUnavailableError`` on any error or unreachability.
        """


class ModelNotFoundError(RuntimeError):
    """Raised when the Model Registry has no matching model or version.

    Covers both "no ``PRODUCTION`` model exists for this model name" (from
    ``resolve_production``) and "this exact version does not exist" (from
    ``get_version``) — the caller fails the run immediately with "no
    approved model available" rather than falling back to any other source
    (Requirements 9.6, 9.7).
    """


class ModelRegistryUnavailableError(RuntimeError):
    """Raised when the Model Registry is unreachable or returns a non-404 error.

    Covers connection failures, timeouts, and any HTTP error status other
    than "not found". The run fails immediately with no fallback to any
    other data source (Requirement 37.1).
    """


class ModelRegistryClient(ABC):
    """Port for the engine's sole channel to model resolution.

    Only the Model Registry's published REST API is ever called through
    this port; the engine never obtains a model from the Training Pipeline,
    a filesystem path, or by retraining, and never queries the Training
    Pipeline for any purpose (Requirements 9.1, 9.4, 9.5, 45.1).
    """

    @abstractmethod
    async def resolve_production(self, model_name: str) -> ResolvedModel:
        """Resolve the current ``PRODUCTION`` champion for ``model_name``.

        Returns a ``ResolvedModel`` with ``resolved_as == "production"``,
        carrying its identity, checksum, and lineage. Raises
        ``ModelNotFoundError`` if no production model exists for
        ``model_name`` and ``ModelRegistryUnavailableError`` on any other
        error or unreachability (Requirements 9.2, 9.6, 9.7).
        """

    @abstractmethod
    async def get_version(self, model_name: str, version: int) -> ResolvedModel:
        """Resolve exactly ``(model_name, version)`` as an explicit pin.

        Returns a ``ResolvedModel`` with ``resolved_as == "pinned"``,
        carrying its identity, checksum, and lineage. Raises
        ``ModelNotFoundError`` if that version does not exist and
        ``ModelRegistryUnavailableError`` on any other error or
        unreachability (Requirements 9.3, 9.7).
        """

    @abstractmethod
    async def download_artifact(self, model_name: str, version: int) -> bytes:
        """Download the raw model artifact bytes for ``(model_name, version)``.

        Raises ``ModelNotFoundError`` if that version does not exist and
        ``ModelRegistryUnavailableError`` on any other error or
        unreachability.
        """

    @abstractmethod
    async def publish_result(
        self,
        run_uuid: UUID,
        report: dict[str, object],
        signature: str,
    ) -> dict[str, object]:
        """Publish a signed backtest result report to the Model Registry.

        Returns the registry's response metadata (e.g. artifact id, version).
        Raises ``ModelRegistryUnavailableError`` on any error or unreachability.
        """


class FeatureStoreUnavailableError(RuntimeError):
    """Raised when the Feature Store is unreachable or returns an error.

    Covers connection failures, timeouts, and any HTTP error status while
    resolving feature values. A run that requires features it cannot
    retrieve fails rather than proceeding with a silent gap (Requirement
    10.4, 37.1).
    """


class FeatureStoreClient(ABC):
    """Port for the engine's sole channel to engineered feature values.

    Only the Feature Store's published REST API is ever called through this
    port (Requirement 10.1). ``as_of`` is a mandatory point-in-time cutoff
    on every call — never omitted — so the engine can never request or
    receive a feature value it could not yet have known (Requirements 4.3,
    10.2).
    """

    @abstractmethod
    async def get_feature_values(
        self,
        symbol: str,
        feature_name: str,
        feature_version: int,
        start: datetime,
        end: datetime,
        as_of: datetime,
    ) -> list[FeatureValue]:
        """Fetch ``FeatureValue`` rows for ``symbol``/``feature_name`` over ``[start, end]``.

        Only values whose ``knowledge_time`` is at or before ``as_of`` are
        returned. Callers pass ``as_of = current Simulation_Clock time`` on
        every call (Requirement 4.3). Raises ``FeatureStoreUnavailableError``
        on any error or unreachability.
        """


class ArtifactAlreadyExistsError(RuntimeError):
    """Raised by ``ResultArtifactStore.write_artifact`` when ``(run_uuid, name)``
    has already been persisted (Requirement 39.2) — artifacts are never
    overwritten; a second write is refused and the original bytes are kept.
    """


class ArtifactIntegrityError(RuntimeError):
    """Raised by ``ResultArtifactStore.read_artifact`` when the retrieved bytes
    do not match the checksum recorded at write time (Requirement 39.3) —
    corrupted bytes are refused rather than served.
    """


class ResultArtifactStore(ABC):
    """Port for versioned, checksum-verified, swappable persistence of result bytes.

    Takes/returns plain ``bytes`` only, so ``LocalResultArtifactStore`` (the
    MVP filesystem-backed adapter) is a drop-in swap for a future
    object-store-backed one (S3/MinIO/R2) with no change to any caller
    (Requirements 38.5, 39.1). Mirrors the Model Registry's ``ArtifactStore``
    port.
    """

    @abstractmethod
    async def write_artifact(self, run_uuid: UUID, name: str, data: bytes) -> str:
        """Persist ``data`` under ``(run_uuid, name)`` and return its storage location.

        Computes and stores a checksum alongside the bytes. Raises
        ``ArtifactAlreadyExistsError`` if that pair has already been
        written, rather than overwriting it (Requirement 39.2).
        """

    @abstractmethod
    async def read_artifact(self, run_uuid: UUID, name: str) -> bytes:
        """Return the exact bytes previously written for ``(run_uuid, name)``.

        Recomputes the checksum and compares it to the recorded value,
        raising ``ArtifactIntegrityError`` on mismatch rather than serving
        corrupted bytes (Requirement 39.3).
        """


class BacktestResultAlreadyExistsError(RuntimeError):
    """Raised by ``BacktestRunRepository.write_result`` when a result already
    exists for ``run_uuid`` (Requirements 38.2, 40.4) — a ``Backtest_Result``
    is written at most once per run and never overwritten or updated.
    """


class BacktestRunRepository(ABC):
    """Port for persisting and retrieving ``Backtest_Run`` state and results.

    ``append_trade_log`` and ``append_equity_points`` are the only write
    paths for the trade log and equity curve — both are append-only with no
    update or delete path (Requirement 38.3). ``write_result`` is write-once
    per run (Requirements 38.2, 40.4). Implementations take an
    ``AsyncSession`` via their constructor and never ``commit()`` — the
    request-scoped session owns the transaction, exactly as the Model
    Registry's and Training Pipeline's repositories do.
    """

    @abstractmethod
    async def create_run(
        self,
        config: BacktestConfiguration,
        manifest_stub: RunManifest,
        run_uuid: UUID,
    ) -> None:
        """Insert a new ``PENDING`` run — the write-once identity for ``run_uuid``.

        ``manifest_stub`` carries whatever manifest fields are already known
        at submission time (the configuration, seed, and universe); fields
        that depend on model resolution and replay are filled in only when
        ``write_result`` persists the final manifest (Requirements 12.2,
        12.3, 30.1).
        """

    @abstractmethod
    async def set_status(
        self,
        run_uuid: UUID,
        status: RunStatus,
        failure_reason: str | None = None,
    ) -> None:
        """Advance ``run_uuid``'s lifecycle to ``status`` (``PENDING -> RUNNING -> COMPLETED | FAILED``).

        ``failure_reason`` is recorded when transitioning to ``FAILED``
        (Requirement 37.5).
        """

    @abstractmethod
    async def append_trade_log(self, run_uuid: UUID, entries: list[TradeLogEntry]) -> None:
        """Append ``entries`` to ``run_uuid``'s ``Trade_Log`` (append-only, Requirement 38.3)."""

    @abstractmethod
    async def append_equity_points(self, run_uuid: UUID, points: list[EquityPoint]) -> None:
        """Append ``points`` to ``run_uuid``'s ``Equity_Curve`` (append-only, Requirement 38.3)."""

    @abstractmethod
    async def write_result(
        self,
        run_uuid: UUID,
        result: BacktestResult,
        manifest: RunManifest,
    ) -> None:
        """Persist the final, immutable ``result`` and ``manifest`` for ``run_uuid``.

        Written at most once per run. Raises
        ``BacktestResultAlreadyExistsError`` if a result already exists for
        ``run_uuid`` rather than overwriting it (Requirements 38.2, 40.4).
        """

    @abstractmethod
    async def get_run(self, run_uuid: UUID) -> BacktestRun | None:
        """Fetch one run's status/identity record by ``run_uuid``, or ``None`` if unknown."""

    @abstractmethod
    async def get_result(self, run_uuid: UUID) -> BacktestResult | None:
        """Fetch one run's ``Backtest_Result`` by ``run_uuid``, or ``None`` if not yet written."""

    @abstractmethod
    async def get_manifest(self, run_uuid: UUID) -> RunManifest | None:
        """Fetch one run's ``Run_Manifest`` by ``run_uuid``, or ``None`` if not yet written."""

    @abstractmethod
    async def list_runs(
        self,
        *,
        strategy_id: str | None = None,
        model_name: str | None = None,
        status: RunStatus | None = None,
    ) -> list[BacktestRun]:
        """List run status/identity records, optionally filtered by strategy, model, and/or status."""


class Clock(ABC):
    """Port for the current time, injected so domain logic and tests are deterministic.

    ``SystemClock`` (adapter, real UTC now) is used only outside the pure
    simulation — for example to timestamp API-level events. The
    ``Simulation_Engine`` itself never reads this port; it reads only the
    injected ``Simulation_Clock`` implementation of this port, which
    advances solely as events are processed (Requirements 4.2, 6.3, 31.1).
    """

    @abstractmethod
    def now(self) -> datetime:
        """Return the current timezone-aware timestamp."""


class CalendarProvider(ABC):
    """Port for deterministic, versioned exchange trading-calendar data.

    Supplies the sessions/holidays/half-days/time-zone data the pure domain
    ``Trading_Calendar`` derives concrete session boundaries from, and the
    calendar source/version string pinned into the ``Run_Manifest``
    (Requirement 7.6). This port performs no simulation logic itself — it
    only supplies the raw calendar facts.
    """

    @abstractmethod
    async def get_calendar(self, exchange: str) -> ExchangeCalendarData:
        """Return the deterministic, versioned calendar data for ``exchange``."""
