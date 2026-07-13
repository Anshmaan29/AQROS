from __future__ import annotations

from typing import Protocol, cast

import pandas as pd
from lightgbm import LGBMClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from xgboost import XGBClassifier

from aqros_training_pipeline.domain.models import FoldFrames, ModelType

LABEL_COLUMN = "label"


class FittedClassifier(Protocol):
    def fit(self, X: pd.DataFrame, y: "pd.Series[object]") -> object: ...

    def predict(self, X: pd.DataFrame) -> object: ...

    def predict_proba(self, X: pd.DataFrame) -> object: ...


ESTIMATOR_CLASSES: dict[ModelType, type[object]] = {
    ModelType.LOGISTIC_REGRESSION: LogisticRegression,
    ModelType.RANDOM_FOREST: RandomForestClassifier,
    ModelType.XGBOOST: XGBClassifier,
    ModelType.LIGHTGBM: LGBMClassifier,
}

HYPERPARAMETER_DEFAULTS: dict[ModelType, dict[str, object]] = {
    ModelType.LOGISTIC_REGRESSION: {
        "penalty": "l2",
        "C": 1.0,
        "solver": "lbfgs",
        "max_iter": 1000,
    },
    ModelType.RANDOM_FOREST: {
        "n_estimators": 200,
        "max_depth": None,
        "random_state": 42,
    },
    ModelType.XGBOOST: {
        "n_estimators": 200,
        "max_depth": 6,
        "learning_rate": 0.1,
        "random_state": 42,
    },
    ModelType.LIGHTGBM: {
        "n_estimators": 200,
        "max_depth": -1,
        "learning_rate": 0.1,
        "random_state": 42,
    },
}


def merge_hyperparameters(
    model_type: ModelType, overrides: dict[str, object]
) -> dict[str, object]:
    merged = dict(HYPERPARAMETER_DEFAULTS[model_type])
    merged.update(overrides)
    return merged


def _instantiate(model_type: ModelType, hyperparameters: dict[str, object]) -> FittedClassifier:
    estimator_class = ESTIMATOR_CLASSES[model_type]
    return cast(FittedClassifier, estimator_class(**hyperparameters))


def fit_per_fold(
    model_type: ModelType,
    folds: dict[int, FoldFrames],
    hyperparameters: dict[str, object],
    feature_names: tuple[str, ...],
) -> dict[int, FittedClassifier]:
    merged = merge_hyperparameters(model_type, hyperparameters)
    fitted: dict[int, FittedClassifier] = {}
    for fold_id, fold_frames in folds.items():
        train_frame = fold_frames.train
        x_train = train_frame.loc[:, list(feature_names)]
        y_train = train_frame[LABEL_COLUMN]
        estimator = _instantiate(model_type, merged)
        estimator.fit(x_train, y_train)
        fitted[fold_id] = estimator
    return fitted
