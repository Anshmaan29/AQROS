"""Property tests for the Feature_Importance_Extractor (task 8.6)."""

from __future__ import annotations

import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st

from aqros_training_pipeline.domain import feature_importance, partitioning, trainers
from aqros_training_pipeline.domain.models import ModelType
from tests.unit.builders import FEATURE_NAMES, make_dataframe

_MODEL_TYPES = list(ModelType)


def _fit_one(model_type: ModelType):
    df = make_dataframe(n_folds=1, rows_per_role=12)
    folds = partitioning.partition(df)
    fitted = trainers.fit_per_fold(model_type, folds, {}, FEATURE_NAMES)
    return next(iter(fitted.values()))


# Feature: training-pipeline, Property 15: extracted importance equals the fitted
# model's own coefficient/importance array, in feature_names order.
@settings(max_examples=len(_MODEL_TYPES), deadline=None)
@given(model_type=st.sampled_from(_MODEL_TYPES))
def test_property_15_matches_fitted_values(model_type: ModelType) -> None:
    model = _fit_one(model_type)
    result = feature_importance.extract(model, model_type, FEATURE_NAMES)

    if model_type is ModelType.LOGISTIC_REGRESSION:
        expected = np.asarray(model.coef_[0])
    else:
        expected = np.asarray(model.feature_importances_)

    for name, exp in zip(FEATURE_NAMES, expected, strict=True):
        assert result[name] == float(exp)


# Feature: training-pipeline, Property 16: exactly one importance value per
# manifest feature_name.
@settings(max_examples=len(_MODEL_TYPES), deadline=None)
@given(model_type=st.sampled_from(_MODEL_TYPES))
def test_property_16_one_value_per_feature(model_type: ModelType) -> None:
    model = _fit_one(model_type)
    result = feature_importance.extract(model, model_type, FEATURE_NAMES)
    assert set(result) == set(FEATURE_NAMES)
    assert len(result) == len(FEATURE_NAMES)
