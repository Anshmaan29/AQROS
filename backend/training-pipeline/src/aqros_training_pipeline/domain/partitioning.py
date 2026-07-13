"""The Fold_Partitioner — reads the existing ``fold``/``split_role`` columns verbatim.

Pure, no I/O. This module never computes, generates, or assigns a new
train/validation/test split (Requirement 5.3): it strictly groups a
downloaded ``Dataset_Artifact`` DataFrame by the values already present in
its ``fold`` column (Requirement 5.2), and within each fold group, slices
rows by the values already present in its ``split_role`` column
(Requirement 5.1) into the ``train``/``test`` roles the rest of the
pipeline uses.

There is no sort, shuffle, or ``.sample()`` call anywhere in this module
(Requirement 5.4) — boolean masking (``frame[frame["split_role"] == ...]``)
and ``DataFrame.groupby`` preserve each row's original relative order and
identity; no row is duplicated, dropped, or reordered relative to its
original fold/split_role grouping.
"""

from __future__ import annotations

from typing import cast

import pandas as pd

from aqros_training_pipeline.domain.models import FoldFrames, SplitRole


class NoEvaluableFoldsError(RuntimeError):
    """Raised when every fold's ``test``-role slice is empty (Requirement 6.4).

    Covers both the zero-fold case (an empty or fold-less DataFrame) and
    the case where one or more folds exist but none of them has any row
    whose ``split_role`` equals ``test`` — either way, the
    ``Evaluation_Engine`` would have nothing to evaluate, so the
    ``Training_Request`` is rejected before any ``Model_Trainer`` call is
    made.
    """


def partition(dataframe: pd.DataFrame) -> dict[int, FoldFrames]:
    """Group ``dataframe`` by its existing ``fold`` column and split each group by ``split_role``.

    For every distinct value in the ``fold`` column, this produces one
    ``FoldFrames`` containing exactly that fold's ``train``-role rows and
    exactly that fold's ``test``-role rows (Requirements 5.1, 5.2) — the
    fold and role assignments are read verbatim from the DataFrame's own
    ``fold``/``split_role`` columns, never re-derived (Requirement 5.3).

    No sort, shuffle, or ``.sample()`` call is used anywhere in this
    function (Requirement 5.4): grouping and boolean masking both preserve
    each row's original relative order.

    Raises ``NoEvaluableFoldsError`` if, across every fold present in
    ``dataframe`` (including the case where ``dataframe`` has no rows or
    no folds at all), the ``test``-role slice is empty for all of them
    (Requirement 6.4) — this check happens before returning, so a caller
    never receives a partition result with zero evaluable folds.
    """
    folds: dict[int, FoldFrames] = {}
    for fold_value, fold_group in dataframe.groupby("fold", sort=False):
        fold_id = int(cast(int, fold_value))
        train_frame = fold_group[fold_group["split_role"] == SplitRole.TRAIN.value]
        test_frame = fold_group[fold_group["split_role"] == SplitRole.TEST.value]
        folds[fold_id] = FoldFrames(fold=fold_id, train=train_frame, test=test_frame)

    if all(fold_frames.test.empty for fold_frames in folds.values()):
        raise NoEvaluableFoldsError(
            "no evaluable folds found: every fold's test-role row slice is empty"
        )

    return folds
