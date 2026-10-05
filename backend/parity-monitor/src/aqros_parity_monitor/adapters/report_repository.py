"""InMemoryParityReportRepository and LogMetricPublisher — adapters for the parity monitor.

``InMemoryParityReportRepository`` stores reports in a dict (suitable for
development/test). ``LogMetricPublisher`` logs metrics via structlog.
"""

from __future__ import annotations

from datetime import datetime

import structlog

from aqros_parity_monitor.domain.models import (
    FeatureComparisonResult,
    ParityReport,
    ParityStatistics,
)
from aqros_parity_monitor.ports.ports import (
    MetricPublisher,
    ParityReportRepository,
)

_logger = structlog.get_logger(__name__)


class InMemoryParityReportRepository(ParityReportRepository):
    """In-memory parity report repository (dev/test).

    Not suitable for production — use a SQL/Redis-backed adapter.
    """

    def __init__(self) -> None:
        self._reports: dict[str, list[ParityReport]] = {}

    async def save(self, report: ParityReport) -> None:
        symbol = report.symbol.upper()
        if symbol not in self._reports:
            self._reports[symbol] = []
        self._reports[symbol].append(report)

    async def get_by_symbol(
        self, symbol: str, *, limit: int = 100, offset: int = 0
    ) -> list[ParityReport]:
        reports = self._reports.get(symbol.upper(), [])
        return sorted(reports, key=lambda r: r.timestamp, reverse=True)[offset : offset + limit]

    async def get_latest(self, symbol: str) -> ParityReport | None:
        reports = self._reports.get(symbol.upper(), [])
        if not reports:
            return None
        return max(reports, key=lambda r: r.timestamp)

    async def get_statistics(self, *, since: datetime | None = None) -> ParityStatistics:
        all_reports: list[ParityReport] = []
        for reports in self._reports.values():
            all_reports.extend(reports)

        if since is not None:
            all_reports = [r for r in all_reports if r.timestamp >= since]

        if not all_reports:
            return ParityStatistics()

        total = len(all_reports)
        total_matches = sum(r.matching_count for r in all_reports)
        total_failures = sum(r.failed_count for r in all_reports)
        total_skew = sum(r.failed_count for r in all_reports)
        total_stale = sum(r.stale_count for r in all_reports)
        total_missing = sum(r.missing_count for r in all_reports)
        total_version_mismatches = sum(r.version_mismatch_count for r in all_reports)
        avg_latency = (
            sum(r.latency_ms or 0.0 for r in all_reports if r.latency_ms is not None) / total
        )
        total_feature_checks = sum(r.feature_count for r in all_reports)
        passing_rate = total_matches / total_feature_checks if total_feature_checks > 0 else 0.0

        return ParityStatistics(
            total_checks=total,
            total_matches=total_matches,
            total_failures=total_failures,
            total_skew=total_skew,
            total_stale=total_stale,
            total_missing=total_missing,
            total_version_mismatches=total_version_mismatches,
            average_latency_ms=avg_latency,
            overall_passing_rate=passing_rate,
        )

    async def prune(self, before: datetime) -> int:
        removed = 0
        for symbol in list(self._reports):
            self._reports[symbol] = [r for r in self._reports[symbol] if r.timestamp >= before]
            removed += len(self._reports[symbol])
            if not self._reports[symbol]:
                del self._reports[symbol]
        return removed


class LogMetricPublisher(MetricPublisher):
    """Publishes parity metrics to the log (structured)."""

    async def publish_report_metrics(self, report: ParityReport) -> None:
        _logger.info(
            "parity.report.metrics",
            symbol=report.symbol,
            feature_count=report.feature_count,
            matching_count=report.matching_count,
            failed_count=report.failed_count,
            missing_count=report.missing_count,
            stale_count=report.stale_count,
            passed=report.passed,
            latency_ms=report.latency_ms,
        )

    async def publish_comparison_metrics(self, result: FeatureComparisonResult) -> None:
        _logger.info(
            "parity.comparison.metrics",
            feature_name=result.feature_name,
            status=result.status.value,
            difference=result.difference,
            tolerance=result.tolerance,
        )
