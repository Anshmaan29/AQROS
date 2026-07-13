"""The feature catalog: default feature definitions and their compute functions.

This module is the single place a feature is registered. Each entry pairs an
immutable :class:`FeatureDefinition` (name, version, category, parameters,
min-bars-required) with a pure function that computes it from an OHLCV
DataFrame. Adding a new feature means adding one entry here — nothing else
in ``domain/``, ``adapters/``, or ``api/`` needs to change (the same
"register once, compose everywhere" pattern market-data uses for providers).

Multi-output indicators (MACD, Bollinger Bands) are registered as several
independent named features (e.g. ``macd_line``, ``macd_signal``,
``macd_histogram``) since :class:`FeatureValue` is single-valued per name —
this keeps persistence and querying uniform across every feature.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import pandas as pd

from aqros_feature_store.domain import indicators
from aqros_feature_store.domain.models import FeatureCategory, FeatureDefinition

FeatureComputeFn = Callable[[pd.DataFrame], pd.Series]


@dataclass(frozen=True, slots=True)
class FeatureRegistration:
    """A feature definition paired with the function that computes its values."""

    definition: FeatureDefinition
    compute: FeatureComputeFn


def _sma_20(bars: pd.DataFrame) -> pd.Series:
    return indicators.sma(bars["close"], window=20)


def _ema_20(bars: pd.DataFrame) -> pd.Series:
    return indicators.ema(bars["close"], window=20)


def _rsi_14(bars: pd.DataFrame) -> pd.Series:
    return indicators.rsi(bars["close"], window=14)


def _macd_line(bars: pd.DataFrame) -> pd.Series:
    return indicators.macd(bars["close"], fast=12, slow=26, signal=9)["macd"]


def _macd_signal(bars: pd.DataFrame) -> pd.Series:
    return indicators.macd(bars["close"], fast=12, slow=26, signal=9)["signal"]


def _macd_histogram(bars: pd.DataFrame) -> pd.Series:
    return indicators.macd(bars["close"], fast=12, slow=26, signal=9)["histogram"]


def _atr_14(bars: pd.DataFrame) -> pd.Series:
    return indicators.atr(bars["high"], bars["low"], bars["close"], window=14)


def _bollinger_upper(bars: pd.DataFrame) -> pd.Series:
    return indicators.bollinger_bands(bars["close"], window=20, num_std=2.0)["upper"]


def _bollinger_middle(bars: pd.DataFrame) -> pd.Series:
    return indicators.bollinger_bands(bars["close"], window=20, num_std=2.0)["middle"]


def _bollinger_lower(bars: pd.DataFrame) -> pd.Series:
    return indicators.bollinger_bands(bars["close"], window=20, num_std=2.0)["lower"]


def _obv(bars: pd.DataFrame) -> pd.Series:
    return indicators.obv(bars["close"], bars["volume"])


def _vwap_20(bars: pd.DataFrame) -> pd.Series:
    return indicators.rolling_vwap(
        bars["close"], bars["volume"], bars["high"], bars["low"], window=20
    )


def _daily_return(bars: pd.DataFrame) -> pd.Series:
    return indicators.daily_return(bars["close"])


def _log_return(bars: pd.DataFrame) -> pd.Series:
    return indicators.log_return(bars["close"])


def _rolling_mean_20(bars: pd.DataFrame) -> pd.Series:
    return indicators.rolling_mean(bars["close"], window=20)


def _rolling_std_20(bars: pd.DataFrame) -> pd.Series:
    return indicators.rolling_std(bars["close"], window=20)


def _rolling_min_20(bars: pd.DataFrame) -> pd.Series:
    return indicators.rolling_min(bars["close"], window=20)


def _rolling_max_20(bars: pd.DataFrame) -> pd.Series:
    return indicators.rolling_max(bars["close"], window=20)


# The default catalog — every feature required by the milestone, at version 1.
# `min_bars_required` is deliberately conservative (a few bars above the
# mathematical minimum) so that indicators relying on smoothed/lagged inputs
# (MACD's signal line, RSI's Wilder smoothing) have settled past their warm-up
# transient before we persist a value, not just past the point where pandas
# stops returning NaN.
FEATURE_REGISTRY: tuple[FeatureRegistration, ...] = (
    FeatureRegistration(
        FeatureDefinition(
            name="sma_20",
            version=1,
            category=FeatureCategory.TREND,
            description="20-period Simple Moving Average of close price.",
            parameters={"window": 20},
            min_bars_required=20,
        ),
        _sma_20,
    ),
    FeatureRegistration(
        FeatureDefinition(
            name="ema_20",
            version=1,
            category=FeatureCategory.TREND,
            description="20-period Exponential Moving Average of close price.",
            parameters={"window": 20},
            min_bars_required=20,
        ),
        _ema_20,
    ),
    FeatureRegistration(
        FeatureDefinition(
            name="rsi_14",
            version=1,
            category=FeatureCategory.MOMENTUM,
            description="14-period Relative Strength Index (Wilder's smoothing).",
            parameters={"window": 14},
            min_bars_required=15,
        ),
        _rsi_14,
    ),
    FeatureRegistration(
        FeatureDefinition(
            name="macd_line",
            version=1,
            category=FeatureCategory.MOMENTUM,
            description="MACD line: EMA(12) - EMA(26) of close price.",
            parameters={"fast": 12, "slow": 26, "signal": 9},
            min_bars_required=26,
        ),
        _macd_line,
    ),
    FeatureRegistration(
        FeatureDefinition(
            name="macd_signal",
            version=1,
            category=FeatureCategory.MOMENTUM,
            description="MACD signal line: 9-period EMA of the MACD line.",
            parameters={"fast": 12, "slow": 26, "signal": 9},
            min_bars_required=34,
        ),
        _macd_signal,
    ),
    FeatureRegistration(
        FeatureDefinition(
            name="macd_histogram",
            version=1,
            category=FeatureCategory.MOMENTUM,
            description="MACD histogram: MACD line minus its signal line.",
            parameters={"fast": 12, "slow": 26, "signal": 9},
            min_bars_required=34,
        ),
        _macd_histogram,
    ),
    FeatureRegistration(
        FeatureDefinition(
            name="atr_14",
            version=1,
            category=FeatureCategory.VOLATILITY,
            description="14-period Average True Range (Wilder's smoothing).",
            parameters={"window": 14},
            min_bars_required=15,
        ),
        _atr_14,
    ),
    FeatureRegistration(
        FeatureDefinition(
            name="bollinger_upper",
            version=1,
            category=FeatureCategory.VOLATILITY,
            description="Bollinger Band upper: 20-period SMA + 2 std devs.",
            parameters={"window": 20, "num_std": 2.0},
            min_bars_required=20,
        ),
        _bollinger_upper,
    ),
    FeatureRegistration(
        FeatureDefinition(
            name="bollinger_middle",
            version=1,
            category=FeatureCategory.VOLATILITY,
            description="Bollinger Band middle: 20-period SMA of close price.",
            parameters={"window": 20, "num_std": 2.0},
            min_bars_required=20,
        ),
        _bollinger_middle,
    ),
    FeatureRegistration(
        FeatureDefinition(
            name="bollinger_lower",
            version=1,
            category=FeatureCategory.VOLATILITY,
            description="Bollinger Band lower: 20-period SMA - 2 std devs.",
            parameters={"window": 20, "num_std": 2.0},
            min_bars_required=20,
        ),
        _bollinger_lower,
    ),
    FeatureRegistration(
        FeatureDefinition(
            name="obv",
            version=1,
            category=FeatureCategory.VOLUME,
            description="On-Balance Volume: cumulative volume signed by price direction.",
            parameters={},
            min_bars_required=1,
        ),
        _obv,
    ),
    FeatureRegistration(
        FeatureDefinition(
            name="vwap_20",
            version=1,
            category=FeatureCategory.VOLUME,
            description="20-period rolling Volume-Weighted Average Price.",
            parameters={"window": 20},
            min_bars_required=20,
        ),
        _vwap_20,
    ),
    FeatureRegistration(
        FeatureDefinition(
            name="daily_return",
            version=1,
            category=FeatureCategory.RETURNS,
            description="Simple bar-over-bar percentage return.",
            parameters={},
            min_bars_required=2,
        ),
        _daily_return,
    ),
    FeatureRegistration(
        FeatureDefinition(
            name="log_return",
            version=1,
            category=FeatureCategory.RETURNS,
            description="Logarithmic bar-over-bar return.",
            parameters={},
            min_bars_required=2,
        ),
        _log_return,
    ),
    FeatureRegistration(
        FeatureDefinition(
            name="rolling_mean_20",
            version=1,
            category=FeatureCategory.ROLLING_STATS,
            description="20-period trailing rolling mean of close price.",
            parameters={"window": 20},
            min_bars_required=20,
        ),
        _rolling_mean_20,
    ),
    FeatureRegistration(
        FeatureDefinition(
            name="rolling_std_20",
            version=1,
            category=FeatureCategory.ROLLING_STATS,
            description="20-period trailing rolling standard deviation of close price.",
            parameters={"window": 20},
            min_bars_required=20,
        ),
        _rolling_std_20,
    ),
    FeatureRegistration(
        FeatureDefinition(
            name="rolling_min_20",
            version=1,
            category=FeatureCategory.ROLLING_STATS,
            description="20-period trailing rolling minimum of close price.",
            parameters={"window": 20},
            min_bars_required=20,
        ),
        _rolling_min_20,
    ),
    FeatureRegistration(
        FeatureDefinition(
            name="rolling_max_20",
            version=1,
            category=FeatureCategory.ROLLING_STATS,
            description="20-period trailing rolling maximum of close price.",
            parameters={"window": 20},
            min_bars_required=20,
        ),
        _rolling_max_20,
    ),
)


def get_registry() -> Sequence[FeatureRegistration]:
    """Return the default feature catalog."""
    return FEATURE_REGISTRY


def get_definitions() -> Sequence[FeatureDefinition]:
    """Return just the definitions (no compute functions) from the catalog."""
    return [reg.definition for reg in FEATURE_REGISTRY]
