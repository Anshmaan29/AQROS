"""Unit + property tests for the Approval_Policy + Four_Eyes evaluator (task 8.4).

Exercises ``domain/approval.py`` in isolation: the fixed per-transition
``gate_for`` table (design.md Key Design Decision 6) and the pure
``four_eyes_satisfied`` evaluator (Requirements 12-14, 21.2).
"""

from __future__ import annotations

from datetime import datetime

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aqros_model_registry.domain.approval import (
    DECISION_APPROVE,
    DECISION_REJECT,
    GateKind,
    four_eyes_satisfied,
    gate_for,
)
from aqros_model_registry.domain.lifecycle import (
    IllegalTransitionError,
    transition_allowed,
)
from aqros_model_registry.domain.models import (
    Approval,
    ApprovalState,
    LifecycleState,
    PrincipalKind,
    PromotionRequest,
)

_ALL_STATES = list(LifecycleState)

# A small, fixed name pool with deliberate overlap so hypothesis explores
# collisions (approver == requester, duplicate approvers) far more often
# than a fully random string generator would.
_NAMES = ("alice", "bob", "carol", "dave", "requester")


def _make_request(requester: str, is_rollback: bool = False) -> PromotionRequest:
    return PromotionRequest(
        model_name="aapl_5d_direction__random_forest",
        version=1,
        from_state=LifecycleState.STAGING,
        to_state=LifecycleState.PRODUCTION,
        requester=requester,
        justification="promote",
        approval_state=ApprovalState.PENDING,
        is_rollback=is_rollback,
    )


def _make_approval(approver: str, kind: PrincipalKind, decision: str) -> Approval:
    return Approval(
        approver=approver,
        approver_kind=kind,
        decision=decision,
        reason=None,
        created_at=datetime(2024, 1, 1),
    )


def _independently_expected_satisfied(
    requester: str, entries: list[tuple[str, PrincipalKind, str]]
) -> bool:
    """Re-derive the expected Four_Eyes verdict independently of ``approval.py``.

    Satisfied iff there are at least two *distinct* approvers who are all of:
    human, not the requester, and whose decision is "approve" — counting each
    distinct approver at most once (Requirements 14.1, 14.2, 14.6, 14.7, 21.2).
    """
    distinct: set[str] = set()
    for approver, kind, decision in entries:
        if kind is not PrincipalKind.HUMAN:
            continue
        if decision != DECISION_APPROVE:
            continue
        if approver == requester:
            continue
        distinct.add(approver)
    return len(distinct) >= 2


# --- Property 13: VALIDATED requires validation evidence --------------------
# (Property 13 is fully validated at the services.py layer, which enforces
# that a REGISTERED->VALIDATED transition only applies once Validation_Evidence
# is attached. What approval.py itself asserts is the gate table: this edge's
# gate is EVIDENCE, i.e. no human approval is required — no PromotionRequest
# with approvals is ever created for it.)


# Feature: model-registry, Property 13: VALIDATED requires validation evidence
# For any REGISTERED->VALIDATED transition, the transition succeeds only if
# Validation_Evidence is attached, and that evidence is recorded immutably.
# Validates: Requirements 12.1, 12.2, 12.3
def test_gate_for_registered_to_validated_is_evidence_not_human_approval() -> None:
    """approval.py's contribution to Property 13: this edge's gate is EVIDENCE,
    not SINGLE_APPROVAL or FOUR_EYES — no human approval is ever required."""
    gate = gate_for(LifecycleState.REGISTERED, LifecycleState.VALIDATED, False)
    assert gate == GateKind.EVIDENCE
    assert gate not in (GateKind.SINGLE_APPROVAL, GateKind.FOUR_EYES, GateKind.NONE)


# --- gate_for: full table matches design.md Key Design Decision 6 ----------

# Independently re-derived gate table (not imported from approval.py) so the
# property test below is a genuine cross-check against the design rather than
# a restatement of the implementation.
_EXPECTED_FORWARD_GATES: dict[tuple[LifecycleState, LifecycleState], GateKind] = {
    (LifecycleState.REGISTERED, LifecycleState.VALIDATED): GateKind.EVIDENCE,
    (LifecycleState.VALIDATED, LifecycleState.STAGING): GateKind.SINGLE_APPROVAL,
    (LifecycleState.STAGING, LifecycleState.PRODUCTION): GateKind.FOUR_EYES,
    (LifecycleState.PRODUCTION, LifecycleState.DEPRECATED): GateKind.SINGLE_APPROVAL,
    (LifecycleState.DEPRECATED, LifecycleState.ARCHIVED): GateKind.SINGLE_APPROVAL,
}
_EXPECTED_ABANDONMENT_GATES: dict[tuple[LifecycleState, LifecycleState], GateKind] = {
    (state, LifecycleState.ARCHIVED): GateKind.SINGLE_APPROVAL
    for state in LifecycleState
    if state not in (LifecycleState.PRODUCTION, LifecycleState.ARCHIVED)
}
_EXPECTED_NON_ROLLBACK_GATES: dict[tuple[LifecycleState, LifecycleState], GateKind] = {
    **_EXPECTED_FORWARD_GATES,
    **_EXPECTED_ABANDONMENT_GATES,
}
_EXPECTED_ROLLBACK_EDGE: tuple[LifecycleState, LifecycleState] = (
    LifecycleState.DEPRECATED,
    LifecycleState.PRODUCTION,
)


