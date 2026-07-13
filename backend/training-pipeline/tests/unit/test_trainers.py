"""Unit + property tests for the Model_Trainer (task 8.4)."""

from __future__ import annotations

from lightgbm import LGBMClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from xgboost import XGBClassifier

from aqros_training_pipeline.domain import partitioning, trainers
from aqros_training_pipeline.domain.models import ModelType
from tests.unit.builders import FEATURE_NAMES, make_dataframe


def test_estimator_dispatch_logistic_regression() -> None:
    assert trainers.ESTIMATOR_CLASSES[ModelType.LOGISTIC_REGRESSION] is LogisticRegression


def test_estimator_dispatch_random_forest() -> None:
    assert trainers.ESTIMATOR_CLASSES[ModelType.RANDOM_FOREST] is RandomForestClassifier


def test_estimator_dispatch_xgboost() -> None:
    assert trainers.ESTIMATOR_CLASSES[ModelType.XGBOOST] is XGBClassifier


def test_estimator_dispatch_lightgbm() -> None:
    assert trainers.ESTIMATOR_CLASSES[ModelType.LIGHTGBM] is LGBMClassifier


def test_defaults_applied_when_no_overrides() -> None:
    merged = trainers.merge_hyperparameters(ModelType.RANDOM_FOREST, {})
    assert merged["n_estimators"] == 200
    assert merged["random_state"] == 42


def test_overrides_replace_defaults() -> None:
    merged = trainers.merge_hyperparameters(ModelType.RANDOM_FOREST, {"n_estimators": 300})
    assert merged["n_estimators"] == 300
    # Untouched defaults survive.
    assert merged["random_state"] == 42
    # Defaults table itself is not mutated.
    assert trainers.HYPERPARAMETER_DEFAULTS[ModelType.RANDOM_FOREST]["n_estimators"] == 200


def test_fit_per_fold_produces_one_estimator_per_fold() -> None:
    df = make_dataframe(n_folds=3, rows_per_role=10)
    folds = partitioning.partition(df)
    fitted = trainers.fit_per_fold(ModelType.LOGISTIC_REGRESSION, folds, {}, FEATURE_NAMES)
    assert set(fitted) == set(folds)
    for estimator in fitted.values():
        assert isinstance(estimator, LogisticRegression)
