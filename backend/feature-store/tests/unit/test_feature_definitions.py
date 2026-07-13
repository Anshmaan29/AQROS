"""Unit tests for the feature catalog (registry -> compute function wiring)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aqros_feature_store.domain.feature_definitions import FEATURE_REGISTRY, get_definitions
from aqros_feature_store.domain.models import FeatureCategory

EXPECTED_FEATURE_NAMES = {
    "sma_20",
    "ema_20",
    "rsi_14",
    "macd_line",
    "macd_signal",
    "macd_histogram",
    "atr_14",
    "bollinger_upper",
    "bollinger_middle",
    "bollinger_lower",
    "obv",
    "vwap_20",
    "daily_return",
    "log_return",
    "rolling_mean_20",
    "rolling_std_20",
    "rolling_min_20",
    "rolling_max_20",
}


def test_registry_contains_every_required_feature() -> None:
    names = {reg.definition.name for reg in FEATURE_REGISTRY}
    assert names == EXPECTED_FEATURE_NAMES


def test_registry_has_no_duplicate_name_version_pairs() -> None:
    keys = [(reg.definition.name, reg.definition.version) for reg in FEATURE_REGISTRY]
    assert len(keys) == len(set(keys))


def test_get_definitions_returns_definitions_without_compute_functions() -> None:
    definitions = get_definitions()
    assert len(definitions) == len(FEATURE_REGISTRY)
    names = {d.name for d in definitions}
    assert names == EXPECTED_FEATURE_NAMES


@pytest.mark.parametrize(
    "category",
    [
        FeatureCategory.TREND,
        FeatureCategory.MOMENTUM,
        FeatureCategory.VOLATILITY,
        FeatureCategory.VOLUME,
        FeatureCategory.RETURNS,
        FeatureCategory.ROLLING_STATS,
    ],
)
def test_every_category_has_at_least_one_feature(category: FeatureCategory) -> None:
    matching = [reg for reg in FEATURE_REGISTRY if reg.definition.category == category]
    assert len(matching) >= 1


def _synthetic_bars(n: int = 60) -> pd.DataFrame:
    rng = np.random.default_rng(seed=7)
    close = pd.Series(100.0 + rng.normal(0, 1, size=n).cumsum())
    high = close + rng.uniform(0.1, 1.0, size=n)
    low = close - rng.uniform(0.1, 1.0, size=n)
    open_ = close.shift(1).fillna(close.iloc[0])
    volume = pd.Series(rng.uniform(1000, 5000, size=n))
    return pd.DataFrame(
        {
            "event_time": pd.date_range("2024-01-01", periods=n, freq="D"),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


def test_every_registered_feature_computes_without_error_on_synthetic_bars() -> None:
    frame = _synthetic_bars()
    for registration in FEATURE_REGISTRY:
        series = registration.compute(frame)
        assert isinstance(series, pd.Series)
        assert len(series) == len(frame)
        # Every feature should have settled past its warm-up window well
        # within 60 bars and produce at least one real (non-NaN) value.
        assert series.notna().any(), f"{registration.definition.name} produced only NaN"


def test_every_registered_feature_respects_its_min_bars_required() -> None:
    """The catalog's ``min_bars_required`` must never *underclaim*.

    ``min_bars_required`` is a promise: "you need at least this many bars
    before this feature has a real value." That promise is safe as long as
    the indicator's actual first valid value appears at or before bar index
    ``min_bars_required - 1`` (0-indexed) — i.e. the catalog may be
    conservative (claim more bars than strictly necessary) but must never be
    optimistic (claim fewer bars than the computation actually needs, which
    would let a caller treat a NaN warm-up row as if it were ready).
    """
    frame = _synthetic_bars(n=100)
    for registration in FEATURE_REGISTRY:
        series = registration.compute(frame)
        first_valid_index = series.first_valid_index()
        assert first_valid_index is not None
        assert first_valid_index <= registration.definition.min_bars_required - 1, (
            f"{registration.definition.name} needed a value at index "
            f"{first_valid_index}, later than its stated min_bars_required="
            f"{registration.definition.min_bars_required} promises"
        )
