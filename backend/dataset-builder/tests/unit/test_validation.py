"""Unit tests for the automated leakage audit (pure domain logic, no I/O)."""

from __future__ import annotations

from aqros_dataset_builder.domain.models import SplitRole, SplitStrategy
from aqros_dataset_builder.domain.validation import (
    audit_finite_values,
    audit_fold_integrity,
    audit_label_horizon,
    run_leakage_audit,
)


def test_audit_fold_integrity_passes_for_correctly_ordered_walk_forward_fold() -> None:
    folds = [{0: SplitRole.TRAIN, 1: SplitRole.TRAIN, 2: SplitRole.VALIDATION, 3: SplitRole.TEST}]
    findings = audit_fold_integrity(folds, SplitStrategy.WALK_FORWARD)
    assert findings == []


def test_audit_fold_integrity_flags_train_after_validation() -> None:
    # Deliberately corrupted: train index 5 comes after validation index 2.
    folds = [{2: SplitRole.VALIDATION, 5: SplitRole.TRAIN, 6: SplitRole.TEST}]
    findings = audit_fold_integrity(folds, SplitStrategy.WALK_FORWARD)
    assert len(findings) == 1
    assert "train" in findings[0].lower()


def test_audit_fold_integrity_flags_validation_after_test() -> None:
    # Validation index 7 comes after test index 5 -> a violation.
    folds = [{0: SplitRole.TRAIN, 5: SplitRole.TEST, 7: SplitRole.VALIDATION}]
    findings = audit_fold_integrity(folds, SplitStrategy.ROLLING_WINDOW)
    assert len(findings) == 1
    assert "validation" in findings[0].lower()


def test_audit_fold_integrity_flags_train_overlapping_test_without_validation() -> None:
    folds = [{5: SplitRole.TRAIN, 3: SplitRole.TEST}]
    findings = audit_fold_integrity(folds, SplitStrategy.EXPANDING_WINDOW)
    assert len(findings) == 1
    assert "train" in findings[0].lower()


def test_audit_fold_integrity_passes_for_correctly_purged_cv_fold() -> None:
    folds = [{0: SplitRole.TRAIN, 1: SplitRole.TRAIN, 5: SplitRole.TEST, 6: SplitRole.TEST}]
    findings = audit_fold_integrity(folds, SplitStrategy.PURGED_CV)
    assert findings == []


def test_audit_fold_integrity_flags_purged_cv_train_leaking_into_test_span() -> None:
    # Train index 5 sits inside the test block's span [4, 6] -> a leak.
    folds = [{5: SplitRole.TRAIN, 4: SplitRole.TEST, 6: SplitRole.TEST}]
    findings = audit_fold_integrity(folds, SplitStrategy.PURGED_CV)
    assert len(findings) == 1
    assert "purged" in findings[0].lower()


def test_audit_finite_values_passes_for_all_finite() -> None:
    findings = audit_finite_values([1.0, 2.0, -3.5, 0.0], "features")
    assert findings == []


def test_audit_finite_values_flags_nan_and_inf() -> None:
    findings = audit_finite_values([1.0, float("nan"), float("inf"), 2.0], "label")
    assert len(findings) == 1
    assert "2" in findings[0]  # two non-finite values found
    assert "label" in findings[0]


def test_audit_label_horizon_passes_when_within_range() -> None:
    findings = audit_label_horizon(
        label_event_time_index=10, last_available_index=20, horizon_bars=5, symbol="AAPL"
    )
    assert findings == []


def test_audit_label_horizon_flags_when_beyond_known_history() -> None:
    findings = audit_label_horizon(
        label_event_time_index=18, last_available_index=20, horizon_bars=5, symbol="AAPL"
    )
    assert len(findings) == 1
    assert "AAPL" in findings[0]


def test_run_leakage_audit_passes_clean_input() -> None:
    folds = [{0: SplitRole.TRAIN, 1: SplitRole.VALIDATION, 2: SplitRole.TEST}]
    result = run_leakage_audit(
        folds=folds,
        split_strategy=SplitStrategy.WALK_FORWARD,
        feature_values=[1.0, 2.0, 3.0],
        label_values=[0.0, 1.0],
    )
    assert result.passed is True
    assert result.findings == []


def test_run_leakage_audit_fails_on_any_violation() -> None:
    folds = [{5: SplitRole.TRAIN, 2: SplitRole.VALIDATION}]  # train after validation
    result = run_leakage_audit(
        folds=folds,
        split_strategy=SplitStrategy.WALK_FORWARD,
        feature_values=[1.0, float("nan")],
        label_values=[0.0],
    )
    assert result.passed is False
    assert len(result.findings) == 2  # fold-integrity + finite-value finding
