"""Unit tests for feature-value validation rules (pure domain logic, no I/O)."""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest

from aqros_feature_store.domain.models import FeatureValue
from aqros_feature_store.domain.validation import validate_feature_value, validate_feature_values

FIXED_NOW = datetime(2024, 1, 15, tzinfo=UTC)


def _make_value(**overrides: object) -> FeatureValue:
    defaults: dict[str, object] = {
        "symbol": "AAPL",
        "feature_name": "sma_20",
        "feature_version": 1,
        "event_time": datetime(2024, 1, 10, tzinfo=UTC),
        "value": 150.25,
        "knowledge_time": datetime(2024, 1, 10, tzinfo=UTC),
    }
    defaults.update(overrides)
    return FeatureValue(**defaults)  # type: ignore[arg-type]


def test_valid_feature_value_passes() -> None:
    result = validate_feature_value(_make_value(), now=FIXED_NOW)
    assert result.is_valid
    assert result.violations == []


def test_nan_value_is_rejected() -> None:
    result = validate_feature_value(_make_value(value=math.nan), now=FIXED_NOW)
    assert not result.is_valid
    assert any("NaN" in v for v in result.violations)


def test_infinite_value_is_rejected() -> None:
    result = validate_feature_value(_make_value(value=math.inf), now=FIXED_NOW)
    assert not result.is_valid
    assert any("infinite" in v for v in result.violations)


def test_empty_symbol_is_rejected() -> None:
    result = validate_feature_value(_make_value(symbol="  "), now=FIXED_NOW)
    assert not result.is_valid
    assert any("symbol" in v for v in result.violations)


def test_empty_feature_name_is_rejected() -> None:
    result = validate_feature_value(_make_value(feature_name=""), now=FIXED_NOW)
    assert not result.is_valid
    assert any("feature_name" in v for v in result.violations)


def test_non_positive_version_is_rejected() -> None:
    result = validate_feature_value(_make_value(feature_version=0), now=FIXED_NOW)
    assert not result.is_valid
    assert any("feature_version" in v for v in result.violations)


def test_knowledge_time_before_event_time_is_rejected() -> None:
    result = validate_feature_value(
        _make_value(
            event_time=datetime(2024, 1, 10, tzinfo=UTC),
            knowledge_time=datetime(2024, 1, 9, tzinfo=UTC),
        ),
        now=FIXED_NOW,
    )
    assert not result.is_valid
    assert any("knowledge_time" in v for v in result.violations)


def test_future_event_time_is_rejected() -> None:
    result = validate_feature_value(
        _make_value(
            event_time=datetime(2024, 2, 1, tzinfo=UTC),
            knowledge_time=datetime(2024, 2, 1, tzinfo=UTC),
        ),
        now=FIXED_NOW,
    )
    assert not result.is_valid
    assert any("future" in v for v in result.violations)


def test_future_knowledge_time_is_rejected() -> None:
    # event_time is valid (in the past) but knowledge_time claims the future
    # — the lookahead trap this validator exists to close structurally.
    result = validate_feature_value(
        _make_value(
            event_time=datetime(2024, 1, 10, tzinfo=UTC),
            knowledge_time=datetime(2024, 2, 1, tzinfo=UTC),
        ),
        now=FIXED_NOW,
    )
    assert not result.is_valid
    assert any("future" in v for v in result.violations)


def test_validate_feature_values_batch_returns_one_result_per_value() -> None:
    values = [_make_value(), _make_value(value=math.nan)]
    results = validate_feature_values(values, now=FIXED_NOW)
    assert len(results) == 2
    assert results[0].is_valid
    assert not results[1].is_valid


@pytest.mark.parametrize("bad_value", [math.nan, math.inf, -math.inf])
def test_various_non_finite_values_are_rejected(bad_value: float) -> None:
    result = validate_feature_value(_make_value(value=bad_value), now=FIXED_NOW)
    assert not result.is_valid
