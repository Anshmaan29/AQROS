"""Property tests for the Fold_Partitioner (task 8.3)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aqros_training_pipeline.domain import partitioning
from aqros_training_pipeline.domain.models import SplitRole

from .builders import make_dataframe


# Feature: training-pipeline, Property 4: fold and split-role assignment is read
# verbatim, never re-derived.
@settings(max_examples=100)
@given(n_folds=st.integers(min_value=1, max_value=4))
def test_property_4_fold_and_role_verbatim(n_folds: int) -> None:
    df = make_dataframe(n_folds=n_folds, rows_per_role=6)
    folds = partitioning.partition(df)
    assert set(folds) == set(range(n_folds))
    for fold_id, frames in folds.items():
        assert (frames.train["fold"] == fold_id).all()
        assert (frames.test["fold"] == fold_id).all()
        assert (frames.train["split_role"] == SplitRole.TRAIN.value).all()
        assert (frames.test["split_role"] == SplitRole.TEST.value).all()


# Feature: training-pipeline, Property 5: row set is preserved without shuffle,
# reorder, or resampling.
@settings(max_examples=100)
@given(seed=st.integers(min_value=0, max_value=10_000))
def test_property_5_no_shuffle_or_resample(seed: int) -> None:
    df = make_dataframe(n_folds=2, rows_per_role=8, seed=seed)
    folds = partitioning.partition(df)

    reconstructed = pd.concat(
        [frames.train for frames in folds.values()] + [frames.test for frames in folds.values()]
    )
    # Every original (index-identified) row appears exactly once, unchanged.
    assert len(reconstructed) == len(df)
    assert set(reconstructed.index) == set(df.index)
    for frames in folds.values():
        # Order within a slice matches the original relative order.
        assert list(frames.train.index) == sorted(frames.train.index)


# Feature: training-pipeline, Property 6: per-fold fitting uses exactly that fold's
# train rows (the partition exposes exactly those rows).
@settings(max_examples=100)
@given(n_folds=st.integers(min_value=2, max_value=4))
def test_property_6_train_rows_isolated_per_fold(n_folds: int) -> None:
    df = make_dataframe(n_folds=n_folds, rows_per_role=5)
    folds = partitioning.partition(df)
    for fold_id, frames in folds.items():
        expected = df[(df["fold"] == fold_id) & (df["split_role"] == "train")]
        assert set(frames.train.index) == set(expected.index)


# Feature: training-pipeline, Property 9: zero evaluable folds rejects the request.
@settings(max_examples=50)
@given(n_folds=st.integers(min_value=1, max_value=3))
def test_property_9_zero_evaluable_folds_rejected(n_folds: int) -> None:
    df = make_dataframe(n_folds=n_folds, rows_per_role=6, empty_test=True)
    with pytest.raises(partitioning.NoEvaluableFoldsError):
        partitioning.partition(df)


def test_empty_dataframe_rejected() -> None:
    df = make_dataframe(n_folds=1, rows_per_role=4).iloc[0:0]
    with pytest.raises(partitioning.NoEvaluableFoldsError):
        partitioning.partition(df)


def test_no_sort_shuffle_sample_calls_in_source() -> None:
    """AST check: partition() makes no sort/shuffle/sample method call (Req 5.4)."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(partitioning.partition))
    forbidden = {"sample", "shuffle", "sort_values", "sort_index", "reset_index"}
    called_attrs = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert forbidden.isdisjoint(called_attrs)
    assert np is not None  # keep import referenced
