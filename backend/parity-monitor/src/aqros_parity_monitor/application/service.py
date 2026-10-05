"""ParityService — orchestration of parity checking.

Coordinates the domain ``ParityMonitor`` (pure comparison logic) with the
six ports (offline/online providers, report repository, clock, metrics,
events). This is the application service; it delegates I/O to adapters,
business logic to the domain.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

from pydantic import BaseModel

from aqros_parity_monitor.application.events import (
    ParityCheckCompleted,
    ParityCheckStarted,
    ParityReportGenerated,
    ParityViolationDetected,
)
from aqros_parity_monitor.domain.models import ParityReport, ParityStatistics
from aqros_parity_monitor.domain.monitor import ParityMonitor
from aqros_parity_monitor.ports.ports import (
    Clock,
    EventPublisher,
    MetricPublisher,
    OfflineFeatureProvider,
    OnlineFeatureProvider,
    ParityReportRepository,
)

TOPIC_PARITY_CHECK_STARTED = "parity.check.started"
TOPIC_PARITY_CHECK_COMPLETED = "parity.check.completed"
TOPIC_PARITY_VIOLATION_DETECTED = "parity.violation.detected"
TOPIC_PARITY_RECOVERED = "parity.recovered"
TOPIC_PARITY_REPORT_GENERATED = "parity.report.generated"


class ParityService:
    """Orchestrates parity checks between offline and online feature stores.

    Typical flow:

    1. Fetch offline features from the offline feature provider.
    2. Fetch online features from the online feature provider.
    3. Compare them using ``ParityMonitor``.
    4. Persist the resulting ``ParityReport``.
    5. Publish metrics (if enabled).
    6. Emit events (if enabled).
    """

    def __init__(
        self,
        offline_provider: OfflineFeatureProvider,
        online_provider: OnlineFeatureProvider,
        report_repository: ParityReportRepository,
        monitor: ParityMonitor,
        clock: Clock | None = None,
        metric_publisher: MetricPublisher | None = None,
        event_publisher: EventPublisher | None = None,
        metrics_enabled: bool = True,
        events_enabled: bool = True,
    ) -> None:
        self._offline = offline_provider
        self._online = online_provider
        self._repository = report_repository
        self._monitor = monitor
        self._clock = clock or _RealClock()
        self._metrics = metric_publisher
        self._events = event_publisher
        self._metrics_enabled = metrics_enabled
        self._events_enabled = events_enabled

    async def run_check(
        self,
        symbol: str,
        *,
        tolerance: float | None = None,
        correlation_id: str | None = None,
    ) -> ParityReport:
        """Run a full parity check for one symbol.

        1. Fetch offline and online snapshots.
        2. Compare every feature.
        3. Save, emit, and return the report.
        """
        start_s = time.monotonic()

        if self._events_enabled and self._events is not None:
            await self._emit(
                TOPIC_PARITY_CHECK_STARTED,
                ParityCheckStarted(
                    symbol=symbol,
                    correlation_id=correlation_id,
                ),
            )

        offline_features = await self._offline.get_snapshot(symbol)
        online_features = await self._online.get_snapshot(symbol)

        report = self._monitor.compare_snapshot(
            symbol=symbol,
            offline_features=offline_features,
            online_features=online_features,
            tolerance=tolerance,
            correlation_id=correlation_id,
        )
        report.latency_ms = (time.monotonic() - start_s) * 1000

        await self._repository.save(report)

        if self._events_enabled and self._events is not None:
            await self._emit(
                TOPIC_PARITY_CHECK_COMPLETED,
                ParityCheckCompleted(
                    symbol=symbol,
                    correlation_id=correlation_id,
                    feature_count=report.feature_count,
                    matching_count=report.matching_count,
                    failed_count=report.failed_count,
                    missing_count=report.missing_count,
                    stale_count=report.stale_count,
                    version_mismatch_count=report.version_mismatch_count,
                    passed=report.passed,
                    latency_ms=report.latency_ms,
                ),
            )

            for comp in report.comparisons:
                if not comp.passed:
                    await self._emit(
                        TOPIC_PARITY_VIOLATION_DETECTED,
                        ParityViolationDetected(
                            symbol=symbol,
                            feature_name=comp.feature_name,
                            correlation_id=correlation_id,
                            offline_value=comp.offline_value,
                            online_value=comp.online_value,
                            difference=comp.difference,
                            tolerance=comp.tolerance,
                            reason=comp.reason,
                        ),
                    )

            if report.passed:
                await self._emit(
                    TOPIC_PARITY_REPORT_GENERATED,
                    ParityReportGenerated(
                        symbol=symbol,
                        correlation_id=correlation_id,
                        feature_count=report.feature_count,
                        passed=True,
                        latency_ms=report.latency_ms,
                        report_id=report.correlation_id,
                    ),
                )

        if self._metrics_enabled and self._metrics is not None:
            await self._metrics.publish_report_metrics(report)

        return report

    async def run_batch(
        self,
        symbols: list[str],
        *,
        tolerance: float | None = None,
        max_workers: int = 4,
    ) -> list[ParityReport]:
        """Run parity checks for multiple symbols sequentially.

        In a production deployment this should be parallelised with
        ``asyncio.gather`` with a semaphore.
        """
        reports: list[ParityReport] = []
        for symbol in symbols:
            report = await self.run_check(symbol, tolerance=tolerance)
            reports.append(report)
        return reports

    async def run_portfolio_check(
        self,
        symbols: list[str],
        *,
        tolerance: float | None = None,
    ) -> list[ParityReport]:
        """Run parity checks for the entire portfolio of symbols."""
        return await self.run_batch(symbols, tolerance=tolerance)

    async def get_latest_report(self, symbol: str) -> ParityReport | None:
        """Return the most recent parity report for a symbol."""
        return await self._repository.get_latest(symbol.upper())

    async def get_history(
        self, symbol: str, *, limit: int = 100, offset: int = 0
    ) -> list[ParityReport]:
        """Return historical parity reports for a symbol."""
        return await self._repository.get_by_symbol(symbol.upper(), limit=limit, offset=offset)

    async def get_statistics(self, *, since: datetime | None = None) -> ParityStatistics:
        """Return aggregate parity statistics."""
        return await self._repository.get_statistics(since=since)

    async def _emit(self, topic: str, payload: BaseModel) -> None:
        if self._events is None:
            return
        raw = json.dumps(payload.model_dump(), default=str).encode("utf-8")
        await self._events.publish_event(topic, raw)


class _RealClock(Clock):
    def now(self) -> datetime:
        return datetime.now(UTC)
