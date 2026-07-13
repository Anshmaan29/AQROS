"""Label computation — the target variable, computed from future *prices only*.

Pure functions over a per-symbol, ascending-by-time close-price series — no
I/O, no framework imports.

The one rule that governs this entire module (`claude_ROI.md` §18.2): **a
label may legitimately use future prices — that is the entire point of a
label — but the corresponding feature row may never use future data.**
These two facts must never be confused, which is exactly why label
computation lives in its own module, entirely separate from feature
computation (which lives in the Feature Store service and is always
strictly causal).

Every function here returns ``NaN`` for the trailing ``horizon`` bars of a
series, where the future price needed to compute the label does not exist
yet. Callers must drop these rows before persisting a row as a training
example (see ``domain/services.py``) — we never fabricate a label for data
that isn't there.
"""

from __future__ import annotations

from typing import cast

import numpy as np
import pandas as pd

from aqros_dataset_builder.domain.models import LabelType, PredictionHorizon


def binary_direction(close: pd.Series, horizon: PredictionHorizon) -> pd.Series:
    """1.0 if price is higher `horizon` bars ahead, 0.0 if lower-or-equal, else NaN.

    Uses only ``close.shift(-horizon)`` (a forward shift) — deliberately the
    *one* place in the whole system a forward shift is allowed, because this
    is a label, not a feature.
    """
    future_close = close.shift(-horizon.bars)
    return (future_close > close).astype(float).where(future_close.notna())


def future_return(close: pd.Series, horizon: PredictionHorizon) -> pd.Series:
    """Simple forward return over `horizon` bars: (close[t+h] / close[t]) - 1."""
    future_close = close.shift(-horizon.bars)
    return (future_close / close) - 1.0


def volatility(close: pd.Series, horizon: PredictionHorizon) -> pd.Series:
    """Realized volatility of daily log returns over the *forward* `horizon`-bar window.

    Computed as the standard deviation of one-bar log returns realized
    strictly *after* t, over the next ``horizon.bars`` bars — i.e. "how
    volatile will this instrument be over the horizon we're predicting,"
    which is itself a legitimate, future-price-derived label (per the
    catalogue in `claude_MLResearchFramework.md` §3.1, task #3).
    """
    ratio = close / close.shift(1)
    log_returns = cast(pd.Series, np.log(ratio))
    # A forward-looking rolling std: at each t, look at log_returns for bars
    # (t+1 .. t+horizon]. Implemented by reversing, taking a trailing rolling
    # std, then reversing back — equivalent to a forward window without
    # relying on pandas' lack of a native forward-rolling API.
    reversed_returns = log_returns.iloc[::-1]
    reversed_std = reversed_returns.rolling(window=horizon.bars, min_periods=horizon.bars).std(
        ddof=0
    )
    forward_std = reversed_std.iloc[::-1]
    # Shift by one bar backward so the window at row t covers (t, t+horizon]
    # rather than [t-horizon+1, t].
    result: pd.Series = forward_std.shift(-1)
    return result


_LABEL_FUNCTIONS = {
    LabelType.BINARY_DIRECTION: binary_direction,
    LabelType.FUTURE_RETURN: future_return,
    LabelType.VOLATILITY: volatility,
}


def compute_label(close: pd.Series, label_type: LabelType, horizon: PredictionHorizon) -> pd.Series:
    """Dispatch to the compute function for ``label_type``."""
    return _LABEL_FUNCTIONS[label_type](close, horizon)
