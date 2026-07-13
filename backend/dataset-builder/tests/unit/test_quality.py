"""Unit tests for dataset quality metrics (pure domain logic, no I/O)."""

from __future__ import annotations

import pandas as pd
import pytest

from aqros_dataset_builder.domain.models import LabelType
from aqros_dataset_builder.domain.quality import (
    build_quality_report,
    compute_class_balance,
    compute_duplicate_row_count,
    compute_feature_statistics,
    compute_missing_value_counts,
    validate_quality,
)


def _sample_rows() -> list[dict[str, object]]:
    return [
        {"symbol": "AAPL", "event_time": "d1", "fold": 0, "split_role": "train", "label": 1.0},
        {"symbol": "AAPL", "event_time": "d2", "fold": 0, "split_role": "train", "label": 0.0},
        {"symbol": "AAPL", "event_time": "d3", "fold": 0, "split_role": "validation", "label": 1.0},
        {"symbol": "AAPL", "event_time": "d4", "fold": 0, "split_role": "test", "label": 1.0},
        {
            "symbol": "AAPL",
            "event_time": "d4",
            "fold": 0,
            "split_role": "test",
            "label": 1.0,
        },  # dup
    ]


def test_compute_missing_value_counts() -> None:
    frame = pd.DataFrame({"a": [1.0, None, 3.0], "b": [None, None, 6.0]})
    counts = compute_missing_value_counts(frame)
    assert counts == {"a": 1, "b": 2}


def test_compute_duplicate_row_count_detects_exact_key_repeats() -> None:
    rows = _sample_rows()
    assert compute_duplicate_row_count(rows) == 1


def test_compute_duplicate_row_count_with_no_duplicates() -> None:
    rows = _sample_rows()[:-1]
    assert compute_duplicate_row_count(rows) == 0


def test_compute_class_balance_for_binary_direction() -> None:
    rows = _sample_rows()
    balance = compute_class_balance(rows, LabelType.BINARY_DIRECTION)
    # train: [1.0, 0.0] -> 0.5 positive; validation: [1.0] -> 1.0; test: [1.0, 1.0] -> 1.0
    assert balance["train_positive_fraction"] == pytest.approx(0.5)
    assert balance["validation_positive_fraction"] == pytest.approx(1.0)
    assert balance["test_positive_fraction"] == pytest.approx(1.0)


def test_compute_class_balance_is_empty_for_continuous_labels() -> None:
    rows = _sample_rows()
    balance = compute_class_balance(rows, LabelType.FUTURE_RETURN)
    assert balance == {}


def test_compute_feature_statistics_reports_missing_and_moments() -> None:
    frame = pd.DataFrame({"sma_20": [10.0, 12.0, None, 14.0]})
    stats = compute_feature_statistics(frame, ("sma_20",))
    assert len(stats) == 1
    stat = stats[0]
    assert stat.feature_name == "sma_20"
    assert stat.count == 3
    assert stat.missing_count == 1
    assert stat.mean == pytest.approx(12.0)
    assert stat.minimum == pytest.approx(10.0)
    assert stat.maximum == pytest.approx(14.0)


def test_compute_feature_statistics_handles_absent_column() -> None:
    frame = pd.DataFrame({"other": [1.0, 2.0]})
    stats = compute_feature_statistics(frame, ("missing_feature",))
    assert stats[0].feature_name == "missing_feature"
    assert stats[0].count == 0
    assert stats[0].missing_count == 2
    assert stats[0].mean is None


def test_validate_quality_flags_high_missing_fraction() -> None:
    # Two distinct non-null values so the zero-variance check doesn't also
    # fire — isolating this test to the missing-fraction finding only.
    frame = pd.DataFrame({"sma_20": [10.0, 12.0, None, None, None, None]})
    stats = compute_feature_statistics(frame, ("sma_20",))
    missing_counts = compute_missing_value_counts(frame)
    findings = validate_quality(missing_counts, len(frame), stats)
    assert len(findings) == 1
    assert "sma_20" in findings[0]
    assert "missing" in findings[0]


def test_validate_quality_flags_zero_variance_feature() -> None:
    frame = pd.DataFrame({"constant_feature": [5.0, 5.0, 5.0]})
    stats = compute_feature_statistics(frame, ("constant_feature",))
    missing_counts = compute_missing_value_counts(frame)
    findings = validate_quality(missing_counts, len(frame), stats)
    assert any("zero variance" in f for f in findings)


def test_validate_quality_passes_clean_feature() -> None:
    frame = pd.DataFrame({"sma_20": [10.0, 12.0, 14.0, 16.0]})
    stats = compute_feature_statistics(frame, ("sma_20",))
    missing_counts = compute_missing_value_counts(frame)
    findings = validate_quality(missing_counts, len(frame), stats)
    assert findings == []


def test_build_quality_report_end_to_end() -> None:
    raw_frame = pd.DataFrame({"sma_20": [10.0, 12.0, None, 14.0]})
    rows = _sample_rows()
    report = build_quality_report(raw_frame, rows, ("sma_20",), LabelType.BINARY_DIRECTION)

    assert report.total_rows == len(rows)
    assert report.duplicate_row_count == 1
    assert report.missing_value_counts == {"sma_20": 1}
    assert "train_positive_fraction" in report.class_balance
    assert len(report.feature_statistics) == 1
    # No zero-variance/high-missing finding expected for this frame.
    assert report.validation_passed is True
    assert report.validation_findings == []
