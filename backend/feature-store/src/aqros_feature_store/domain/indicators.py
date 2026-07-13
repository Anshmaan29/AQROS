"""Technical indicator computations.

Pure functions over pandas Series/DataFrames — no I/O, no framework imports.
Every indicator here is **strictly causal**: the value at row ``t`` is a
function of rows ``<= t`` only. This is the feature-level expression of
CLAUDE.md §6.3 ("point-in-time correctness is sacred... no feature... may
ever use data before its knowledge_time") — concretely, that means:

- ``pandas.Series.rolling(window)`` (trailing, right-aligned) — never
  ``center=True``.
- ``pandas.Series.shift(n)`` with ``n > 0`` (looks backward) — never a
  negative shift (which would look forward).
- ``pandas.Series.ewm(...)`` — exponentially weighted, causal by
  construction.

Callers must pass series already sorted ascending by ``event_time``; these
functions do not re-sort (sorting is the repository/service's job, once,
rather than every indicator re-deriving order from an index it can't fully
trust).

All indicators return ``NaN`` for the initial rows that don't yet have
enough history (e.g. the first 19 rows of a 20-period SMA) — callers must
drop or otherwise handle NaNs before persisting (see
``domain/validation.py``); we never fabricate a value for missing history.
"""

from __future__ import annotations

from typing import cast

import numpy as np
import pandas as pd


def sma(close: pd.Series, window: int) -> pd.Series:
    """Simple Moving Average over the trailing ``window`` bars."""
    return close.rolling(window=window, min_periods=window).mean()


def ema(close: pd.Series, window: int) -> pd.Series:
    """Exponential Moving Average with span ``window`` (causal by construction)."""
    return close.ewm(span=window, adjust=False, min_periods=window).mean()


def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    """Relative Strength Index (Wilder's smoothing), range [0, 100].

    Uses only past price changes (``diff()`` looks backward by one bar) and
    a trailing exponential average of gains/losses (Wilder's original
    smoothing, alpha = 1/window).
    """
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

    avg_gain = gain.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()

    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    result = 100.0 - (100.0 / (1.0 + rs))
    # When avg_loss is exactly 0 (all gains), RSI is defined as 100.
    result = result.where(avg_loss != 0.0, 100.0)
    return result.where(avg_gain.notna() & avg_loss.notna())


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """Moving Average Convergence/Divergence.

    Returns a DataFrame with columns ``macd``, ``signal``, ``histogram``.
    """
    fast_ema = ema(close, fast)
    slow_ema = ema(close, slow)
    macd_line = fast_ema - slow_ema
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    histogram = macd_line - signal_line
    return pd.DataFrame({"macd": macd_line, "signal": signal_line, "histogram": histogram})


def atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    """Average True Range (Wilder's smoothing) — a volatility measure.

    True range uses the *previous* close (``close.shift(1)``, backward-only)
    alongside the current bar's high/low.
    """
    prev_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()


def bollinger_bands(close: pd.Series, window: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    """Bollinger Bands: trailing SMA +/- ``num_std`` trailing standard deviations.

    Returns a DataFrame with columns ``middle``, ``upper``, ``lower``.
    """
    middle = sma(close, window)
    std = close.rolling(window=window, min_periods=window).std(ddof=0)
    upper = middle + num_std * std
    lower = middle - num_std * std
    return pd.DataFrame({"middle": middle, "upper": upper, "lower": lower})


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On-Balance Volume: cumulative volume signed by the price change direction.

    Uses only ``close.diff()`` (backward-looking) to determine sign; the
    cumulative sum is inherently causal (each value depends only on prior
    values).
    """
    direction = np.sign(close.diff().fillna(0.0))
    signed_volume = direction * volume
    result: pd.Series = signed_volume.cumsum()
    return result


def rolling_vwap(
    close: pd.Series, volume: pd.Series, high: pd.Series, low: pd.Series, window: int = 20
) -> pd.Series:
    """Rolling Volume-Weighted Average Price over the trailing ``window`` bars.

    Uses the typical price ``(high + low + close) / 3`` per bar (a standard
    VWAP approximation when tick-level trade data isn't available), weighted
    by volume, over a trailing window — never a full-session VWAP reset,
    since bar data alone carries no session boundary here.
    """
    typical_price = (high + low + close) / 3.0
    pv = typical_price * volume
    rolling_pv = pv.rolling(window=window, min_periods=window).sum()
    rolling_vol = volume.rolling(window=window, min_periods=window).sum()
    return rolling_pv / rolling_vol.replace(0.0, np.nan)


def daily_return(close: pd.Series) -> pd.Series:
    """Simple daily (bar-over-bar) percentage return."""
    return close.pct_change(periods=1)


def log_return(close: pd.Series) -> pd.Series:
    """Logarithmic bar-over-bar return: ln(close_t / close_{t-1})."""
    ratio = close / close.shift(1)
    return cast(pd.Series, np.log(ratio))


def rolling_mean(close: pd.Series, window: int) -> pd.Series:
    """Trailing rolling mean over ``window`` bars."""
    return close.rolling(window=window, min_periods=window).mean()


def rolling_std(close: pd.Series, window: int) -> pd.Series:
    """Trailing rolling (population) standard deviation over ``window`` bars."""
    return close.rolling(window=window, min_periods=window).std(ddof=0)


def rolling_min(close: pd.Series, window: int) -> pd.Series:
    """Trailing rolling minimum over ``window`` bars."""
    return close.rolling(window=window, min_periods=window).min()


def rolling_max(close: pd.Series, window: int) -> pd.Series:
    """Trailing rolling maximum over ``window`` bars."""
    return close.rolling(window=window, min_periods=window).max()
