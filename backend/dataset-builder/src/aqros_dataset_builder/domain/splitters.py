"""Train/validation/test split algorithms — pure, index-based, no I/O.

Every splitter operates on a simple ``n`` (number of ascending-time rows for
one symbol) and returns a list of *folds*, where each fold is a mapping from
row index to :class:`SplitRole`. Indices not present in a fold's mapping are
excluded from that fold entirely (this is how purging removes rows, rather
than mislabeling them).

All four algorithms are **strictly temporal** — validation/test indices are
always later in time than the train indices that precede them within a fold.
This is the direct implementation of `claude_MLResearchFramework.md` §8's
central rule: "Random k-fold... is FORBIDDEN" for financial data, because
shuffling time shuffles the future into the past.
"""

from __future__ import annotations

from aqros_dataset_builder.domain.models import (
    ExpandingWindowParams,
    PurgedCVParams,
    RollingWindowParams,
    SplitParams,
    SplitRole,
    WalkForwardParams,
)

Fold = dict[int, SplitRole]


def walk_forward_splits(n: int, params: WalkForwardParams) -> list[Fold]:
    """Multiple sequential folds sliding forward by ``step_size`` bars each.

    Each fold is a fixed-size (train, validation, test) window; the window
    slides forward by ``step_size`` until it runs off the end of the series.
    This simulates periodic retraining through history
    (`claude_MLResearchFramework.md` §8.1).
    """
    folds: list[Fold] = []
    fold_span = params.train_size + params.validation_size + params.test_size
    start = 0
    while start + fold_span <= n:
        fold: Fold = {}
        train_end = start + params.train_size
        val_end = train_end + params.validation_size
        test_end = val_end + params.test_size
        for i in range(start, train_end):
            fold[i] = SplitRole.TRAIN
        for i in range(train_end, val_end):
            fold[i] = SplitRole.VALIDATION
        for i in range(val_end, test_end):
            fold[i] = SplitRole.TEST
        folds.append(fold)
        start += params.step_size
    return folds


def rolling_window_splits(n: int, params: RollingWindowParams) -> list[Fold]:
    """A single fold using only the most recent fixed-size window of history.

    Unlike walk-forward, this discards all history before the window
    (`claude_MLResearchFramework.md` §8.2) — appropriate when older data is
    considered a different, no-longer-relevant regime. Anchored to the *end*
    of the series so it always reflects the most current, fixed-size slice.
    """
    fold_span = params.train_size + params.validation_size + params.test_size
    if fold_span > n:
        return []

    start = n - fold_span
    train_end = start + params.train_size
    val_end = train_end + params.validation_size

    fold: Fold = {}
    for i in range(start, train_end):
        fold[i] = SplitRole.TRAIN
    for i in range(train_end, val_end):
        fold[i] = SplitRole.VALIDATION
    for i in range(val_end, n):
        fold[i] = SplitRole.TEST
    return [fold]


def expanding_window_splits(n: int, params: ExpandingWindowParams) -> list[Fold]:
    """A single fold where train uses *all* history before the val/test tail.

    Train grows to include every available bar before the held-out tail
    (`claude_MLResearchFramework.md` §8.3) — appropriate when more history
    is assumed to help and the relationship is relatively stable.
    """
    tail_span = params.validation_size + params.test_size
    if tail_span >= n:
        return []

    val_start = n - tail_span
    test_start = val_start + params.validation_size

    fold: Fold = {}
    for i in range(0, val_start):
        fold[i] = SplitRole.TRAIN
    for i in range(val_start, test_start):
        fold[i] = SplitRole.VALIDATION
    for i in range(test_start, n):
        fold[i] = SplitRole.TEST
    return [fold]


def purged_cv_splits(n: int, params: PurgedCVParams) -> list[Fold]:
    """Combinatorial-purged-CV-style folds: k contiguous time blocks as test,
    with purging + an embargo gap around each test block to prevent
    label-window leakage across the train/test boundary
    (López de Prado; `claude_MLResearchFramework.md` §8.4-8.5).

    Each of the ``n_splits`` folds holds out one contiguous block as TEST;
    every other index becomes TRAIN, **except** those within
    ``embargo_size`` bars on either side of the test block, which are purged
    (excluded from the fold entirely — neither trained nor tested on, since
    their label windows plausibly overlap the test block's information).
    """
    if params.n_splits < 2 or n < params.n_splits:
        return []

    block_size = n // params.n_splits
    folds: list[Fold] = []

    for k in range(params.n_splits):
        test_start = k * block_size
        # The last block absorbs any remainder so every row is covered.
        test_end = n if k == params.n_splits - 1 else test_start + block_size

        purge_start = max(0, test_start - params.embargo_size)
        purge_end = min(n, test_end + params.embargo_size)

        fold: Fold = {}
        for i in range(n):
            if test_start <= i < test_end:
                fold[i] = SplitRole.TEST
            elif purge_start <= i < purge_end:
                continue  # purged: too close in time to the test block
            else:
                fold[i] = SplitRole.TRAIN
        folds.append(fold)

    return folds


def compute_splits(n: int, params: SplitParams) -> list[Fold]:
    """Dispatch to the splitter matching the type of ``params``."""
    if isinstance(params, WalkForwardParams):
        return walk_forward_splits(n, params)
    if isinstance(params, RollingWindowParams):
        return rolling_window_splits(n, params)
    if isinstance(params, ExpandingWindowParams):
        return expanding_window_splits(n, params)
    return purged_cv_splits(n, params)
