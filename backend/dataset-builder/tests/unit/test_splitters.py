"""Unit tests for split algorithms (pure domain logic, no I/O)."""

from __future__ import annotations

from aqros_dataset_builder.domain.models import (
    ExpandingWindowParams,
    PurgedCVParams,
    RollingWindowParams,
    SplitRole,
    WalkForwardParams,
)
from aqros_dataset_builder.domain.splitters import (
    compute_splits,
    expanding_window_splits,
    purged_cv_splits,
    rolling_window_splits,
    walk_forward_splits,
)


def test_walk_forward_produces_multiple_sliding_folds() -> None:
    params = WalkForwardParams(train_size=3, validation_size=1, test_size=1, step_size=1)
    folds = walk_forward_splits(n=10, params=params)

    # fold_span = 5; with step 1, a fold fits starting at every index from 0
    # up to n - fold_span = 5 inclusive -> 6 folds (starts 0,1,2,3,4,5).
    assert len(folds) == 6
    first = folds[0]
    assert [i for i, r in first.items() if r is SplitRole.TRAIN] == [0, 1, 2]
    assert [i for i, r in first.items() if r is SplitRole.VALIDATION] == [3]
    assert [i for i, r in first.items() if r is SplitRole.TEST] == [4]

    second = folds[1]
    assert [i for i, r in second.items() if r is SplitRole.TRAIN] == [1, 2, 3]


def test_walk_forward_with_no_room_returns_no_folds() -> None:
    params = WalkForwardParams(train_size=10, validation_size=5, test_size=5, step_size=1)
    folds = walk_forward_splits(n=5, params=params)
    assert folds == []


def test_rolling_window_uses_only_the_most_recent_slice() -> None:
    params = RollingWindowParams(train_size=3, validation_size=1, test_size=1)
    folds = rolling_window_splits(n=10, params=params)

    assert len(folds) == 1
    fold = folds[0]
    # fold_span = 5, anchored to the end: indices 5..9.
    assert [i for i, r in fold.items() if r is SplitRole.TRAIN] == [5, 6, 7]
    assert [i for i, r in fold.items() if r is SplitRole.VALIDATION] == [8]
    assert [i for i, r in fold.items() if r is SplitRole.TEST] == [9]
    # Indices before the window are excluded entirely (not TRAIN).
    assert all(i not in fold for i in range(5))


def test_rolling_window_with_no_room_returns_no_folds() -> None:
    params = RollingWindowParams(train_size=10, validation_size=5, test_size=5)
    folds = rolling_window_splits(n=5, params=params)
    assert folds == []


def test_expanding_window_train_includes_all_prior_history() -> None:
    params = ExpandingWindowParams(validation_size=2, test_size=2)
    folds = expanding_window_splits(n=10, params=params)

    assert len(folds) == 1
    fold = folds[0]
    assert [i for i, r in fold.items() if r is SplitRole.TRAIN] == list(range(6))
    assert [i for i, r in fold.items() if r is SplitRole.VALIDATION] == [6, 7]
    assert [i for i, r in fold.items() if r is SplitRole.TEST] == [8, 9]


def test_expanding_window_with_no_room_returns_no_folds() -> None:
    params = ExpandingWindowParams(validation_size=5, test_size=5)
    folds = expanding_window_splits(n=8, params=params)
    assert folds == []


def test_purged_cv_creates_n_splits_folds_with_test_block_each() -> None:
    params = PurgedCVParams(n_splits=5, embargo_size=0)
    folds = purged_cv_splits(n=10, params=params)

    assert len(folds) == 5
    # Every index is TEST in exactly one fold (no embargo, so it's also
    # TRAIN in every other fold).
    for k, fold in enumerate(folds):
        test_indices = [i for i, r in fold.items() if r is SplitRole.TEST]
        assert test_indices == [k * 2, k * 2 + 1]


def test_purged_cv_embargo_purges_indices_near_the_test_block() -> None:
    params = PurgedCVParams(n_splits=5, embargo_size=1)
    folds = purged_cv_splits(n=10, params=params)

    fold = folds[2]  # test block = indices [4, 5]
    test_indices = {i for i, r in fold.items() if r is SplitRole.TEST}
    train_indices = {i for i, r in fold.items() if r is SplitRole.TRAIN}
    assert test_indices == {4, 5}
    # Indices 3 and 6 are within the 1-bar embargo of the test block and
    # must be purged (present in neither role).
    assert 3 not in fold
    assert 6 not in fold
    assert 3 not in train_indices
    assert 6 not in train_indices


def test_purged_cv_requires_at_least_two_splits() -> None:
    params = PurgedCVParams(n_splits=1, embargo_size=0)
    folds = purged_cv_splits(n=10, params=params)
    assert folds == []


def test_purged_cv_train_never_overlaps_test_block_in_time() -> None:
    """No train index may fall within [test_min, test_max] for any fold —
    the direct check that purging actually removes leaking neighbors."""
    params = PurgedCVParams(n_splits=4, embargo_size=2)
    folds = purged_cv_splits(n=20, params=params)

    for fold in folds:
        test_indices = [i for i, r in fold.items() if r is SplitRole.TEST]
        train_indices = [i for i, r in fold.items() if r is SplitRole.TRAIN]
        if not test_indices or not train_indices:
            continue
        test_min, test_max = min(test_indices), max(test_indices)
        leaking = [i for i in train_indices if test_min <= i <= test_max]
        assert leaking == []


def test_compute_splits_dispatches_by_params_type() -> None:
    wf_params = WalkForwardParams(train_size=2, validation_size=1, test_size=1, step_size=1)
    direct = walk_forward_splits(10, wf_params)
    dispatched = compute_splits(10, wf_params)
    assert direct == dispatched


def test_every_strategy_is_strictly_temporal_within_a_fold() -> None:
    """For forward-looking strategies, train < validation < test in index order."""
    strategies = [
        walk_forward_splits(20, WalkForwardParams(4, 2, 2, 2)),
        rolling_window_splits(20, RollingWindowParams(6, 2, 2)),
        expanding_window_splits(20, ExpandingWindowParams(3, 3)),
    ]
    for folds in strategies:
        for fold in folds:
            train = [i for i, r in fold.items() if r is SplitRole.TRAIN]
            val = [i for i, r in fold.items() if r is SplitRole.VALIDATION]
            test = [i for i, r in fold.items() if r is SplitRole.TEST]
            if train and val:
                assert max(train) < min(val)
            if val and test:
                assert max(val) < min(test)
