"""The pure ``Approval_Policy`` + ``Four_Eyes`` evaluator (Requirements 12-14, 21).

Governance in the Model Registry is expressed as a small, exhaustively
enumerated *gate table*: every legal lifecycle edge maps to exactly one
``GateKind`` describing what must be satisfied before the transition may be
applied (design.md Key Design Decision 6). The gate table is:

======================================  ======================  ===============
Transition                              Gate                    Source
======================================  ======================  ===============
``REGISTERED → VALIDATED``              ``EVIDENCE``            Requirement 12
``VALIDATED → STAGING``                 ``SINGLE_APPROVAL``     design default
``STAGING → PRODUCTION``                ``FOUR_EYES``           Requirement 14
``PRODUCTION → DEPRECATED``             ``SINGLE_APPROVAL``     Req 16.2 / default
``DEPRECATED → ARCHIVED``               ``SINGLE_APPROVAL``     design default
non-``PRODUCTION`` → ``ARCHIVED``       ``SINGLE_APPROVAL``     Req 11.5 default
``DEPRECATED → PRODUCTION`` (rollback)  ``FOUR_EYES``           Requirement 15
======================================  ======================  ===============

``PRODUCTION → DEPRECATED`` carries a ``SINGLE_APPROVAL`` gate for the *manual*
path; the *automatic* incumbent demotion that accompanies a new PRODUCTION
promotion (Requirement 16.2) is applied by the service without a separate
approval and does not consult this table.

The ``Four_Eyes`` evaluator (``four_eyes_satisfied``) enforces the mandated
PRODUCTION/rollback control (Requirements 14.1, 14.2, 14.6, 14.7, 21.2): at
least two *distinct human* approvers, none of them the requester, each counted
at most once, all approving — and never satisfiable by an automated principal.

This module is pure: no I/O, no framework dependencies, exhaustively
property-testable, and impossible to bypass.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

from aqros_model_registry.domain.lifecycle import IllegalTransitionError
from aqros_model_registry.domain.models import (
    Approval,
    LifecycleState,
    PrincipalKind,
    PromotionRequest,
)

# The decision values carried by an ``Approval`` (design.md Section 4).
DECISION_APPROVE = "approve"
DECISION_REJECT = "reject"

# The number of distinct authorized human approvers a Four_Eyes gate requires
# (Requirement 14.1): two, neither of whom is the requester.
FOUR_EYES_APPROVER_COUNT = 2


class GateKind(StrEnum):
    """What must be satisfied before a lifecycle transition may be applied.

    * ``NONE`` — no gate; the transition applies immediately (Requirement 13.3).
    * ``EVIDENCE`` — ``Validation_Evidence`` must be attached, but no human
      approval is required (Requirement 12).
    * ``SINGLE_APPROVAL`` — one authorized approval is required (design default).
    * ``FOUR_EYES`` — two distinct human approvers, neither the requester
      (Requirements 14, 15.2).
    """

    NONE = "none"
    EVIDENCE = "evidence"
    SINGLE_APPROVAL = "single_approval"
    FOUR_EYES = "four_eyes"


# The gate for every legal non-rollback edge (design.md Key Design Decision 6).
# Abandonment edges (any non-PRODUCTION, non-terminal state -> ARCHIVED) and the
# forward DEPRECATED -> ARCHIVED edge all share the SINGLE_APPROVAL default and
# are generated below to stay in lockstep with ``lifecycle.py``.
_ARCHIVE_EDGES: dict[tuple[LifecycleState, LifecycleState], GateKind] = {
    (state, LifecycleState.ARCHIVED): GateKind.SINGLE_APPROVAL
    for state in LifecycleState
    if state not in (LifecycleState.PRODUCTION, LifecycleState.ARCHIVED)
}

_GATE_TABLE: dict[tuple[LifecycleState, LifecycleState], GateKind] = {
    (LifecycleState.REGISTERED, LifecycleState.VALIDATED): GateKind.EVIDENCE,
    (LifecycleState.VALIDATED, LifecycleState.STAGING): GateKind.SINGLE_APPROVAL,
    (LifecycleState.STAGING, LifecycleState.PRODUCTION): GateKind.FOUR_EYES,
    (LifecycleState.PRODUCTION, LifecycleState.DEPRECATED): GateKind.SINGLE_APPROVAL,
    **_ARCHIVE_EDGES,
}

# The sole rollback edge (Requirement 15.2): re-promoting a previously-PRODUCTION,
# currently-DEPRECATED version to PRODUCTION always demands Four_Eyes.
_ROLLBACK_EDGE: tuple[LifecycleState, LifecycleState] = (
    LifecycleState.DEPRECATED,
    LifecycleState.PRODUCTION,
)


def gate_for(
    from_state: LifecycleState,
    to_state: LifecycleState,
    is_rollback: bool,
) -> GateKind:
    """Resolve the ``GateKind`` governing a lifecycle transition.

    When ``is_rollback`` is ``True`` the only governed edge is
    ``DEPRECATED → PRODUCTION`` and its gate is always ``FOUR_EYES``
    (Requirement 15.2). Otherwise the gate is looked up in the fixed policy
    table (design.md Key Design Decision 6).

    This resolver is intended to be called only for edges the
    ``Lifecycle_State_Machine`` has already deemed legal; any edge with no
    defined gate is not a permitted transition and is surfaced as an
    ``IllegalTransitionError`` (Requirement 11.3), keeping the policy and the
    legality table in lockstep.

    Raises:
        IllegalTransitionError: if the transition has no defined gate because it
            is not a permitted lifecycle edge.
    """
    if is_rollback:
        if (from_state, to_state) == _ROLLBACK_EDGE:
            return GateKind.FOUR_EYES
        raise IllegalTransitionError(from_state, to_state, is_rollback)

    gate = _GATE_TABLE.get((from_state, to_state))
    if gate is None:
        raise IllegalTransitionError(from_state, to_state, is_rollback)
    return gate


def four_eyes_satisfied(
    request: PromotionRequest,
    approvals: Iterable[Approval],
) -> bool:
    """Return ``True`` iff ``approvals`` satisfy the Four_Eyes control.

    The control is satisfied only when at least
    :data:`FOUR_EYES_APPROVER_COUNT` (two) *distinct* approvers meet **all** of
    the following (Requirements 14.1, 14.2, 14.6, 14.7, 21.2):

    * the approver is a human principal (``PrincipalKind.HUMAN``) — any approval
      attributed to a non-human principal does not count, so a PRODUCTION
      promotion can never be satisfied by an automated principal;
    * the approver is different from ``request.requester`` (no self-approval);
    * the approver's decision is ``"approve"``; and
    * each distinct approver is counted at most once, no matter how many times
      they appear in ``approvals``.

    Rejections and any approvals failing the above are simply not counted; this
    function never mutates state and never raises.
    """
    distinct_approvers: set[str] = set()
    for approval in approvals:
        if approval.approver_kind is not PrincipalKind.HUMAN:
            continue
        if approval.decision != DECISION_APPROVE:
            continue
        if approval.approver == request.requester:
            continue
        distinct_approvers.add(approval.approver)

    return len(distinct_approvers) >= FOUR_EYES_APPROVER_COUNT
