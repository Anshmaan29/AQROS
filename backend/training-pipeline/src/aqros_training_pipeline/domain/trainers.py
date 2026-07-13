"""The Model_Trainer — per-``ModelType`` estimator dispatch and per-fold fitting.

Pure domain logic — no I/O, no persistence, no evaluation. This module owns
exactly three responsibilities (design.md Section 3, Key Design Decision 5):

1. **Estimator dispatch** (``ESTIMATOR_CLASSES``) — mapping each ``ModelType``
   to its scikit-learn-compatible estimator class: ``LogisticRegression``
   (Requirement 7.2), ``RandomForestClassifier`` (Requirement 7.3),
   ``XGBClassifier`` (Requirement 7.4), and ``LGBMClassifier``
   (Requirement 7.5). All four expose the same ``fit``/``predict``/
   ``predict_proba`` scikit-learn-compatible interface, so there is no
   model-type branching anywhere above this dispatch table.
2. **Hyperparameter defaults** (``HYPERPARAMETER_DEFAULTS``) — the exact
   table from design.md Key Design Decision 5, used whenever a
   ``Training_Request`` omits hyperparameters for a requested
   ``ModelType``, and ``merge_hyperparameters`` — which applies any
   caller-supplied overrides on top of those defaults (unspecified keys
   fall back to the default; specified keys are replaced verbatim).
3. **Per-fold fitting** (``fit_per_fold``) — fits one freshly-constructed
   estimator per fold, using *only* that fold's ``train``-role rows
   (Requirement 5.5) — never a fold's ``test`` or ``validation`` rows, and
   never rows from another fold. Feature columns are always selected and
   ordered exactly as the caller's ``feature_names`` sequence (itself
   sourced from ``DatasetManifest.feature_names``), never the DataFrame's
   own incidental column order — this is what guarantees the
   ``Feature_Importance_Extractor`` (Requirement 10.3) can later ``zip``
   the fitted model's coefficient/importance array against
   ``feature_names`` positionally and get a correct pairing.

**How the label column is identified:** the downloaded ``Dataset_Artifact``
DataFrame has a fixed, non-feature column named literally ``"label"``
(design.md Section 4's "departure" note: "``symbol``/``event_time``/
``fold``/``split_role``/``label`` columns plus whatever feature columns
``feature_names`` names"; the Glossary entry for ``Dataset_Artifact``
states the same fixed column set). ``DatasetManifest.label_type`` (e.g.
``"binary_direction"``) and ``label_definition`` describe *how* that
column's values were derived by the Dataset Builder — they are metadata
about the label, not the name of a different column to look up. This
module therefore reads the target values from the constant
``LABEL_COLUMN = "label"`` rather than from any manifest field.
"""

from __future__ import annotations

from typing import Protocol, cast

import pandas as pd
from lightgbm import LGBMClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from xgboost import XGBClassifier

from aqros_training_pipeline.domain.models import FoldFrames, ModelType

LABEL_COLUMN = "label"
"""The fixed name of the target column in every downloaded ``Dataset_Artifact``.

Not derived from ``DatasetManifest.label_type``/``label_definition`` — those
fields describe the label's semantics, not its column name. The Dataset
Builder's own artifact schema (design.md Section 4, the Glossary's
``Dataset_Artifact`` entry) fixes this column's name as ``"label"``
regardless of ``label_type``.
"""


class FittedEstimator(Protocol):
    """The scikit-learn-compatible subset of methods every fitted estimator exposes.

    All four supported estimator classes (``LogisticRegression``,
    ``RandomForestClassifier``, ``XGBClassifier``, ``LGBMClassifier``)
    implement this shape, which is what lets ``Evaluation_Engine`` and
    ``Feature_Importance_Extractor`` operate on any of them with zero
    model-type branching (design.md Key Design Decision 4).
    """

    def fit(self, features: pd.DataFrame, labels: pd.Series[int]) -> object: ...

    def predict(self, features: pd.DataFrame) -> object: ...

    def predict_proba(self, features: pd.DataFrame) -> object: ...


