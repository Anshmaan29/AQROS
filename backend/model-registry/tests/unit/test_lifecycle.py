"""Unit + property tests for the Lifecycle_State_Machine (task 8.3).

Exercises ``domain/lifecycle.py``'s pure ``transition_allowed`` in isolation:
the forward chain
``REGISTERED -> VALIDATED -> STAGING -> PRODUCTION -> DEPRECATED -> ARCHIVED``,
the rollback edge ``DEPRECATED -> PRODUCTION``, and the abandonment edges from
any non-``PRODUCTION`` state directly to ``ARCHIVED`` (design.md Section 10,
Key Design Decision 5).
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aqros_model_registry.domain.lifecycle import (
    IllegalTransitionError,
    transition_allowed,
)
from aqros_model_registry.domain.models import LifecycleState

# The forward-only chain (Requirement 11.2), built independently of
# `lifecycle.py`'s internals so the property test below is a genuine
# cross-check rather than a reflection of the implementation.
_FORWARD_EDGES: frozenset[tuple[LifecycleState, LifecycleState]] = frozenset(
    {
        (LifecycleState.REGISTERED, LifecycleState.VALIDATED),
        (LifecycleState.VALIDATED, LifecycleState.STAGING),
        (LifecycleState.STAGING, LifecycleState.PRODUCTION),
        (LifecycleState.PRODUCTION, LifecycleState.DEPRECATED),
        (LifecycleState.DEPRECATED, LifecycleState.ARCHIVED),
    }
)

# Abandonment: any non-PRODUCTION, non-terminal state may go straight to
# ARCHIVED (Requirement 11.5). PRODUCTION is excluded (Requirement 11.6);
# ARCHIVED is excluded as terminal (Requirement 11.4).
_ABANDONMENT_EDGES: frozenset[tuple[LifecycleState, LifecycleState]] = frozenset(
    (state, LifecycleState.ARCHIVED)
    for state in LifecycleState
    if state not in (LifecycleState.PRODUCTION, LifecycleState.ARCHIVED)
)

# The sole rollback edge (Requirement 15.1).
_ROLLBACK_EDGE: tuple[LifecycleState, LifecycleState] = (
    LifecycleState.DEPRECATED,
    LifecycleState.PRODUCTION,
)

_ALL_STATES = list(LifecycleState)


def _independently_expected_legal(
    from_state: LifecycleState, to_state: LifecycleState, is_rollback: bool
) -> bool:
    """Build the expected legal/illegal verdict from the design's edge table.

    Deliberately re-derived here (not imported from `lifecycle.py`) so the
    property test genuinely cross-checks the implementation against the
    design rather than restating it.
    """
    if is_rollback:
        return (from_state, to_state) == _ROLLBACK_EDGE
    return (from_state, to_state) in _FORWARD_EDGES or (from_state, to_state) in _ABANDONMENT_EDGES


# Feature: model-registry, Property 11: Only legal lifecycle transitions are applied
# For any (from_state, to_state), a transition is applied only if it lies on a
# permitted edge (forward chain, rollback DEPRECATED->PRODUCTION, or
# abandonment to ARCHIVED); every other transition is rejected.
# Validates: Requirements 11.2, 11.3
@settings(max_examples=100)
@given(
    from_state=st.sampled_from(_ALL_STATES),
    to_state=st.sampled_from(_ALL_STATES),
    is_rollback=st.booleans(),
)
def test_transition_allowed_matches_independently_built_edge_set(
    from_state: LifecycleState, to_state: LifecycleState, is_rollback: bool
) -> None:
    """Exhaustively (LifecycleState x LifecycleState x bool is finite) checks
    that `transition_allowed` returns True only for the exact permitted edge
    set and raises `IllegalTransitionError` for every other combination.
    """
    expected_legal = _independently_expected_legal(from_state, to_state, is_rollback)

    if expected_legal:
        assert transition_allowed(from_state, to_state, is_rollback) is True
    else:
        with pytest.raises(IllegalTransitionError) as exc_info:
            transition_allowed(from_state, to_state, is_rollback)
        assert exc_info.value.from_state == from_state
        assert exc_info.value.to_state == to_state
        assert exc_info.value.is_rollback == is_rollback


# Feature: model-registry, Property 12: ARCHIVED is terminal and PRODUCTION cannot skip DEPRECATED
# For any Model_Version in ARCHIVED, no further transition is permitted; no
# PRODUCTION version transitions to ARCHIVED without first entering DEPRECATED.
# Validates: Requirements 11.4, 11.6
@settings(max_examples=100)
@given(to_state=st.sampled_from(_ALL_STATES), is_rollback=st.booleans())
def test_archived_has_no_outgoing_legal_edge(to_state: LifecycleState, is_rollback: bool) -> None:
    """ARCHIVED is terminal: every transition out of it is illegal, for any
    target state and regardless of the rollback flag."""
    with pytest.raises(IllegalTransitionError) as exc_info:
        transition_allowed(LifecycleState.ARCHIVED, to_state, is_rollback)
    assert exc_info.value.from_state == LifecycleState.ARCHIVED
    assert exc_info.value.to_state == to_state


# Feature: model-registry, Property 12: ARCHIVED is terminal and PRODUCTION cannot skip DEPRECATED
# For any Model_Version in ARCHIVED, no further transition is permitted; no
# PRODUCTION version transitions to ARCHIVED without first entering DEPRECATED.
# Validates: Requirements 11.4, 11.6
@settings(max_examples=100)
@given(is_rollback=st.booleans())
def test_production_cannot_transition_directly_to_archived(is_rollback: bool) -> None:
    """PRODUCTION -> ARCHIVED is illegal regardless of the rollback flag; the
    only legal exit from PRODUCTION is to DEPRECATED."""
    with pytest.raises(IllegalTransitionError):
        transition_allowed(LifecycleState.PRODUCTION, LifecycleState.ARCHIVED, is_rollback)


# --- Concrete examples: forward chain -------------------------------------


def test_registered_to_validated_is_legal() -> None:
    assert transition_allowed(LifecycleState.REGISTERED, LifecycleState.VALIDATED, False) is True


def test_validated_to_staging_is_legal() -> None:
    assert transition_allowed(LifecycleState.VALIDATED, LifecycleState.STAGING, False) is True


def test_staging_to_production_is_legal() -> None:
    assert transition_allowed(LifecycleState.STAGING, LifecycleState.PRODUCTION, False) is True


def test_production_to_deprecated_is_legal() -> None:
    assert transition_allowed(LifecycleState.PRODUCTION, LifecycleState.DEPRECATED, False) is True


def test_deprecated_to_archived_is_legal() -> None:
    assert transition_allowed(LifecycleState.DEPRECATED, LifecycleState.ARCHIVED, False) is True


# --- Concrete example: rollback edge ---------------------------------------


def test_deprecated_to_production_rollback_is_legal() -> None:
    assert transition_allowed(LifecycleState.DEPRECATED, LifecycleState.PRODUCTION, True) is True


def test_deprecated_to_production_without_rollback_flag_is_illegal() -> None:
    """The rollback edge is only legal when explicitly flagged as a rollback;
    otherwise DEPRECATED -> PRODUCTION is not on the forward chain."""
    with pytest.raises(IllegalTransitionError):
        transition_allowed(LifecycleState.DEPRECATED, LifecycleState.PRODUCTION, False)


# --- Concrete examples: abandonment edges -----------------------------------


def test_registered_to_archived_abandonment_is_legal() -> None:
    assert transition_allowed(LifecycleState.REGISTERED, LifecycleState.ARCHIVED, False) is True


def test_validated_to_archived_abandonment_is_legal() -> None:
    assert transition_allowed(LifecycleState.VALIDATED, LifecycleState.ARCHIVED, False) is True


def test_staging_to_archived_abandonment_is_legal() -> None:
    assert transition_allowed(LifecycleState.STAGING, LifecycleState.ARCHIVED, False) is True


# --- Illegal transition surfaces the offending edge -------------------------


def test_illegal_transition_error_carries_the_offending_edge() -> None:
    with pytest.raises(IllegalTransitionError) as exc_info:
        transition_allowed(LifecycleState.REGISTERED, LifecycleState.PRODUCTION, False)
    assert exc_info.value.from_state == LifecycleState.REGISTERED
    assert exc_info.value.to_state == LifecycleState.PRODUCTION
    assert exc_info.value.is_rollback is False


def test_backward_transition_on_forward_chain_is_illegal() -> None:
    with pytest.raises(IllegalTransitionError):
        transition_allowed(LifecycleState.STAGING, LifecycleState.VALIDATED, False)


def test_self_transition_is_illegal() -> None:
    with pytest.raises(IllegalTransitionError):
        transition_allowed(LifecycleState.VALIDATED, LifecycleState.VALIDATED, False)