@settings(max_examples=100)
@given(
    from_state=st.sampled_from(_ALL_STATES),
    to_state=st.sampled_from(_ALL_STATES),
    is_rollback=st.booleans(),
)
def test_gate_for_matches_independently_built_gate_table(
    from_state: LifecycleState, to_state: LifecycleState, is_rollback: bool
) -> None:
    """Exhaustively (finite state x state x bool) checks ``gate_for`` against
    the design.md Key Design Decision 6 gate table, including the rollback
    edge always being FOUR_EYES and every non-permitted edge raising."""
    if is_rollback:
        if (from_state, to_state) == _EXPECTED_ROLLBACK_EDGE:
            assert gate_for(from_state, to_state, True) == GateKind.FOUR_EYES
        else:
            with pytest.raises(IllegalTransitionError):
                gate_for(from_state, to_state, True)
        return

    expected = _EXPECTED_NON_ROLLBACK_GATES.get((from_state, to_state))
    if expected is None:
        with pytest.raises(IllegalTransitionError):
            gate_for(from_state, to_state, False)
    else:
        assert gate_for(from_state, to_state, False) == expected


@settings(max_examples=100)
@given(
    from_state=st.sampled_from(_ALL_STATES),
    to_state=st.sampled_from(_ALL_STATES),
    is_rollback=st.booleans(),
)
def test_gate_for_raises_exactly_where_lifecycle_considers_the_edge_illegal(
    from_state: LifecycleState, to_state: LifecycleState, is_rollback: bool
) -> None:
    """gate_for's legality surface must stay in lockstep with
    ``transition_allowed`` (Requirement 11.3): defined for every legal edge,
    illegal for every edge ``lifecycle.py`` itself rejects."""
    try:
        transition_allowed(from_state, to_state, is_rollback)
        lifecycle_says_legal = True
    except IllegalTransitionError:
        lifecycle_says_legal = False

    if lifecycle_says_legal:
        # Defined: does not raise, returns a real GateKind.
        gate = gate_for(from_state, to_state, is_rollback)
        assert isinstance(gate, GateKind)
    else:
        with pytest.raises(IllegalTransitionError):
            gate_for(from_state, to_state, is_rollback)


# Concrete examples covering each row of the gate table explicitly.
def test_gate_for_validated_to_staging_is_single_approval() -> None:
    assert gate_for(LifecycleState.VALIDATED, LifecycleState.STAGING, False) == (
        GateKind.SINGLE_APPROVAL
    )


def test_gate_for_staging_to_production_is_four_eyes() -> None:
    assert gate_for(LifecycleState.STAGING, LifecycleState.PRODUCTION, False) == GateKind.FOUR_EYES


def test_gate_for_production_to_deprecated_is_single_approval() -> None:
    assert gate_for(LifecycleState.PRODUCTION, LifecycleState.DEPRECATED, False) == (
        GateKind.SINGLE_APPROVAL
    )


def test_gate_for_deprecated_to_archived_is_single_approval() -> None:
    assert gate_for(LifecycleState.DEPRECATED, LifecycleState.ARCHIVED, False) == (
        GateKind.SINGLE_APPROVAL
    )


def test_gate_for_abandonment_edges_are_single_approval() -> None:
    for state in (LifecycleState.REGISTERED, LifecycleState.VALIDATED, LifecycleState.STAGING):
        assert gate_for(state, LifecycleState.ARCHIVED, False) == GateKind.SINGLE_APPROVAL


def test_gate_for_rollback_edge_is_four_eyes() -> None:
    assert gate_for(LifecycleState.DEPRECATED, LifecycleState.PRODUCTION, True) == (
        GateKind.FOUR_EYES
    )


def test_gate_for_illegal_edge_raises() -> None:
    with pytest.raises(IllegalTransitionError):
        gate_for(LifecycleState.REGISTERED, LifecycleState.PRODUCTION, False)