ESTIMATOR_CLASSES: dict[ModelType, type[object]] = {
    ModelType.LOGISTIC_REGRESSION: LogisticRegression,
    ModelType.RANDOM_FOREST: RandomForestClassifier,
    ModelType.XGBOOST: XGBClassifier,
    ModelType.LIGHTGBM: LGBMClassifier,
}
"""Dispatch table mapping each ``ModelType`` to its estimator class (Requirements 7.2-7.5).

Kept as a plain dict, not a ``match``/``if`` chain, so every other module in
this codebase that needs "the estimator class for this ``ModelType``"
(chiefly ``Feature_Importance_Extractor``, for its own model-type-specific
importance-attribute lookup) shares this single source of truth rather than
re-declaring the mapping.
"""

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
"""The platform-chosen hyperparameter defaults table (design.md Key Design Decision 5).

Applied whenever a ``Training_Request`` omits hyperparameters for a
requested ``ModelType`` — Requirement 7 never makes hyperparameters
mandatory, only the ``ModelType`` itself.
"""


def merge_hyperparameters(model_type: ModelType, overrides: dict[str, object]) -> dict[str, object]:
    """Apply caller-supplied ``overrides`` on top of ``model_type``'s defaults.

    Any key present in ``overrides`` replaces the corresponding default
    value verbatim; any default key absent from ``overrides`` is kept
    unchanged. Returns a fresh dict — ``HYPERPARAMETER_DEFAULTS`` is never
    mutated.
    """
    merged = dict(HYPERPARAMETER_DEFAULTS[model_type])
    merged.update(overrides)
    return merged


def _instantiate(model_type: ModelType, hyperparameters: dict[str, object]) -> FittedEstimator:
    """Construct a fresh, unfitted estimator instance for ``model_type``.

    A new instance is built for every fold (``fit_per_fold`` calls this once
    per fold) so that one fold's fitted state can never leak into another
    fold's estimator.
    """
    estimator_class = ESTIMATOR_CLASSES[model_type]
    return cast(FittedEstimator, estimator_class(**hyperparameters))


def fit_per_fold(
    model_type: ModelType,
    folds: dict[int, FoldFrames],
    hyperparameters: dict[str, object],
    feature_names: tuple[str, ...],
) -> dict[int, FittedEstimator]:
    """Fit one fresh estimator of ``model_type`` per fold, using only that fold's ``train`` rows.

    For every fold in ``folds``, this selects ``feature_names`` from that
    fold's ``FoldFrames.train`` DataFrame — never ``FoldFrames.test`` and
    never another fold's rows (Requirement 5.5) — in exactly the order
    ``feature_names`` lists them, and fits a new estimator instance against
    those feature columns and the ``LABEL_COLUMN`` target column.

    ``hyperparameters`` is the caller-supplied override dict (may be empty);
    it is merged over ``HYPERPARAMETER_DEFAULTS[model_type]`` via
    ``merge_hyperparameters`` once, up front, and the same merged
    hyperparameter set is used to construct every fold's estimator.

    Returns a mapping from fold id to that fold's fitted estimator. Raises
    whatever the underlying estimator's ``fit`` raises (e.g. on degenerate
    single-class training data) — this function does not swallow or
    translate fitting errors; the caller (``TrainingPipelineService``)
    is responsible for recording a failed ``ModelTypeOutcome`` when that
    happens (design.md Key Design Decision 8).
    """
    merged_hyperparameters = merge_hyperparameters(model_type, hyperparameters)
    fitted_per_fold: dict[int, FittedEstimator] = {}
    for fold_id, fold_frames in folds.items():
        train_frame = fold_frames.train
        features = train_frame.loc[:, list(feature_names)]
        labels = cast("pd.Series[int]", train_frame[LABEL_COLUMN])
        estimator = _instantiate(model_type, merged_hyperparameters)
        estimator.fit(features, labels)
        fitted_per_fold[fold_id] = estimator
    return fitted_per_fold
