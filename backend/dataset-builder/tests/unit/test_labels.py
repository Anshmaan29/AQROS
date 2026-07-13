"""Unit tests for label computation (pure domain logic, no I/O).

Expected values below are independently derived (see comments) rather than
copied from the implementation, so these tests genuinely verify
correctness, not just self-consistency.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from aqros_dataset_builder.domain.labels import (
    binary_direction,
    compute_label,
    future_return,
    volatility,
)
from aqros_dataset_builder.domain.models import LabelType, PredictionHorizon


def _assert_series_close(actual: pd.Series, expected: list[float], *, tol: float = 1e-9) -> None:
    assert len(actual) == len(expected)
    for a, e in zip(actual.tolist(), expected, strict=True):
        if math.isnan(e):
            assert math.isnan(a), f"expected NaN, got {a}"
        else:
            assert a == pytest.approx(e, abs=tol), f"expected {e}, got {a}"


def test_binary_direction_one_day_horizon() -> None:
    close = pd.Series([10.0, 11.0, 12.0, 11.0, 13.0])
    result = binary_direction(close, PredictionHorizon.ONE_DAY)
    # 10->11 up, 11->12 up, 12->11 down, 11->13 up, 13->? unknown (NaN)
    expected = [1.0, 1.0, 0.0, 1.0, float("nan")]
    _assert_series_close(result, expected)


def test_binary_direction_equal_price_is_down() -> None:
    close = pd.Series([10.0, 10.0])
    result = binary_direction(close, PredictionHorizon.ONE_DAY)
    # future_close > close is False when equal -> labeled 0.0, not up.
    assert result.iloc[0] == 0.0


def test_future_return_one_day_horizon() -> None:
    close = pd.Series([10.0, 11.0, 12.0, 11.0, 13.0])
    result = future_return(close, PredictionHorizon.ONE_DAY)
    expected = [
        0.10000000000000009,
        0.09090909090909083,
        -0.08333333333333337,
        0.18181818181818188,
        float("nan"),
    ]
    _assert_series_close(result, expected)


def test_volatility_five_day_horizon() -> None:
    close = pd.Series([10.0, 11.0, 9.0, 12.0, 8.0, 15.0, 10.0])
    result = volatility(close, PredictionHorizon.FIVE_DAY)
    # Hand-verified: population std of log-returns for bars 1..5 (the
    # forward 5-bar window starting right after index 0).
    assert result.iloc[0] == pytest.approx(0.36298323460376913, abs=1e-9)
    assert result.iloc[1] == pytest.approx(0.4111363888637623, abs=1e-9)
    # From index 2 onward there aren't 5 more bars available -> NaN.
    assert math.isnan(result.iloc[2])
    assert math.isnan(result.iloc[-1])


def test_labels_never_use_more_future_than_the_horizon_allows() -> None:
    """A label at the last `horizon` rows of a series must be NaN.

    This is the direct test of the module's central claim: labels are
    never fabricated past the end of known history.
    """
    close = pd.Series([float(x) for x in range(1, 11)])
    for label_type in LabelType:
        for horizon in PredictionHorizon:
            result = compute_label(close, label_type, horizon)
            trailing = result.iloc[-horizon.bars :]
            assert trailing.isna().all(), (
                f"{label_type.value}@{horizon.value}: expected the trailing "
                f"{horizon.bars} rows to be NaN, got {trailing.tolist()}"
            )


def test_compute_label_dispatches_correctly() -> None:
    close = pd.Series([10.0, 11.0, 12.0, 11.0, 13.0])
    direct = binary_direction(close, PredictionHorizon.ONE_DAY)
    dispatched = compute_label(close, LabelType.BINARY_DIRECTION, PredictionHorizon.ONE_DAY)
    pd.testing.assert_series_equal(direct, dispatched)