def test_gate_for_rollback_flag_on_non_rollback_edge_raises() -> None:
    with pytest.raises(IllegalTransitionError):
        gate_for(LifecycleState.VALIDATED, LifecycleState.STAGING, True)


# --- Property 14: PRODUCTION/rollback require four-eyes by distinct humans --


# Feature: model-registry, Property 14: PRODUCTION and rollback require four-eyes by distinct humans
# For any promotion to PRODUCTION or any rollback, the transition applies only
# after two distinct human approvers, neither of whom is the requester, have
# approved; the same approver never counts twice.
# Validates: Requirements 14.1, 14.2, 14.4, 14.6, 15.2
@settings(max_examples=100)
@given(
    requester=st.sampled_from(_NAMES),
    entries=st.lists(
        st.tuples(
            st.sampled_from(_NAMES),
            st.sampled_from(list(PrincipalKind)),
            st.sampled_from([DECISION_APPROVE, DECISION_REJECT]),
        ),
        max_size=8,
    ),
)
def test_four_eyes_satisfied_matches_independent_reference(
    requester: str, entries: list[tuple[str, PrincipalKind, str]]
) -> None:
    """``four_eyes_satisfied`` returns True iff there are >=2 distinct HUMAN
    approvers with decision "approve", none equal to the requester, each
    counted once — for arbitrary mixes of overlapping/duplicated/rejecting
    approvers."""
    request = _make_request(requester)
    approvals = [_make_approval(approver, kind, decision) for approver, kind, decision in entries]

    expected = _independently_expected_satisfied(requester, entries)
    assert four_eyes_satisfied(request, approvals) is expected


@settings(max_examples=100)
@given(
    requester=st.sampled_from(_NAMES),
    approver_a=st.sampled_from(_NAMES),
    approver_b=st.sampled_from(_NAMES),
)
def test_four_eyes_requires_two_distinct_non_requester_human_approvers(
    requester: str, approver_a: str, approver_b: str
) -> None:
    """Two HUMAN "approve" decisions satisfy Four_Eyes iff they name two
    distinct approvers, neither of whom is the requester."""
    request = _make_request(requester)
    approvals = [
        _make_approval(approver_a, PrincipalKind.HUMAN, DECISION_APPROVE),
        _make_approval(approver_b, PrincipalKind.HUMAN, DECISION_APPROVE),
    ]
    expected = approver_a != approver_b and approver_a != requester and approver_b != requester
    assert four_eyes_satisfied(request, approvals) is expected


def test_four_eyes_same_approver_twice_never_counts_twice() -> None:
    """A repeated approval from the same human approver is not double-counted
    (Requirement 14.6): one distinct approver is never enough on its own."""
    request = _make_request("requester")
    approvals = [
        _make_approval("alice", PrincipalKind.HUMAN, DECISION_APPROVE),
        _make_approval("alice", PrincipalKind.HUMAN, DECISION_APPROVE),
        _make_approval("alice", PrincipalKind.HUMAN, DECISION_APPROVE),
    ]
    assert four_eyes_satisfied(request, approvals) is False


def test_four_eyes_two_distinct_human_approvers_satisfies() -> None:
    request = _make_request("requester")
    approvals = [
        _make_approval("alice", PrincipalKind.HUMAN, DECISION_APPROVE),
        _make_approval("bob", PrincipalKind.HUMAN, DECISION_APPROVE),
    ]
    assert four_eyes_satisfied(request, approvals) is True


def test_four_eyes_requester_approval_is_excluded() -> None:
    """An approval attributed to the requester never counts toward the
    threshold, even alongside a genuine second approver (Requirement 14.2)."""
    request = _make_request("requester")
    approvals = [
        _make_approval("requester", PrincipalKind.HUMAN, DECISION_APPROVE),
        _make_approval("alice", PrincipalKind.HUMAN, DECISION_APPROVE),
    ]
    assert four_eyes_satisfied(request, approvals) is False


def test_four_eyes_no_approvals_is_unsatisfied() -> None:
    request = _make_request("requester")
    assert four_eyes_satisfied(request, []) is False


# --- Property 15: automated principals cannot satisfy a PRODUCTION gate -----


# Feature: model-registry, Property 15: Automated principals cannot satisfy a PRODUCTION gate
# For any approval or PRODUCTION promotion attributed to a non-human
# principal, the action is rejected and the state is unchanged.
# Validates: Requirements 14.7, 21.2
@settings(max_examples=100)
@given(
    requester=st.sampled_from(_NAMES),
    approvers=st.lists(st.sampled_from(_NAMES), min_size=0, max_size=6),
)
def test_four_eyes_never_satisfied_by_automated_only_approvals(
    requester: str, approvers: list[str]
) -> None:
    """Any list of approvals where every principal is AUTOMATED never
    satisfies Four_Eyes, no matter how many distinct automated approvers
    "approve" — automated principals can never substitute for a human."""
    request = _make_request(requester)
    approvals = [
        _make_approval(approver, PrincipalKind.AUTOMATED, DECISION_APPROVE)
        for approver in approvers
    ]
    assert four_eyes_satisfied(request, approvals) is False


