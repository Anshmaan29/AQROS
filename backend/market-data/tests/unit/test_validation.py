"""Unit tests for OHLCV validation rules (pure domain logic, no I/O)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from aqros_market_data.domain.models import BarInterval, OHLCVBar
from aqros_market_data.domain.validation import validate_bar, validate_bars

FIXED_NOW = datetime(2024, 1, 15, tzinfo=UTC)


def _make_bar(**overrides: object) -> OHLCVBar:
    defaults: dict[str, object] = {
        "symbol": "AAPL",
        "event_time": datetime(2024, 1, 10, tzinfo=UTC),
        "interval": BarInterval.DAILY,
        "open": Decimal("100.00"),
        "high": Decimal("105.00"),
        "low": Decimal("99.00"),
        "close": Decimal("103.00"),
        "volume": 1_000_000,
        "source": "yfinance",
        "knowledge_time": datetime(2024, 1, 11, tzinfo=UTC),
        "adjusted_close": Decimal("103.00"),
    }
    defaults.update(overrides)
    return OHLCVBar(**defaults)  # type: ignore[arg-type]


def test_valid_bar_passes() -> None:
    result = validate_bar(_make_bar(), now=FIXED_NOW)
    assert result.is_valid
    assert result.violations == []


@pytest.mark.parametrize("field", ["open", "high", "low", "close"])
def test_non_positive_prices_are_rejected(field: str) -> None:
    result = validate_bar(_make_bar(**{field: Decimal("0")}), now=FIXED_NOW)
    assert not result.is_valid
    assert any(field in v for v in result.violations)


def test_negative_volume_is_rejected() -> None:
    result = validate_bar(_make_bar(volume=-1), now=FIXED_NOW)
    assert not result.is_valid
    assert any("volume" in v for v in result.violations)


def test_high_less_than_low_is_rejected() -> None:
    result = validate_bar(_make_bar(high=Decimal("90"), low=Decimal("95")), now=FIXED_NOW)
    assert not result.is_valid
    assert any("high" in v and "low" in v for v in result.violations)


def test_high_below_open_or_close_is_rejected() -> None:
    result = validate_bar(_make_bar(high=Decimal("101"), close=Decimal("103")), now=FIXED_NOW)
    assert not result.is_valid


def test_low_above_open_or_close_is_rejected() -> None:
    result = validate_bar(_make_bar(low=Decimal("102"), open=Decimal("100")), now=FIXED_NOW)
    assert not result.is_valid


def test_negative_adjusted_close_is_rejected() -> None:
    result = validate_bar(_make_bar(adjusted_close=Decimal("-1")), now=FIXED_NOW)
    assert not result.is_valid
    assert any("adjusted_close" in v for v in result.violations)


def test_empty_symbol_is_rejected() -> None:
    result = validate_bar(_make_bar(symbol="   "), now=FIXED_NOW)
    assert not result.is_valid
    assert any("symbol" in v for v in result.violations)


def test_knowledge_time_before_event_time_is_rejected() -> None:
    result = validate_bar(
        _make_bar(
            event_time=datetime(2024, 1, 10, tzinfo=UTC),
            knowledge_time=datetime(2024, 1, 9, tzinfo=UTC),
        ),
        now=FIXED_NOW,
    )
    assert not result.is_valid
    assert any("knowledge_time" in v for v in result.violations)


def test_future_event_time_is_rejected() -> None:
    result = validate_bar(
        _make_bar(
            event_time=datetime(2024, 2, 1, tzinfo=UTC),
            knowledge_time=datetime(2024, 2, 1, tzinfo=UTC),
        ),
        now=FIXED_NOW,
    )
    assert not result.is_valid
    assert any("future" in v for v in result.violations)


def test_future_knowledge_time_is_rejected() -> None:
    # event_time is valid (in the past) but knowledge_time claims the future —
    # this is precisely the lookahead trap CLAUDE.md §5/docs/claude_ROI.md §17
    # require the system to make structurally impossible.
    result = validate_bar(
        _make_bar(
            event_time=datetime(2024, 1, 10, tzinfo=UTC),
            knowledge_time=datetime(2024, 2, 1, tzinfo=UTC),
        ),
        now=FIXED_NOW,
    )
    assert not result.is_valid
    assert any("future" in v for v in result.violations)


def test_validate_bars_batch_returns_one_result_per_bar() -> None:
    bars = [_make_bar(), _make_bar(volume=-1)]
    results = validate_bars(bars, now=FIXED_NOW)
    assert len(results) == 2
    assert results[0].is_valid
    assert not results[1].is_valid
