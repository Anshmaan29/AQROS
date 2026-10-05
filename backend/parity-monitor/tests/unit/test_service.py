"""Unit tests for ParityService orchestration.

Uses fakes for every port — no network, no Redis — proving the orchestration
logic (fetch → compare → save → emit) is correct.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import pytest
from aqros_parity_monitor.adapters.report_repository import (
    InMemoryParityReportRepository,
)
from aqros_parity_monitor.application.service import ParityService
from aqros_parity_monitor.domain.monitor import ParityMonitor
from aqros_parity_monitor.ports.ports import (
    Clock,
    EventPublisher,
    MetricPublisher,
    OfflineFeatureProvider,
    OnlineFeatureProvider,
)


class FakeOfflineProvider(OfflineFeatureProvider):
    def __init__(self) -> None:
        self.data: dict[str, dict[str, Any]] = {}

    async def get_snapshot(
        self, symbol: str, *, as_of: datetime | None = None
    ) -> Mapping[str, Any]:
        return self.data.get(symbol.upper(), {})

    async def get_feature_value(
        self, symbol: str, feature_name: str, *, as_of: datetime | None = None
    ) -> Any | None:
        return self.data.get(symbol.upper(), {}).get(feature_name)

    async def get_feature_version(self, symbol: str, feature_name: str) -> int | None:
        return 1


class FakeOnlineProvider(OnlineFeatureProvider):
    def __init__(self) -> None:
        self.data: dict[str, dict[str, Any]] = {}

    async def get_snapshot(self, symbol: str) -> Mapping[str, Any]:
        return self.data.get(symbol.upper(), {})

    async def get_feature_value(self, symbol: str, feature_name: str) -> Any | None:
        return self.data.get(symbol.upper(), {}).get(feature_name)

    async def get_feature_timestamp(self, symbol: str, feature_name: str) -> datetime | None:
        return datetime.now(UTC)

    async def health_check(self) -> bool:
        return True


class FakeEventPublisher(EventPublisher):
    def __init__(self) -> None:
        self.events: list[tuple[str, bytes]] = []

    async def publish_event(self, topic: str, payload: bytes) -> None:
        self.events.append((topic, payload))


class FakeMetricPublisher(MetricPublisher):
    def __init__(self) -> None:
        self.report_metrics: list[object] = []
        self.comparison_metrics: list[object] = []

    async def publish_report_metrics(self, report: object) -> None:
        self.report_metrics.append(report)

    async def publish_comparison_metrics(self, result: object) -> None:
        self.comparison_metrics.append(result)


class FixedClock(Clock):
    def __init__(self, dt: datetime | None = None) -> None:
        self._dt = dt or datetime(2024, 6, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self._dt


@pytest.fixture
def offline() -> FakeOfflineProvider:
    return FakeOfflineProvider()


@pytest.fixture
def online() -> FakeOnlineProvider:
    return FakeOnlineProvider()


@pytest.fixture
def repo() -> InMemoryParityReportRepository:
    return InMemoryParityReportRepository()


@pytest.fixture
def monitor() -> ParityMonitor:
    return ParityMonitor()


@pytest.fixture
def event_pub() -> FakeEventPublisher:
    return FakeEventPublisher()


@pytest.fixture
def metric_pub() -> FakeMetricPublisher:
    return FakeMetricPublisher()


@pytest.fixture
def fixed_clock() -> FixedClock:
    return FixedClock()


@pytest.fixture
def service(
    offline: FakeOfflineProvider,
    online: FakeOnlineProvider,
    repo: InMemoryParityReportRepository,
    monitor: ParityMonitor,
    event_pub: FakeEventPublisher,
    metric_pub: FakeMetricPublisher,
) -> ParityService:
    return ParityService(
        offline_provider=offline,
        online_provider=online,
        report_repository=repo,
        monitor=monitor,
        event_publisher=event_pub,
        metric_publisher=metric_pub,
        metrics_enabled=True,
        events_enabled=True,
    )


class TestRunCheck:
    """Tests for ``ParityService.run_check``."""

    async def test_perfect_parity(
        self, service: ParityService, offline: FakeOfflineProvider, online: FakeOnlineProvider
    ) -> None:
        offline.data["AAPL"] = {"sma_20": 42.5, "rsi_14": 65.0}
        online.data["AAPL"] = {"sma_20": 42.5, "rsi_14": 65.0}

        report = await service.run_check("AAPL")

        assert report.passed
        assert report.matching_count == 2
        assert report.failed_count == 0

    async def test_violation_detected(
        self, service: ParityService, offline: FakeOfflineProvider, online: FakeOnlineProvider
    ) -> None:
        offline.data["AAPL"] = {"sma_20": 42.5}
        online.data["AAPL"] = {"sma_20": 999.0}

        report = await service.run_check("AAPL")

        assert not report.passed
        assert report.failed_count == 1

    async def test_events_emitted(
        self,
        service: ParityService,
        offline: FakeOfflineProvider,
        online: FakeOnlineProvider,
        event_pub: FakeEventPublisher,
    ) -> None:
        offline.data["AAPL"] = {"sma_20": 42.5}
        online.data["AAPL"] = {"sma_20": 42.5}

        await service.run_check("AAPL")

        topics = [e[0] for e in event_pub.events]
        assert "parity.check.started" in topics
        assert "parity.check.completed" in topics

    async def test_metrics_published(
        self,
        service: ParityService,
        offline: FakeOfflineProvider,
        online: FakeOnlineProvider,
        metric_pub: FakeMetricPublisher,
    ) -> None:
        offline.data["AAPL"] = {"sma_20": 42.5}
        online.data["AAPL"] = {"sma_20": 42.5}

        await service.run_check("AAPL")

        assert len(metric_pub.report_metrics) == 1

    async def test_correlation_id_propagated(
        self,
        service: ParityService,
        offline: FakeOfflineProvider,
        online: FakeOnlineProvider,
    ) -> None:
        offline.data["AAPL"] = {"sma_20": 42.5}
        online.data["AAPL"] = {"sma_20": 42.5}

        report = await service.run_check("AAPL", correlation_id="test-corr")
        assert report.correlation_id == "test-corr"


class TestBatch:
    """Tests for batch operations."""

    async def test_batch_parity(
        self, service: ParityService, offline: FakeOfflineProvider, online: FakeOnlineProvider
    ) -> None:
        offline.data["AAPL"] = {"sma_20": 1.0}
        online.data["AAPL"] = {"sma_20": 1.0}
        offline.data["MSFT"] = {"rsi_14": 2.0}
        online.data["MSFT"] = {"rsi_14": 2.0}

        reports = await service.run_batch(["AAPL", "MSFT"])
        assert len(reports) == 2
        assert all(r.passed for r in reports)


class TestHistoryAndStats:
    """Tests for report history and statistics."""

    async def test_get_latest_report_returns_most_recent(
        self, service: ParityService, offline: FakeOfflineProvider, online: FakeOnlineProvider
    ) -> None:
        offline.data["AAPL"] = {"sma_20": 1.0}
        online.data["AAPL"] = {"sma_20": 1.0}

        await service.run_check("AAPL")
        latest = await service.get_latest_report("AAPL")
        assert latest is not None
        assert latest.symbol == "AAPL"

    async def test_get_latest_report_none_for_unknown(self, service: ParityService) -> None:
        latest = await service.get_latest_report("UNKNOWN")
        assert latest is None

    async def test_statistics(
        self, service: ParityService, offline: FakeOfflineProvider, online: FakeOnlineProvider
    ) -> None:
        offline.data["AAPL"] = {"sma_20": 1.0}
        online.data["AAPL"] = {"sma_20": 1.0}

        await service.run_check("AAPL")
        stats = await service.get_statistics()
        assert stats.total_checks >= 1
        assert stats.total_matches >= 1
