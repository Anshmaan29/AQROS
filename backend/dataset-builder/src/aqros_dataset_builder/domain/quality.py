"""Dataset quality metrics and basic data-quality validation.

Pure functions over a pandas DataFrame — no I/O, no framework imports.

This module answers "how good is this dataset, descriptively?" — missing
values, duplicate rows, class balance, per-feature statistics — and runs a
small set of sanity checks (e.g. "not >50% of a feature column is missing").
It is deliberately **separate from** ``domain/validation.py``'s leakage
audit: quality metrics never gate whether a dataset may be persisted (a
class-imbalanced or noisy dataset may still be exactly what a researcher
wants); the leakage audit is the one hard gate.
"""

from __future__ import annotations

import pandas as pd

from aqros_dataset_builder.domain.models import (
    DatasetQualityReport,
    FeatureStatistics,
    LabelType,
    SplitRole,
)

# A feature column missing more than this fraction of its values (before
# row-level cleaning) is flagged as a validation finding — it doesn't block
# persistence, but a consumer should know a feature was this sparse over the
# requested date range.
_MAX_ACCEPTABLE_MISSING_FRACTION = 0.5


def compute_missing_value_counts(raw_frame: pd.DataFrame) -> dict[str, int]:
    """Count nulls per column in the raw (pre-clean) joined frame."""
    return {str(col): int(raw_frame[col].isna().sum()) for col in raw_frame.columns}


def compute_duplicate_row_count(rows: list[dict[str, object]]) -> int:
    """Count rows that are exact duplicates of an earlier row (by symbol + event_time + fold)."""
    seen: set[tuple[object, ...]] = set()
    duplicate_count = 0
    for row in rows:
        key = (row.get("symbol"), row.get("event_time"), row.get("fold"), row.get("split_role"))
        if key in seen:
            duplicate_count += 1
        else:
            seen.add(key)
    return duplicate_count


def compute_class_balance(rows: list[dict[str, object]], label_type: LabelType) -> dict[str, float]:
    """Fraction of rows in each class, keyed by role (train/validation/test).

    Only meaningful for :attr:`LabelType.BINARY_DIRECTION`; for continuous
    label types (future_return, volatility) this returns an empty mapping —
    "class balance" does not apply to a continuous target.
    """
    if label_type is not LabelType.BINARY_DIRECTION:
        return {}

    balance: dict[str, float] = {}
    for role in SplitRole:
        role_rows = [r for r in rows if r.get("split_role") == role.value]
        if not role_rows:
            continue
        positive = sum(1 for r in role_rows if row_value_as_float(r["label"]) == 1.0)
        balance[f"{role.value}_positive_fraction"] = positive / len(role_rows)
    return balance


def row_value_as_float(value: object) -> float:
    """Narrow an ``object``-typed row value to ``float`` for mypy's sake.

    Row dicts are ``dict[str, object]`` because a persisted row mixes str
    (symbol, split_role), int (fold), datetime (event_time), and float
    (label, feature values) columns — this helper documents the one place
    that heterogeneity is resolved back to a concrete type. Shared by
    ``domain/quality.py`` and ``domain/services.py`` so both narrow row
    values identically.
    """
    assert isinstance(value, int | float)
    return float(value)


def compute_feature_statistics(
    raw_frame: pd.DataFrame, feature_names: tuple[str, ...]
) -> tuple[FeatureStatistics, ...]:
    """Descriptive statistics per feature, over the raw (pre-clean) joined frame."""
    stats: list[FeatureStatistics] = []
    for feature_name in feature_names:
        if feature_name not in raw_frame.columns:
            stats.append(
                FeatureStatistics(
                    feature_name=feature_name,
                    count=0,
                    missing_count=len(raw_frame),
                    mean=None,
                    std=None,
                    minimum=None,
                    maximum=None,
                )
            )
            continue
        column = raw_frame[feature_name]
        non_null = column.dropna()
        stats.append(
            FeatureStatistics(
                feature_name=feature_name,
                count=int(non_null.count()),
                missing_count=int(column.isna().sum()),
                mean=float(non_null.mean()) if len(non_null) else None,
                std=float(non_null.std(ddof=0)) if len(non_null) else None,
                minimum=float(non_null.min()) if len(non_null) else None,
                maximum=float(non_null.max()) if len(non_null) else None,
            )
        )
    return tuple(stats)


def validate_quality(
    missing_value_counts: dict[str, int],
    total_rows_before_cleaning: int,
    feature_statistics: tuple[FeatureStatistics, ...],
) -> list[str]:
    """Basic data-quality validation findings (non-blocking; informational)."""
    findings: list[str] = []

    for stat in feature_statistics:
        if total_rows_before_cleaning == 0:
            continue
        missing_fraction = stat.missing_count / total_rows_before_cleaning
        if missing_fraction > _MAX_ACCEPTABLE_MISSING_FRACTION:
            findings.append(
                f"feature '{stat.feature_name}' is missing in "
                f"{missing_fraction:.1%} of rows over the requested date range "
                f"(threshold: {_MAX_ACCEPTABLE_MISSING_FRACTION:.0%})"
            )
        if stat.count > 0 and stat.std == 0.0:
            findings.append(
                f"feature '{stat.feature_name}' has zero variance (constant value) "
                f"over the requested date range"
            )

    return findings


def build_quality_report(
    raw_frame: pd.DataFrame,
    rows: list[dict[str, object]],
    feature_names: tuple[str, ...],
    label_type: LabelType,
) -> DatasetQualityReport:
    """Compute the full :class:`DatasetQualityReport` for one build run."""
    missing_value_counts = compute_missing_value_counts(raw_frame)
    duplicate_row_count = compute_duplicate_row_count(rows)
    class_balance = compute_class_balance(rows, label_type)
    feature_statistics = compute_feature_statistics(raw_frame, feature_names)
    findings = validate_quality(missing_value_counts, len(raw_frame), feature_statistics)

    return DatasetQualityReport(
        total_rows=len(rows),
        duplicate_row_count=duplicate_row_count,
        missing_value_counts=missing_value_counts,
        class_balance=class_balance,
        feature_statistics=feature_statistics,
        validation_passed=not findings,
        validation_findings=findings,
    )
