"""The Evaluation_Engine — per-fold metric computation and cross-fold aggregation.

Pure domain logic, no I/O. Computes ``PerFoldMetrics`` for one fold's
``test``-role rows in isolation (Requirements 6.1, 6.3) and aggregates the
per-fold values into ``AggregatedMetrics`` (Requirements 6.2, 9.2). ROC AUC
is undefined for a single-class test fold and excluded from aggregation
(Requirement 9.3).
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)

from aqros_training_pipeline.domain.models import (
    AggregatedMetrics,
    ConfusionMatrix,
    PerFoldMetrics,
)


def evaluate_fold(
    fold: int,
    y_true: Sequence[int] | np.ndarray,
    y_pred: Sequence[int] | np.ndarray,
    y_proba: Sequence[float] | np.ndarray,
) -> PerFoldMetrics:
    """Compute one fold's metrics from that fold's ``test``-role rows only.

    ``accuracy``/``precision``/``recall``/``f1_score``/``confusion_matrix``
    are always computed (Requirement 9.1). ``roc_auc`` is computed from
    ``y_proba`` (the positive-class probability) unless the fold's test
    rows are single-class, in which case it is ``None`` (Requirement 9.3).
    """
    y_true_arr = np.asarray(y_true)
    y_pred_arr = np.asarray(y_pred)

    accuracy = float(accuracy_score(y_true_arr, y_pred_arr))
    precision = float(precision_score(y_true_arr, y_pred_arr, zero_division=0))
    recall = float(recall_score(y_true_arr, y_pred_arr, zero_division=0))
    f1 = float(f1_score(y_true_arr, y_pred_arr, zero_division=0))

    matrix = confusion_matrix(y_true_arr, y_pred_arr, labels=[0, 1])
    tn, fp, fn, tp = (int(v) for v in matrix.ravel())

    roc_auc: float | None
    if len(set(y_true_arr.tolist())) < 2:
        roc_auc = None
    else:
        roc_auc = float(roc_auc_score(y_true_arr, np.asarray(y_proba)))

    return PerFoldMetrics(
        fold=fold,
        accuracy=accuracy,
        precision=precision,
        recall=recall,
        f1_score=f1,
        roc_auc=roc_auc,
        confusion_matrix=ConfusionMatrix(
            true_negative=tn,
            false_positive=fp,
            false_negative=fn,
            true_positive=tp,
        ),
        test_row_count=int(y_true_arr.shape[0]),
    )


def _mean_std(values: Sequence[float]) -> tuple[float, float]:
    """Return ``(mean, population-std)`` of ``values`` (``statistics.fmean``/``pstdev``)."""
    return statistics.fmean(values), statistics.pstdev(values)


def aggregate(per_fold_metrics: Sequence[PerFoldMetrics]) -> AggregatedMetrics:
    """Aggregate per-fold metrics into mean/std across folds (Requirements 6.2, 9.2).

    Accuracy/precision/recall/F1 mean+std are computed across every fold.
    ROC AUC mean+std are computed across only the folds whose ``roc_auc``
    is not ``None``; if every fold's ``roc_auc`` is ``None`` they are
    ``None`` and ``roc_auc_evaluated_fold_count`` is ``0`` (Requirement 9.3).
    """
    if not per_fold_metrics:
        raise ValueError("cannot aggregate an empty set of per-fold metrics")

    accuracy_mean, accuracy_std = _mean_std([m.accuracy for m in per_fold_metrics])
    precision_mean, precision_std = _mean_std([m.precision for m in per_fold_metrics])
    recall_mean, recall_std = _mean_std([m.recall for m in per_fold_metrics])
    f1_mean, f1_std = _mean_std([m.f1_score for m in per_fold_metrics])

    roc_values = [m.roc_auc for m in per_fold_metrics if m.roc_auc is not None]
    roc_auc_mean: float | None
    roc_auc_std: float | None
    if roc_values:
        roc_auc_mean, roc_auc_std = _mean_std(roc_values)
    else:
        roc_auc_mean, roc_auc_std = None, None

    return AggregatedMetrics(
        accuracy_mean=accuracy_mean,
        accuracy_std=accuracy_std,
        precision_mean=precision_mean,
        precision_std=precision_std,
        recall_mean=recall_mean,
        recall_std=recall_std,
        f1_mean=f1_mean,
        f1_std=f1_std,
        roc_auc_mean=roc_auc_mean,
        roc_auc_std=roc_auc_std,
        evaluated_fold_count=len(per_fold_metrics),
        roc_auc_evaluated_fold_count=len(roc_values),
    )
