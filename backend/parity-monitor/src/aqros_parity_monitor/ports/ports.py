"""Ports (interfaces) for the Parity Monitor.

Every external dependency behind an ABC: offline feature provider, online
feature provider, report repository, clock, metric publisher, event publisher.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol


class Clock(ABC):
    """Abstract clock — injectable for deterministic testing."""

    @abstractmethod
    def now(self) -> datetime:
        """Return the current UTC datetime."""


class OfflineFeatureProvider(ABC):
    """Port for reading feature values from the offline (Postgres) store.

    The adapter talks to the Feature Store Service's REST API.
    """

    @abstractmethod
    async def get_snapshot(
        self, symbol: str, *, as_of: datetime | None = None
    ) -> Mapping[str, Any]:
        """Return all feature values for ``symbol`` from the offline store.

        Returns ``{feature_name: value}``. Returns an empty mapping if the
        symbol has no features.
        """

    @abstractmethod
    async def get_feature_value(
        self, symbol: str, feature_name: str, *, as_of: datetime | None = None
    ) -> Any | None:
        """Return a single feature value from the offline store.

        Returns ``None`` if no value exists.
        """

    @abstractmethod
    async def get_feature_version(self, symbol: str, feature_name: str) -> int | None:
        """Return the latest feature version for a given symbol and feature."""


class OnlineFeatureProvider(ABC):
    """Port for reading feature values from the online (Redis) store."""

    @abstractmethod
    async def get_snapshot(self, symbol: str) -> Mapping[str, Any]:
        """Return all feature values for ``symbol`` from the online store.

        Returns ``{feature_name: value}``. Returns an empty mapping if the
        symbol has no features.
        """

    @abstractmethod
    async def get_feature_value(self, symbol: str, feature_name: str) -> Any | None:
        """Return a single feature value from the online store.

        Returns ``None`` if no value exists.
        """

    @abstractmethod
    async def get_feature_timestamp(self, symbol: str, feature_name: str) -> datetime | None:
        """Return when the online value was stored.

        Returns ``None`` if no value exists or timestamp tracking is
        unavailable.
        """

    @abstractmethod
    async def health_check(self) -> bool:
        """Verify the backing store is reachable."""


class ParityReportRepository(ABC):
    """Port for persisting and querying parity reports."""

    @abstractmethod
    async def save(self, report: ParityReport) -> None: ...

    @abstractmethod
    async def get_by_symbol(
        self, symbol: str, *, limit: int = 100, offset: int = 0
    ) -> list[ParityReport]: ...

    @abstractmethod
    async def get_latest(self, symbol: str) -> ParityReport | None: ...

    @abstractmethod
    async def get_statistics(self, *, since: datetime | None = None) -> ParityStatistics: ...

    @abstractmethod
    async def prune(self, before: datetime) -> int:
        """Remove reports older than ``before``. Returns count removed."""


class MetricPublisher(ABC):
    """Port for publishing parity metrics to the observability stack."""

    @abstractmethod
    async def publish_report_metrics(self, report: ParityReport) -> None: ...

    @abstractmethod
    async def publish_comparison_metrics(self, result: FeatureComparisonResult) -> None: ...


class EventPublisher(ABC):
    """Port for publishing parity events on the event bus."""

    @abstractmethod
    async def publish_event(self, topic: str, payload: bytes) -> None: ...


# --- Runtime Protocol for the actual aqros-events EventBus ---------------


class EventBusProtocol(Protocol):
    """Protocol matching ``aqros_events.EventBus`` (avoid hard import)."""

    async def publish(self, envelope: object) -> None: ...


# Late import to resolve circular references
from aqros_parity_monitor.domain.models import (  # noqa: E402
    FeatureComparisonResult,
    ParityReport,
    ParityStatistics,
)
