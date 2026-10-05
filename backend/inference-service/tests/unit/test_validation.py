"""Unit tests for FeatureValidator (pure domain validation logic)."""

from __future__ import annotations

import math

import pytest
from aqros_inference_service.domain.validation import FeatureValidator


@pytest.fixture
def validator() -> FeatureValidator:
    return FeatureValidator()


class TestValidateSymbol:
    def test_valid_symbol(self, validator: FeatureValidator) -> None:
        errors = validator.validate_symbol("AAPL")
        assert errors == []

    def test_empty_symbol(self, validator: FeatureValidator) -> None:
        errors = validator.validate_symbol("")
        assert len(errors) == 1
        assert "empty" in errors[0].message.lower()

    def test_whitespace_symbol(self, validator: FeatureValidator) -> None:
        errors = validator.validate_symbol("   ")
        assert len(errors) == 1

    def test_too_long_symbol(self, validator: FeatureValidator) -> None:
        errors = validator.validate_symbol("A" * 21)
        assert len(errors) == 1
        assert "exceeds max length" in errors[0].message


class TestValidateFeaturesNotEmpty:
    def test_valid_features(self, validator: FeatureValidator) -> None:
        errors = validator.validate_features_not_empty({"sma_20": 42.5})
        assert errors == []

    def test_none_features(self, validator: FeatureValidator) -> None:
        errors = validator.validate_features_not_empty(None)
        assert len(errors) == 1

    def test_empty_dict(self, validator: FeatureValidator) -> None:
        errors = validator.validate_features_not_empty({})
        assert len(errors) == 1


class TestValidateNoNanInf:
    def test_no_nan_or_inf(self, validator: FeatureValidator) -> None:
        errors = validator.validate_no_nan_inf({"sma_20": 42.5, "rsi_14": 65.0})
        assert errors == []

    def test_nan_value(self, validator: FeatureValidator) -> None:
        errors = validator.validate_no_nan_inf({"feature": math.nan})
        assert len(errors) == 1
        assert "NaN" in errors[0].message

    def test_inf_value(self, validator: FeatureValidator) -> None:
        errors = validator.validate_no_nan_inf({"feature": math.inf})
        assert len(errors) == 1
        assert "infinite" in errors[0].message

    def test_neg_inf_value(self, validator: FeatureValidator) -> None:
        errors = validator.validate_no_nan_inf({"feature": -math.inf})
        assert len(errors) == 1

    def test_string_value_skipped(self, validator: FeatureValidator) -> None:
        errors = validator.validate_no_nan_inf({"sector": "tech"})
        assert errors == []


class TestValidateDtype:
    def test_no_expected_dtypes_all_valid(self, validator: FeatureValidator) -> None:
        errors = validator.validate_dtype({"sma_20": 42.5, "count": 5})
        assert errors == []

    def test_expected_dtypes_pass(self, validator: FeatureValidator) -> None:
        errors = validator.validate_dtype(
            {"sma_20": 42.5, "sector": "tech"},
            expected_dtypes={"sma_20": float, "sector": str},
        )
        assert errors == []

    def test_expected_dtypes_fail(self, validator: FeatureValidator) -> None:
        errors = validator.validate_dtype(
            {"sma_20": "not_a_float"},
            expected_dtypes={"sma_20": float},
        )
        assert len(errors) == 1

    def test_none_value(self, validator: FeatureValidator) -> None:
        errors = validator.validate_dtype({"feature": None})
        assert len(errors) == 1
        assert "None" in errors[0].message


class TestValidateFeatureCount:
    def test_expected_count_matches(self, validator: FeatureValidator) -> None:
        errors = validator.validate_feature_count({"a": 1, "b": 2, "c": 3}, expected_count=3)
        assert errors == []

    def test_expected_count_mismatch(self, validator: FeatureValidator) -> None:
        errors = validator.validate_feature_count({"a": 1, "b": 2}, expected_count=3)
        assert len(errors) == 1

    def test_below_min_count(self, validator: FeatureValidator) -> None:
        errors = validator.validate_feature_count({}, min_count=1)
        assert len(errors) == 1

    def test_above_min_count(self, validator: FeatureValidator) -> None:
        errors = validator.validate_feature_count({"a": 1, "b": 2}, min_count=1)
        assert errors == []


class TestValidateFeatureNames:
    def test_all_expected_present(self, validator: FeatureValidator) -> None:
        errors = validator.validate_feature_names(
            {"sma_20": 1.0, "rsi_14": 2.0},
            expected_names={"sma_20", "rsi_14"},
        )
        assert errors == []

    def test_missing_feature(self, validator: FeatureValidator) -> None:
        errors = validator.validate_feature_names(
            {"sma_20": 1.0},
            expected_names={"sma_20", "rsi_14"},
        )
        assert len(errors) == 1
        assert "Missing" in errors[0].message

    def test_extra_feature(self, validator: FeatureValidator) -> None:
        errors = validator.validate_feature_names(
            {"sma_20": 1.0, "extra": 3.0},
            expected_names={"sma_20"},
        )
        assert len(errors) == 1
        assert "Unexpected" in errors[0].message


class TestValidateAll:
    def test_all_checks_pass(self, validator: FeatureValidator) -> None:
        errors = validator.validate_all(
            "AAPL",
            {"sma_20": 42.5, "rsi_14": 65.0},
        )
        assert errors == []

    def test_invalid_symbol(self, validator: FeatureValidator) -> None:
        errors = validator.validate_all("", {"sma_20": 42.5})
        assert len(errors) >= 1

    def test_none_features(self, validator: FeatureValidator) -> None:
        errors = validator.validate_all("AAPL", None)
        assert errors == []

    def test_nan_features(self, validator: FeatureValidator) -> None:
        errors = validator.validate_all("AAPL", {"feat": math.nan})
        assert len(errors) == 1
        assert "NaN" in errors[0].message
