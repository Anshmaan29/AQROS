"""Unit tests for ParityMonitor (pure domain comparison logic).

Tests every ``ComparisonStatus`` code path: match, outside tolerance, missing
offline/online, type mismatch, version mismatch, stale, and edge cases.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from aqros_parity_monitor.domain.models import ComparisonStatus
from aqros_parity_monitor.domain.monitor import ParityMonitor


@pytest.fixture
def monitor() -> ParityMonitor:
    return ParityMonitor(default_tolerance=1e-6, max_feature_age_seconds=300.0)


class TestCompareFeature:
    """Tests for ``ParityMonitor.compare_feature``."""

    async def test_perfect_match(self, monitor: ParityMonitor) -> None:
        result = monitor.compare_feature("sma_20", 42.5, 42.5)
        assert result.passed
        assert result.status is ComparisonStatus.MATCH
        assert result.difference == 0.0

    async def test_match_within_tolerance(self, monitor: ParityMonitor) -> None:
        result = monitor.compare_feature("sma_20", 100.0, 100.000001, tolerance=1e-5)
        assert result.passed

    async def test_large_difference(self, monitor: ParityMonitor) -> None:
        result = monitor.compare_feature("sma_20", 10.0, 20.0)
        assert not result.passed
        assert result.status is ComparisonStatus.OUTSIDE_TOLERANCE
        assert result.difference == 10.0

    async def test_both_none(self, monitor: ParityMonitor) -> None:
        result = monitor.compare_feature("sma_20", None, None)
        assert result.passed
        assert result.status is ComparisonStatus.MATCH

    async def test_missing_offline(self, monitor: ParityMonitor) -> None:
        result = monitor.compare_feature("sma_20", None, 42.5)
        assert not result.passed
        assert result.status is ComparisonStatus.MISSING_OFFLINE

    async def test_missing_online(self, monitor: ParityMonitor) -> None:
        result = monitor.compare_feature("sma_20", 42.5, None)
        assert not result.passed
        assert result.status is ComparisonStatus.MISSING_ONLINE

    async def test_version_mismatch(self, monitor: ParityMonitor) -> None:
        result = monitor.compare_feature("sma_20", 42.5, 42.5, offline_version=1, online_version=2)
        assert not result.passed
        assert result.status is ComparisonStatus.VERSION_MISMATCH

    async def test_version_match(self, monitor: ParityMonitor) -> None:
        result = monitor.compare_feature("sma_20", 42.5, 42.5, offline_version=2, online_version=2)
        assert result.passed
        assert result.status is ComparisonStatus.MATCH

    async def test_stale_feature(self, monitor: ParityMonitor) -> None:
        stale_time = datetime.now(UTC) - timedelta(seconds=600)
        result = monitor.compare_feature("sma_20", 42.5, 42.5, online_timestamp=stale_time)
        assert not result.passed
        assert result.status is ComparisonStatus.STALE

    async def test_fresh_feature_not_stale(self, monitor: ParityMonitor) -> None:
        fresh_time = datetime.now(UTC) - timedelta(seconds=60)
        result = monitor.compare_feature("sma_20", 42.5, 42.5, online_timestamp=fresh_time)
        assert result.passed

    async def test_type_mismatch_string_vs_float(self, monitor: ParityMonitor) -> None:
        result = monitor.compare_feature("category", "A", 1.0)
        assert not result.passed
        assert result.status is ComparisonStatus.TYPE_MISMATCH

    async def test_type_mismatch_int_vs_float(self, monitor: ParityMonitor) -> None:
        result = monitor.compare_feature("count", 5, 5.0)
        assert result.passed  # both numeric

    async def test_non_numeric_exact_match(self, monitor: ParityMonitor) -> None:
        result = monitor.compare_feature("sector", "tech", "tech")
        assert result.passed
        assert result.status is ComparisonStatus.MATCH

    async def test_non_numeric_mismatch(self, monitor: ParityMonitor) -> None:
        result = monitor.compare_feature("sector", "tech", "finance")
        assert not result.passed
        assert result.status is ComparisonStatus.OUTSIDE_TOLERANCE


class TestCompareSnapshot:
    """Tests for ``ParityMonitor.compare_snapshot``."""

    async def test_all_match(self, monitor: ParityMonitor) -> None:
        report = monitor.compare_snapshot(
            "AAPL",
            {"sma_20": 42.5, "rsi_14": 65.0},
            {"sma_20": 42.5, "rsi_14": 65.0},
        )
        assert report.passed
        assert report.matching_count == 2
        assert report.failed_count == 0

    async def test_some_failures(self, monitor: ParityMonitor) -> None:
        report = monitor.compare_snapshot(
            "AAPL",
            {"sma_20": 42.5, "rsi_14": 65.0},
            {"sma_20": 999.0, "rsi_14": 65.0},
        )
        assert not report.passed
        assert report.matching_count == 1
        assert report.failed_count == 1
        assert report.maximum_difference == 956.5

    async def test_missing_features(self, monitor: ParityMonitor) -> None:
        report = monitor.compare_snapshot(
            "AAPL",
            {"sma_20": 42.5},
            {"rsi_14": 65.0},
        )
        assert not report.passed
        assert report.missing_count == 2

    async def test_empty_snapshot(self, monitor: ParityMonitor) -> None:
        report = monitor.compare_snapshot("AAPL", {}, {})
        assert report.passed
        assert report.feature_count == 0

    async def test_failure_reasons_populated(self, monitor: ParityMonitor) -> None:
        report = monitor.compare_snapshot(
            "AAPL",
            {"sma_20": 42.5, "rsi_14": 65.0},
            {"sma_20": 999.0, "rsi_14": 65.0},
        )
        assert "sma_20" in report.failure_reasons

    async def test_symbol_uppercased(self, monitor: ParityMonitor) -> None:
        report = monitor.compare_snapshot("aapl", {"sma_20": 1.0}, {"sma_20": 1.0})
        assert report.symbol == "AAPL"


class TestEdgeCases:
    """Edge cases for the parity monitor."""

    async def test_tolerance_just_barely_passes(self, monitor: ParityMonitor) -> None:
        result = monitor.compare_feature("sma_20", 100.0, 100.0001, tolerance=1e-3)
        assert result.passed

    async def test_tolerance_just_barely_fails(self, monitor: ParityMonitor) -> None:
        result = monitor.compare_feature("sma_20", 100.0, 101.0, tolerance=1e-3)
        assert not result.passed
