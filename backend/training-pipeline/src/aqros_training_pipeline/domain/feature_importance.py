"""The Feature_Importance_Extractor — one importance value per manifest feature.

Pure domain logic, no I/O. Extracts feature-importance values from a fitted
estimator: coefficients for ``logistic_regression`` (Requirement 10.1) and
impurity/gain-based ``feature_importances_`` for the three tree-ensemble
types (Requirement 10.2). Because ``Model_Trainer`` always fits with
columns ordered exactly as ``feature_names``, ``zip`` produces exactly one
entry per manifest feature name (Requirement 10.3).
"""

from __future__ import annotations

import numpy as np

from aqros_training_pipeline.domain.models import ModelType


def extract(
    fitted_model: object,
    model_type: ModelType,
    feature_names: tuple[str, ...],
) -> dict[str, float]:
    """Return ``{feature_name: importance}`` for ``fitted_model``.

    For ``logistic_regression`` the values are ``coef_[0]`` (the single
    coefficient row of a binary classifier); for ``random_forest``,
    ``xgboost``, and ``lightgbm`` they are ``feature_importances_``. The
    importance array is zipped positionally against ``feature_names`` — safe
    because the model was fit with columns in exactly that order — so the
    result always has exactly one entry per feature name (Requirement 10.3).
    """
    if model_type is ModelType.LOGISTIC_REGRESSION:
        coefficients = np.asarray(fitted_model.coef_)  # type: ignore[attr-defined]
        importance_array = coefficients[0]
    else:
        importance_array = np.asarray(
            fitted_model.feature_importances_  # type: ignore[attr-defined]
        )

    values = [float(v) for v in importance_array.tolist()]
    if len(values) != len(feature_names):
        raise ValueError(
            "feature-importance array length "
            f"({len(values)}) does not match feature_names length ({len(feature_names)})"
        )
    return dict(zip(feature_names, values, strict=True))
