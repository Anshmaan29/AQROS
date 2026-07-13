"""Validation rules for incoming OHLCV data.

Pure functions — no I/O. Bad data must be rejected here, before it ever
reaches persistence (CLAUDE.md §5: "no query, backtest, or feature may ever
use data before its knowledge_time"; docs/claude_ROI.md §13 requires
declarative, business-rule and temporal validation gates between zones).
Failing bars are never silently dropped — callers get a list of every
violation so ingestion can report exactly what was rejected and why.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from aqros_market_data.domain.models import OHLCVBar


class OHLCVValidationError(ValueError):
    """Raised when a bar fails validation and the caller requires a hard stop."""

    def __init__(self, bar: OHLCVBar, violations: list[str]) -> None:
        self.bar = bar
        self.violations = violations
        joined = "; ".join(violations)
        super().__init__(f"Invalid OHLCV bar for {bar.symbol} at {bar.event_time}: {joined}")


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Outcome of validating a single bar."""

    bar: OHLCVBar
    violations: list[str]

    @property
    def is_valid(self) -> bool:
        return not self.violations


def validate_bar(bar: OHLCVBar, *, now: datetime | None = None) -> ValidationResult:
    """Validate a single OHLCV bar against business and temporal rules.

    ``now`` is injected (never read implicitly from the wall clock) so this
    function stays deterministic and testable, per CLAUDE.md §5 ("no wall-clock
    time in domain logic").
    """
    reference_now = now if now is not None else datetime.now(UTC)
    violations: list[str] = []

    for field_name, value in (
        ("open", bar.open),
        ("high", bar.high),
        ("low", bar.low),
        ("close", bar.close),
    ):
        if value <= Decimal(0):
            violations.append(f"{field_name} must be positive, got {value}")

    if bar.volume < 0:
        violations.append(f"volume must be non-negative, got {bar.volume}")

    if bar.high < bar.low:
        violations.append(f"high ({bar.high}) is less than low ({bar.low})")

    if bar.high < bar.open or bar.high < bar.close:
        violations.append(f"high ({bar.high}) must be >= open and close")

    if bar.low > bar.open or bar.low > bar.close:
        violations.append(f"low ({bar.low}) must be <= open and close")

    if bar.adjusted_close is not None and bar.adjusted_close <= Decimal(0):
        violations.append(f"adjusted_close must be positive, got {bar.adjusted_close}")

    if not bar.symbol or not bar.symbol.strip():
        violations.append("symbol must not be empty")

    # Temporal / point-in-time sanity: a bar cannot be knowable before it
    # happened, and neither timestamp may be in the future relative to `now`.
    if bar.knowledge_time < bar.event_time:
        violations.append("knowledge_time cannot precede event_time")

    if bar.event_time > reference_now:
        violations.append(
            f"event_time {bar.event_time} is in the future relative to {reference_now}"
        )

    if bar.knowledge_time > reference_now:
        violations.append(
            f"knowledge_time {bar.knowledge_time} is in the future relative to {reference_now}"
        )

    return ValidationResult(bar=bar, violations=violations)


def validate_bars(bars: list[OHLCVBar], *, now: datetime | None = None) -> list[ValidationResult]:
    """Validate a batch of bars, returning one result per input bar."""
    return [validate_bar(bar, now=now) for bar in bars]
