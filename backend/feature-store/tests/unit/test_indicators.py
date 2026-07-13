"""Unit tests for technical indicator computations (pure domain logic, no I/O).

Expected values below are independently derived (see the module docstring
comments referencing simple hand/pandas checks) rather than copied from the
implementation, so these tests genuinely verify correctness, not just
self-consistency.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from aqros_feature_store.domain import indicators


def _assert_series_close(actual: pd.Series, expected: list[float], *, tol: float = 1e-9) -> None:
    assert len(actual) == len(expected)
    for a, e in zip(actual.tolist(), expected, strict=True):
        if math.isnan(e):
            assert math.isnan(a), f"expected NaN, got {a}"
        else:
            assert a == pytest.approx(e, abs=tol), f"expected {e}, got {a}"


def test_sma_matches_trailing_window_mean() -> None:
    close = pd.Series([10.0, 11.0, 12.0, 11.0, 13.0, 14.0, 13.0, 15.0])
    result = indicators.sma(close, window=3)
    expected = [
        float("nan"),
        float("nan"),
        11.0,
        11.333333333333334,
        12.0,
        12.666666666666666,
        13.333333333333334,
        14.0,
    ]
    _assert_series_close(result, expected)


def test_ema_is_causal_and_matches_ewm_formula() -> None:
    close = pd.Series([10.0, 11.0, 12.0, 11.0, 13.0, 14.0, 13.0, 15.0])
    result = indicators.ema(close, window=3)
    expected = [
        float("nan"),
        float("nan"),
        11.25,
        11.125,
        12.0625,
        13.03125,
        13.015625,
        14.0078125,
    ]
    _assert_series_close(result, expected)


def test_rsi_is_100_for_strictly_increasing_prices() -> None:
    # A strictly increasing series has zero losses, so RSI must saturate at
    # 100 rather than divide by zero or return NaN past the warm-up window.
    close = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    result = indicators.rsi(close, window=3)
    expected = [float("nan"), float("nan"), float("nan"), 100.0, 100.0, 100.0]
    _assert_series_close(result, expected)


def test_rsi_is_bounded_between_0_and_100() -> None:
    rng = np.random.default_rng(seed=42)
    close = pd.Series(100.0 + rng.normal(0, 1, size=200).cumsum())
    result = indicators.rsi(close, window=14).dropna()
    assert (result >= 0.0).all()
    assert (result <= 100.0).all()


def test_macd_returns_macd_signal_histogram_columns() -> None:
    close = pd.Series([float(x) for x in range(1, 40)])
    result = indicators.macd(close, fast=3, slow=6, signal=2)
    assert list(result.columns) == ["macd", "signal", "histogram"]

    macd_tail = result["macd"].tail(3).tolist()
    signal_tail = result["signal"].tail(3).tolist()
    expected_macd_tail = [1.4999862807533688, 1.4999902005350023, 1.4999930003805844]
    expected_signal_tail = [1.499982850952623, 1.4999877506742092, 1.4999912504784594]
    for a, e in zip(macd_tail, expected_macd_tail, strict=True):
        assert a == pytest.approx(e, abs=1e-9)
    for a, e in zip(signal_tail, expected_signal_tail, strict=True):
        assert a == pytest.approx(e, abs=1e-9)

    # histogram is always macd - signal by construction
    hist = result["macd"] - result["signal"]
    pd.testing.assert_series_equal(result["histogram"], hist, check_names=False)


def test_atr_uses_true_range_and_wilder_smoothing() -> None:
    high = pd.Series([12.0, 13.0, 14.0, 13.0])
    low = pd.Series([9.0, 10.0, 11.0, 10.0])
    close = pd.Series([10.0, 12.0, 13.0, 11.0])
    result = indicators.atr(high, low, close, window=2)
    # True range is 3.0 for every bar in this fixture (hand-computed); with
    # Wilder smoothing (alpha=1/2, min_periods=2) the first value is NaN and
    # every subsequent value stays at exactly 3.0 since TR never changes.
    expected = [float("nan"), 3.0, 3.0, 3.0]
    _assert_series_close(result, expected)


def test_bollinger_bands_middle_is_sma_and_bands_bracket_it() -> None:
    close = pd.Series([10.0, 12.0, 14.0, 16.0, 18.0])
    result = indicators.bollinger_bands(close, window=5, num_std=2.0)
    assert list(result.columns) == ["middle", "upper", "lower"]

    middle_last = result["middle"].iloc[-1]
    upper_last = result["upper"].iloc[-1]
    lower_last = result["lower"].iloc[-1]
    assert middle_last == pytest.approx(14.0)
    std = 2.8284271247461903
    assert upper_last == pytest.approx(14.0 + 2.0 * std)
    assert lower_last == pytest.approx(14.0 - 2.0 * std)
    assert upper_last > middle_last > lower_last


def test_obv_accumulates_signed_volume_by_price_direction() -> None:
    close = pd.Series([10.0, 11.0, 10.5, 12.0])
    volume = pd.Series([100.0, 200.0, 150.0, 300.0])
    result = indicators.obv(close, volume)
    # direction: [0 (first bar), +1, -1, +1] -> signed volume [0,200,-150,300]
    # -> cumulative [0, 200, 50, 350]
    expected = [0.0, 200.0, 50.0, 350.0]
    _assert_series_close(result, expected)


def test_rolling_vwap_matches_volume_weighted_typical_price() -> None:
    high = pd.Series([11.0, 12.0, 13.0])
    low = pd.Series([9.0, 10.0, 11.0])
    close = pd.Series([10.0, 11.0, 12.0])
    volume = pd.Series([100.0, 200.0, 300.0])
    result = indicators.rolling_vwap(close, volume, high, low, window=3)
    expected = [float("nan"), float("nan"), 11.333333333333334]
    _assert_series_close(result, expected)


def test_daily_return_matches_pct_change() -> None:
    close = pd.Series([10.0, 11.0, 9.9])
    result = indicators.daily_return(close)
    expected = [float("nan"), 0.10000000000000009, -0.09999999999999998]
    _assert_series_close(result, expected)


def test_log_return_matches_log_of_ratio() -> None:
    close = pd.Series([10.0, 11.0, 9.9])
    result = indicators.log_return(close)
    expected = [float("nan"), 0.09531017980432493, -0.10536051565782628]
    _assert_series_close(result, expected)


def test_rolling_mean_std_min_max_over_trailing_window() -> None:
    close = pd.Series([5.0, 7.0, 3.0, 9.0, 6.0])
    window = 3

    mean = indicators.rolling_mean(close, window)
    std = indicators.rolling_std(close, window)
    minimum = indicators.rolling_min(close, window)
    maximum = indicators.rolling_max(close, window)

    # Trailing window [5,7,3] at index 2
    assert mean.iloc[2] == pytest.approx((5.0 + 7.0 + 3.0) / 3.0)
    assert minimum.iloc[2] == pytest.approx(3.0)
    assert maximum.iloc[2] == pytest.approx(7.0)
    assert std.iloc[2] == pytest.approx(pd.Series([5.0, 7.0, 3.0]).std(ddof=0))

    # First two rows lack enough history for a 3-window indicator.
    assert math.isnan(mean.iloc[0])
    assert math.isnan(mean.iloc[1])


def test_indicators_are_strictly_causal_no_lookahead() -> None:
    """Truncating the series must not change any already-computed value.

    This is the direct test of the module's central claim: every indicator's
    value at row t depends only on rows <= t. If an indicator were even
    slightly forward-looking, dropping future rows would change earlier
    values.
    """
    close = pd.Series([10.0, 11.0, 12.0, 11.0, 13.0, 14.0, 13.0, 15.0, 16.0, 17.0])
    high = close + 1.0
    low = close - 1.0
    volume = pd.Series([100.0 + i * 10 for i in range(len(close))])

    full_sma = indicators.sma(close, window=3)
    truncated_sma = indicators.sma(close.iloc[:7], window=3)
    pd.testing.assert_series_equal(full_sma.iloc[:7], truncated_sma, check_names=False)

    full_ema = indicators.ema(close, window=3)
    truncated_ema = indicators.ema(close.iloc[:7], window=3)
    pd.testing.assert_series_equal(full_ema.iloc[:7], truncated_ema, check_names=False)

    full_rsi = indicators.rsi(close, window=3)
    truncated_rsi = indicators.rsi(close.iloc[:7], window=3)
    pd.testing.assert_series_equal(full_rsi.iloc[:7], truncated_rsi, check_names=False)

    full_atr = indicators.atr(high, low, close, window=3)
    truncated_atr = indicators.atr(high.iloc[:7], low.iloc[:7], close.iloc[:7], window=3)
    pd.testing.assert_series_equal(full_atr.iloc[:7], truncated_atr, check_names=False)

    full_obv = indicators.obv(close, volume)
    truncated_obv = indicators.obv(close.iloc[:7], volume.iloc[:7])
    pd.testing.assert_series_equal(full_obv.iloc[:7], truncated_obv, check_names=False)