@settings(max_examples=100)
@given(
    requester=st.sampled_from(_NAMES),
    human_approver=st.sampled_from(_NAMES),
    automated_approver=st.sampled_from(_NAMES),
)
def test_four_eyes_one_human_plus_one_automated_never_satisfies(
    requester: str, human_approver: str, automated_approver: str
) -> None:
    """One HUMAN approval plus one AUTOMATED approval never satisfies
    Four_Eyes: the automated approval never counts, so at most one distinct
    human approver is ever present, which is short of the required two."""
    request = _make_request(requester)
    approvals = [
        _make_approval(human_approver, PrincipalKind.HUMAN, DECISION_APPROVE),
        _make_approval(automated_approver, PrincipalKind.AUTOMATED, DECISION_APPROVE),
    ]
    assert four_eyes_satisfied(request, approvals) is False


@settings(max_examples=100)
@given(
    requester=st.sampled_from(_NAMES),
    entries=st.lists(
        st.tuples(st.sampled_from(_NAMES), st.sampled_from([DECISION_APPROVE, DECISION_REJECT])),
        max_size=8,
    ),
)
def test_four_eyes_never_satisfied_after_removing_all_human_approvals(
    requester: str, entries: list[tuple[str, str]]
) -> None:
    """Given an arbitrary approval list where every entry is forced to
    AUTOMATED (simulating "all HUMAN approvals removed"), Four_Eyes is never
    satisfied regardless of decisions or names."""
    request = _make_request(requester)
    approvals = [
        _make_approval(approver, PrincipalKind.AUTOMATED, decision)
        for approver, decision in entries
    ]
    assert four_eyes_satisfied(request, approvals) is False


def test_four_eyes_automated_principal_alone_does_not_satisfy() -> None:
    request = _make_request("requester")
    approvals = [_make_approval("bot-1", PrincipalKind.AUTOMATED, DECISION_APPROVE)]
    assert four_eyes_satisfied(request, approvals) is False


# --- Property 16: rejection blocks the transition ---------------------------


# Feature: model-registry, Property 16: Rejection blocks the transition
# For any rejected Promotion_Request, the approval state becomes REJECTED,
# the lifecycle state is unchanged, and a reason is recorded.
# Validates: Requirements 14.5, 20.4, 20.5
@settings(max_examples=100)
@given(
    requester=st.sampled_from(_NAMES),
    approvers=st.lists(st.sampled_from(_NAMES), min_size=2, max_size=6, unique=True),
)
def test_four_eyes_never_satisfied_by_rejections_even_with_enough_distinct_people(
    requester: str, approvers: list[str]
) -> None:
    """approval.py's contribution to Property 16: a decision of "reject" never
    counts toward Four_Eyes, even when there are two or more distinct human
    approvers involved and none of them is the requester — a rejection can
    never accidentally satisfy the gate."""
    non_requester_approvers = [a for a in approvers if a != requester]
    request = _make_request(requester)
    approvals = [
        _make_approval(approver, PrincipalKind.HUMAN, DECISION_REJECT)
        for approver in non_requester_approvers
    ]
    assert four_eyes_satisfied(request, approvals) is False


def test_four_eyes_mixed_reject_then_approve_only_counts_the_approval() -> None:
    """A rejection recorded for one approver does not retroactively count once
    that same approver's decision is captured as approve in a separate
    Approval entry — only entries whose decision is "approve" ever count."""
    request = _make_request("requester")
    approvals = [
        _make_approval("alice", PrincipalKind.HUMAN, DECISION_REJECT),
        _make_approval("bob", PrincipalKind.HUMAN, DECISION_APPROVE),
    ]
    # Only "bob" counts; "alice" rejected, so only one distinct approver -> unsatisfied.
    assert four_eyes_satisfied(request, approvals) is False


def test_four_eyes_all_rejections_never_satisfies() -> None:
    request = _make_request("requester")
    approvals = [
        _make_approval("alice", PrincipalKind.HUMAN, DECISION_REJECT),
        _make_approval("bob", PrincipalKind.HUMAN, DECISION_REJECT),
        _make_approval("carol", PrincipalKind.HUMAN, DECISION_REJECT),
    ]
    assert four_eyes_satisfied(request, approvals) is False
