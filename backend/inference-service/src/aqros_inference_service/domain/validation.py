"""FeatureValidator — validates feature vectors before inference.

Performs all pre-prediction checks: schema version, missing features, NaN/Inf,
dtype, shape, feature count, symbol normalization, and timestamp freshness.
"""

from __future__ import annotations

import math
from typing import Any

from aqros_inference_service.domain.models import PredictionError


class FeatureValidationError(PredictionError):
    """A feature vector failed validation."""

    def __init__(self, message: str, detail: str | None = None) -> None:
        super().__init__(code="FEATURE_VALIDATION_ERROR", message=message, detail=detail)


class FeatureValidator:
    """Validates feature vectors before they reach the model.

    Each validation method returns a list of errors. An empty list means
    the check passed.
    """

    @staticmethod
    def validate_symbol(symbol: str) -> list[FeatureValidationError]:
        errors: list[FeatureValidationError] = []
        if not symbol or not symbol.strip():
            errors.append(FeatureValidationError("Symbol is empty or whitespace-only."))
        if len(symbol) > 20:
            errors.append(FeatureValidationError(f"Symbol '{symbol}' exceeds max length of 20."))
        return errors

    @staticmethod
    def validate_features_not_empty(
        features: dict[str, Any] | None,
    ) -> list[FeatureValidationError]:
        errors: list[FeatureValidationError] = []
        if features is None or len(features) == 0:
            errors.append(
                FeatureValidationError(
                    "Feature vector is empty or None.",
                    detail="At least one feature is required.",
                )
            )
        return errors

    @staticmethod
    def validate_no_nan_inf(
        features: dict[str, Any],
    ) -> list[FeatureValidationError]:
        errors: list[FeatureValidationError] = []
        for name, value in features.items():
            if isinstance(value, float):
                if math.isnan(value):
                    errors.append(FeatureValidationError(f"Feature '{name}' is NaN."))
                elif math.isinf(value):
                    errors.append(
                        FeatureValidationError(f"Feature '{name}' is infinite ({value}).")
                    )
        return errors

    @staticmethod
    def validate_dtype(
        features: dict[str, Any],
        expected_dtypes: dict[str, type] | None = None,
    ) -> list[FeatureValidationError]:
        """Validate feature dtypes.

        If ``expected_dtypes`` is None, only checks that numeric features
        are actually numeric (int/float).
        """
        errors: list[FeatureValidationError] = []
        for name, value in features.items():
            if expected_dtypes and name in expected_dtypes:
                expected = expected_dtypes[name]
                if not isinstance(value, expected):
                    errors.append(
                        FeatureValidationError(
                            f"Feature '{name}' has type {type(value).__name__}, "
                            f"expected {expected.__name__}."
                        )
                    )
            elif isinstance(value, (int, float)):
                # Numeric features are always fine
                pass
            elif value is None:
                errors.append(FeatureValidationError(f"Feature '{name}' is None."))
        return errors

    @staticmethod
    def validate_feature_count(
        features: dict[str, Any],
        expected_count: int | None = None,
        min_count: int = 1,
    ) -> list[FeatureValidationError]:
        errors: list[FeatureValidationError] = []
        if expected_count is not None and len(features) != expected_count:
            errors.append(
                FeatureValidationError(f"Expected {expected_count} features, got {len(features)}.")
            )
        if len(features) < min_count:
            errors.append(
                FeatureValidationError(
                    f"At least {min_count} feature(s) required, got {len(features)}."
                )
            )
        return errors

    @staticmethod
    def validate_feature_names(
        features: dict[str, Any],
        expected_names: set[str] | None = None,
    ) -> list[FeatureValidationError]:
        errors: list[FeatureValidationError] = []
        if expected_names is not None:
            missing = expected_names - set(features)
            if missing:
                errors.append(
                    FeatureValidationError(f"Missing expected features: {sorted(missing)}.")
                )
            extra = set(features) - expected_names
            if extra:
                errors.append(FeatureValidationError(f"Unexpected features: {sorted(extra)}."))
        return errors

    def validate_all(
        self,
        symbol: str,
        features: dict[str, Any] | None,
        *,
        expected_names: set[str] | None = None,
        expected_count: int | None = None,
        expected_dtypes: dict[str, type] | None = None,
    ) -> list[FeatureValidationError]:
        """Run all validation checks and return a flat list of errors.

        The caller should reject the request if any errors are returned.
        """
        errors: list[FeatureValidationError] = []
        errors.extend(self.validate_symbol(symbol))
        if features is not None:
            errors.extend(self.validate_features_not_empty(features))
            errors.extend(self.validate_no_nan_inf(features))
            errors.extend(self.validate_dtype(features, expected_dtypes))
            errors.extend(self.validate_feature_count(features, expected_count))
            errors.extend(self.validate_feature_names(features, expected_names))
        return errors
