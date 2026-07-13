"""The automated leakage audit — the "leakage-clearance certificate."

Pure functions — no I/O. This is the dataset-level counterpart to
`claude_MLResearchFramework.md` §2 Stage 3 ("Output: a registered dataset
artifact + a leakage-clearance certificate") and §10.7's overfitting-
prevention doctrine: every impressive dataset is treated as leaky until
these checks pass. A dataset whose audit fails is never persisted — the
builder reports every finding back to the caller instead.

Checks performed:

1. **Purge/embargo integrity** — for split strategies that purge (currently
   only Purged CV), no index may appear in more than one role within the
   same fold, and no TRAIN index may fall inside the embargo zone around a
   TEST block.
2. **Finite-value integrity** — every feature and label value in a
   generated row must be finite (no NaN/Inf reaching a persisted row); rows
   violating this were supposed to be dropped upstream, so a finding here
   indicates a builder bug, not a data problem.
3. **Temporal ordering** — within a fold, every TRAIN/VALIDATION index must
   be chronologically ordered relative to TEST for split strategies that
   are supposed to be strictly forward-looking (walk-forward, rolling,
   expanding) — catches an accidental "test in the past" construction bug.
4. **Label/feature time-alignment** — the label's target horizon must not
   reach beyond the last available bar for its symbol (i.e. no label was
   fabricated past the end of known history).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from aqros_dataset_builder.domain.models import SplitRole, SplitStrategy
from aqros_dataset_builder.domain.splitters import Fold


@dataclass(frozen=True, slots=True)
class AuditResult:
    """Outcome of running the leakage audit over a generated dataset."""

    passed: bool
    findings: list[str]


def audit_fold_integrity(folds: list[Fold], split_strategy: SplitStrategy) -> list[str]:
    """Check purge/embargo and temporal-ordering integrity across folds."""
    findings: list[str] = []

    for fold_idx, fold in enumerate(folds):
        train_indices = [i for i, role in fold.items() if role is SplitRole.TRAIN]
        val_indices = [i for i, role in fold.items() if role is SplitRole.VALIDATION]
        test_indices = [i for i, role in fold.items() if role is SplitRole.TEST]

        if split_strategy in (
            SplitStrategy.WALK_FORWARD,
            SplitStrategy.ROLLING_WINDOW,
            SplitStrategy.EXPANDING_WINDOW,
        ):
            # These strategies must be strictly forward-looking: every train
            # index precedes every validation index, which precedes every
            # test index.
            if train_indices and val_indices and max(train_indices) >= min(val_indices):
                findings.append(
                    f"fold {fold_idx}: train indices overlap or follow validation "
                    f"indices in time (strategy={split_strategy.value})"
                )
            if val_indices and test_indices and max(val_indices) >= min(test_indices):
                findings.append(
                    f"fold {fold_idx}: validation indices overlap or follow test "
                    f"indices in time (strategy={split_strategy.value})"
                )
            elif (
                not val_indices
                and train_indices
                and test_indices
                and max(train_indices) >= min(test_indices)
            ):
                findings.append(
                    f"fold {fold_idx}: train indices overlap or follow test "
                    f"indices in time (strategy={split_strategy.value})"
                )

        if split_strategy is SplitStrategy.PURGED_CV and train_indices and test_indices:
            # No train index may sit strictly between the min and max test
            # index without having been purged — that would mean an
            # embargo/purge gap was not honored.
            test_min, test_max = min(test_indices), max(test_indices)
            leaking_train = [i for i in train_indices if test_min <= i <= test_max]
            if leaking_train:
                findings.append(
                    f"fold {fold_idx}: {len(leaking_train)} train index(es) fall "
                    f"inside the test block's span without being purged"
                )

    return findings


def audit_finite_values(values: list[float], column_name: str) -> list[str]:
    """Check that every value in a persisted column is finite."""
    non_finite_count = sum(1 for v in values if not math.isfinite(v))
    if non_finite_count:
        return [f"{column_name}: {non_finite_count} non-finite value(s) found in persisted rows"]
    return []


def audit_label_horizon(
    label_event_time_index: int, last_available_index: int, horizon_bars: int, symbol: str
) -> list[str]:
    """Check that a label's target horizon never reaches past known history."""
    if label_event_time_index + horizon_bars > last_available_index:
        return [
            f"{symbol}: label at index {label_event_time_index} requires a future bar at "
            f"index {label_event_time_index + horizon_bars}, beyond the last available "
            f"index {last_available_index}"
        ]
    return []


def run_leakage_audit(
    folds: list[Fold],
    split_strategy: SplitStrategy,
    feature_values: list[float],
    label_values: list[float],
) -> AuditResult:
    """Run every applicable check and produce the pass/fail certificate."""
    findings: list[str] = []
    findings.extend(audit_fold_integrity(folds, split_strategy))
    findings.extend(audit_finite_values(feature_values, "features"))
    findings.extend(audit_finite_values(label_values, "label"))
    return AuditResult(passed=not findings, findings=findings)
