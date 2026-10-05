"""ParityMonitor — pure domain logic for comparing offline and online features.

This module contains the comparison logic with no I/O. It takes offline and
online feature values and produces a ``FeatureComparisonResult`` for each.
All numeric tolerance, staleness, version, and type checking is computed here.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from aqros_parity_monitor.domain.models import (
    ComparisonStatus,
    FeatureComparisonResult,
    ParityReport,
)


class ParityMonitor:
    """Pure domain service for comparing offline and online feature values.

    This is a stateless service: every method receives its inputs and returns
    results. The caller (``ParityService``) is responsible for fetching data
    from the ports and persisting results.
    """

    def __init__(
        self,
        default_tolerance: float = 1e-6,
        max_feature_age_seconds: float = 300.0,
    ) -> None:
        self._default_tolerance = default_tolerance
        self._max_feature_age_seconds = max_feature_age_seconds

    @staticmethod
    def _is_numeric(value: Any) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    def compare_feature(
        self,
        feature_name: str,
        offline_value: Any | None,
        online_value: Any | None,
        *,
        tolerance: float | None = None,
        offline_version: int | None = None,
        online_version: int | None = None,
        online_timestamp: datetime | None = None,
    ) -> FeatureComparisonResult:
        """Compare a single feature across offline and online stores.

        Args:
            feature_name: The feature's name.
            offline_value: The value from the offline (Postgres) store.
            online_value: The value from the online (Redis) store.
            tolerance: Numeric tolerance for comparison (defaults to
                ``self._default_tolerance``).
            offline_version: Feature version from the offline store.
            online_version: Feature version from the online store.
            online_timestamp: When the online value was stored (used for
                staleness detection).

        Returns:
            A ``FeatureComparisonResult`` summarising the comparison.
        """
        effective_tolerance = tolerance if tolerance is not None else self._default_tolerance

        # --- Missing checks -----------------------------------------------
        if offline_value is None and online_value is None:
            return FeatureComparisonResult(
                feature_name=feature_name,
                offline_value=None,
                online_value=None,
                status=ComparisonStatus.MATCH,
                reason="Both stores have no value for this feature.",
                tolerance=effective_tolerance,
            )
        if offline_value is None:
            return FeatureComparisonResult(
                feature_name=feature_name,
                offline_value=None,
                online_value=online_value,
                status=ComparisonStatus.MISSING_OFFLINE,
                reason="Value present online but missing from offline store.",
                tolerance=effective_tolerance,
            )
        if online_value is None:
            return FeatureComparisonResult(
                feature_name=feature_name,
                offline_value=offline_value,
                online_value=None,
                status=ComparisonStatus.MISSING_ONLINE,
                reason="Value present offline but missing from online store.",
                tolerance=effective_tolerance,
            )

        # --- Version mismatch ---------------------------------------------
        if (
            offline_version is not None
            and online_version is not None
            and offline_version != online_version
        ):
            return FeatureComparisonResult(
                feature_name=feature_name,
                offline_value=offline_value,
                online_value=online_value,
                status=ComparisonStatus.VERSION_MISMATCH,
                reason=(f"Offline version {offline_version} != online version {online_version}"),
                tolerance=effective_tolerance,
                offline_version=offline_version,
                online_version=online_version,
            )

        # --- Staleness ----------------------------------------------------
        if online_timestamp is not None:
            age_seconds = (datetime.now(UTC) - online_timestamp).total_seconds()
            if age_seconds > self._max_feature_age_seconds:
                return FeatureComparisonResult(
                    feature_name=feature_name,
                    offline_value=offline_value,
                    online_value=online_value,
                    status=ComparisonStatus.STALE,
                    reason=(
                        f"Online value age {age_seconds:.1f}s exceeds max "
                        f"{self._max_feature_age_seconds}s"
                    ),
                    tolerance=effective_tolerance,
                    offline_version=offline_version,
                    online_version=online_version,
                )

        # --- Type mismatch ------------------------------------------------
        offline_is_num = self._is_numeric(offline_value)
        online_is_num = self._is_numeric(online_value)
        if offline_is_num != online_is_num or (
            not offline_is_num and type(offline_value) is not type(online_value)
        ):
            return FeatureComparisonResult(
                feature_name=feature_name,
                offline_value=offline_value,
                online_value=online_value,
                status=ComparisonStatus.TYPE_MISMATCH,
                reason=(
                    f"Type mismatch: offline={type(offline_value).__name__} "
                    f"online={type(online_value).__name__}"
                ),
                tolerance=effective_tolerance,
                offline_version=offline_version,
                online_version=online_version,
            )

        # --- Numeric comparison ------------------------------------------
        if offline_is_num and online_is_num:
            diff = abs(float(online_value) - float(offline_value))
            max_abs = max(abs(float(online_value)), abs(float(offline_value)))
            relative_diff = diff / max_abs if max_abs > effective_tolerance else 0.0

            if relative_diff <= effective_tolerance:
                return FeatureComparisonResult(
                    feature_name=feature_name,
                    offline_value=offline_value,
                    online_value=online_value,
                    status=ComparisonStatus.MATCH,
                    difference=diff,
                    tolerance=effective_tolerance,
                    offline_version=offline_version,
                    online_version=online_version,
                    reason="Values match within tolerance.",
                )
            return FeatureComparisonResult(
                feature_name=feature_name,
                offline_value=offline_value,
                online_value=online_value,
                status=ComparisonStatus.OUTSIDE_TOLERANCE,
                difference=diff,
                tolerance=effective_tolerance,
                offline_version=offline_version,
                online_version=online_version,
                reason=(
                    f"Relative difference {relative_diff:.2e} exceeds "
                    f"tolerance {effective_tolerance:.2e}"
                ),
            )

        # --- Non-numeric exact match --------------------------------------
        if offline_value == online_value:
            return FeatureComparisonResult(
                feature_name=feature_name,
                offline_value=offline_value,
                online_value=online_value,
                status=ComparisonStatus.MATCH,
                tolerance=effective_tolerance,
                offline_version=offline_version,
                online_version=online_version,
                reason="Non-numeric values match exactly.",
            )
        return FeatureComparisonResult(
            feature_name=feature_name,
            offline_value=offline_value,
            online_value=online_value,
            status=ComparisonStatus.OUTSIDE_TOLERANCE,
            tolerance=effective_tolerance,
            offline_version=offline_version,
            online_version=online_version,
            reason="Non-numeric values do not match.",
        )

    def compare_snapshot(
        self,
        symbol: str,
        offline_features: Mapping[str, Any],
        online_features: Mapping[str, Any],
        *,
        tolerance: float | None = None,
        offline_versions: Mapping[str, int] | None = None,
        online_versions: Mapping[str, int] | None = None,
        online_timestamps: Mapping[str, datetime] | None = None,
        correlation_id: str | None = None,
    ) -> ParityReport:
        """Compare all features for one symbol and produce a ``ParityReport``.

        Args:
            symbol: The instrument symbol.
            offline_features: Feature values from the offline store
                (``{name: value}``).
            online_features: Feature values from the online store
                (``{name: value}``).
            tolerance: Optional override for the default numeric tolerance.
            offline_versions: Feature versions from the offline store
                (``{name: version}``).
            online_versions: Feature versions from the online store
                (``{name: version}``).
            online_timestamps: Timestamps of the online values
                (``{name: datetime}``).
            correlation_id: Optional correlation ID for tracing.

        Returns:
            A populated ``ParityReport``.
        """
        all_feature_names = set(offline_features) | set(online_features)
        comparisons: list[FeatureComparisonResult] = []
        match_count = 0
        fail_count = 0
        missing_count = 0
        stale_count = 0
        version_mismatch_count = 0
        type_mismatch_count = 0
        failure_reasons: dict[str, str] = {}
        differences: list[float] = []

        for name in sorted(all_feature_names):
            result = self.compare_feature(
                feature_name=name,
                offline_value=offline_features.get(name),
                online_value=online_features.get(name),
                tolerance=tolerance,
                offline_version=(offline_versions.get(name) if offline_versions else None),
                online_version=(online_versions.get(name) if online_versions else None),
                online_timestamp=(online_timestamps.get(name) if online_timestamps else None),
            )
            comparisons.append(result)

            if result.passed:
                match_count += 1
            else:
                fail_count += 1
                failure_reasons[name] = result.reason

            match result.status:
                case ComparisonStatus.MISSING_OFFLINE | ComparisonStatus.MISSING_ONLINE:
                    missing_count += 1
                case ComparisonStatus.STALE:
                    stale_count += 1
                case ComparisonStatus.VERSION_MISMATCH:
                    version_mismatch_count += 1
                case ComparisonStatus.TYPE_MISMATCH:
                    type_mismatch_count += 1
                case _:
                    pass

            if result.difference is not None:
                differences.append(result.difference)

        max_diff = max(differences) if differences else None
        avg_diff = sum(differences) / len(differences) if differences else None

        return ParityReport(
            symbol=symbol.upper(),
            timestamp=datetime.now(UTC),
            correlation_id=correlation_id,
            feature_count=len(comparisons),
            matching_count=match_count,
            failed_count=fail_count,
            missing_count=missing_count,
            stale_count=stale_count,
            version_mismatch_count=version_mismatch_count,
            type_mismatch_count=type_mismatch_count,
            maximum_difference=max_diff,
            average_difference=avg_diff,
            comparisons=comparisons,
            failure_reasons=failure_reasons,
        )
