"""Property tests for the Evaluation_Engine (task 8.5)."""

from __future__ import annotations

import statistics

import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)

from aqros_training_pipeline.domain import evaluation
from aqros_training_pipeline.domain.models import (
    ConfusionMatrix,
    PerFoldMetrics,
)

_labels = st.lists(st.integers(min_value=0, max_value=1), min_size=4, max_size=40)


# Feature: training-pipeline, Property 13: per-fold metric computations match
# scikit-learn reference formulas.
@settings(max_examples=100)
@given(data=st.data())
def test_property_13_matches_sklearn_reference(data: st.DataObject) -> None:
    n = data.draw(st.integers(min_value=4, max_value=40))
    y_true = np.array(data.draw(st.lists(st.integers(0, 1), min_size=n, max_size=n)))
    y_pred = np.array(data.draw(st.lists(st.integers(0, 1), min_size=n, max_size=n)))
    y_proba = np.array(data.draw(st.lists(st.floats(0.0, 1.0), min_size=n, max_size=n)))
    metrics = evaluation.evaluate_fold(0, y_true, y_pred, y_proba)
    assert metrics.accuracy == accuracy_score(y_true, y_pred)
    assert metrics.precision == precision_score(y_true, y_pred, zero_division=0)
    assert metrics.recall == recall_score(y_true, y_pred, zero_division=0)
    assert metrics.f1_score == f1_score(y_true, y_pred, zero_division=0)


# Feature: training-pipeline, Property 14: single-class fold ROC AUC is undefined
# and excluded from aggregation.
@settings(max_examples=100)
@given(constant_label=st.integers(min_value=0, max_value=1))
def test_property_14_single_class_roc_auc_none(constant_label: int) -> None:
    y_true = np.array([constant_label] * 8)
    y_pred = np.array([constant_label] * 8)
    y_proba = np.linspace(0.1, 0.9, 8)
    metrics = evaluation.evaluate_fold(0, y_true, y_pred, y_proba)
    assert metrics.roc_auc is None


def test_roc_auc_computed_for_two_class_fold() -> None:
    y_true = np.array([0, 1, 0, 1])
    y_proba = np.array([0.2, 0.8, 0.3, 0.9])
    metrics = evaluation.evaluate_fold(0, y_true, (y_proba > 0.5).astype(int), y_proba)
    assert metrics.roc_auc == roc_auc_score(y_true, y_proba)


def _fold(fold: int, acc: float, roc: float | None) -> PerFoldMetrics:
    return PerFoldMetrics(
        fold=fold,
        accuracy=acc,
        precision=acc,
        recall=acc,
        f1_score=acc,
        roc_auc=roc,
        confusion_matrix=ConfusionMatrix(1, 1, 1, 1),
        test_row_count=4,
    )


# Feature: training-pipeline, Property 8: aggregated mean/std equal the statistical
# mean/population-std of the per-fold values.
@settings(max_examples=100)
@given(values=st.lists(st.floats(0.0, 1.0), min_size=1, max_size=8))
def test_property_8_aggregation_mean_std(values: list[float]) -> None:
    per_fold = [_fold(i, v, v) for i, v in enumerate(values)]
    aggregated = evaluation.aggregate(per_fold)
    assert aggregated.accuracy_mean == statistics.fmean(values)
    assert aggregated.accuracy_std == statistics.pstdev(values)
    assert aggregated.evaluated_fold_count == len(values)


# Feature: training-pipeline, Property 14 (aggregation side): ROC AUC aggregated
# only across folds whose roc_auc is defined.
@settings(max_examples=100)
@given(
    defined=st.lists(st.floats(0.0, 1.0), min_size=0, max_size=5),
    none_count=st.integers(min_value=0, max_value=4),
)
def test_roc_auc_aggregation_excludes_none(defined: list[float], none_count: int) -> None:
    per_fold = [_fold(i, 0.5, v) for i, v in enumerate(defined)]
    per_fold += [_fold(100 + i, 0.5, None) for i in range(none_count)]
    if not per_fold:
        return
    aggregated = evaluation.aggregate(per_fold)
    assert aggregated.roc_auc_evaluated_fold_count == len(defined)
    if defined:
        assert aggregated.roc_auc_mean == statistics.fmean(defined)
    else:
        assert aggregated.roc_auc_mean is None
        assert aggregated.roc_auc_std is None


# Feature: training-pipeline, Property 7: per-fold evaluation is isolated to that
# fold's own rows (evaluate_fold consumes only the arrays it is given).
def test_property_7_evaluation_isolated() -> None:
    m1 = evaluation.evaluate_fold(0, np.array([0, 1]), np.array([0, 1]), np.array([0.1, 0.9]))
    m2 = evaluation.evaluate_fold(1, np.array([1, 1]), np.array([0, 1]), np.array([0.4, 0.6]))
    assert m1.fold == 0
    assert m2.fold == 1
    assert m1.test_row_count == 2
