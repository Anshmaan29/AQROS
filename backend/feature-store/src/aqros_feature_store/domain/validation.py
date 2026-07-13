"""Validation rules for computed feature values.

Pure functions — no I/O. Mirrors the discipline in
``aqros_market_data.domain.validation``: bad data is rejected here, before it
ever reaches persistence, and callers get every violation back rather than a
silent drop, so a computation run can report exactly what failed and why.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime

from aqros_feature_store.domain.models import FeatureValue


@dataclass(frozen=True, slots=True)
class FeatureValidationResult:
    """Outcome of validating a single feature value."""

    value: FeatureValue
    violations: list[str]

    @property
    def is_valid(self) -> bool:
        return not self.violations


def validate_feature_value(
    value: FeatureValue, *, now: datetime | None = None
) -> FeatureValidationResult:
    """Validate a single computed feature value.

    ``now`` is injected (never read implicitly from the wall clock) so this
    stays deterministic and testable, per CLAUDE.md §5.
    """
    reference_now = now if now is not None else datetime.now(UTC)
    violations: list[str] = []

    if math.isnan(value.value):
        violations.append("value is NaN (insufficient history or undefined computation)")
    elif math.isinf(value.value):
        violations.append("value is infinite")

    if not value.symbol or not value.symbol.strip():
        violations.append("symbol must not be empty")

    if not value.feature_name or not value.feature_name.strip():
        violations.append("feature_name must not be empty")

    if value.feature_version < 1:
        violations.append(f"feature_version must be >= 1, got {value.feature_version}")

    # Point-in-time sanity, mirroring market-data's OHLCV validator: a
    # feature cannot be knowable before the bar it describes, and neither
    # timestamp may be in the future relative to `now`.
    if value.knowledge_time < value.event_time:
        violations.append("knowledge_time cannot precede event_time")

    if value.event_time > reference_now:
        violations.append(
            f"event_time {value.event_time} is in the future relative to {reference_now}"
        )

    if value.knowledge_time > reference_now:
        violations.append(
            f"knowledge_time {value.knowledge_time} is in the future relative to {reference_now}"
        )

    return FeatureValidationResult(value=value, violations=violations)


def validate_feature_values(
    values: list[FeatureValue], *, now: datetime | None = None
) -> list[FeatureValidationResult]:
    """Validate a batch of feature values, one result per input value."""
    return [validate_feature_value(v, now=now) for v in values]
