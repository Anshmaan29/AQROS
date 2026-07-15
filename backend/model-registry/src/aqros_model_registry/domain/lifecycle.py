"""The pure ``Lifecycle_State_Machine`` for the Model Registry (Requirement 11).

A ``Model_Version`` moves through a strictly-ordered, forward-only lifecycle
with exactly two additional legal edges (design.md Key Design Decision 5):

* the **forward chain**
  ``REGISTERED → VALIDATED → STAGING → PRODUCTION → DEPRECATED → ARCHIVED``
  (Requirement 11.2),
* the **rollback edge** ``DEPRECATED → PRODUCTION``, taken only when
  ``is_rollback`` is set (Requirement 15.1), and
* the **abandonment edge** from any non-``PRODUCTION`` state directly to
  ``ARCHIVED`` (Requirement 11.5).

``ARCHIVED`` is terminal — no transition may leave it (Requirement 11.4) — and
``PRODUCTION`` may never reach ``ARCHIVED`` without first passing through
``DEPRECATED`` (Requirement 11.6). Every other transition is illegal
(Requirement 11.3).

``transition_allowed`` is a single pure function encoding this entire legality
table: no I/O, no framework dependencies, exhaustively property-testable, and
impossible to bypass (design.md Section 10).
"""

from __future__ import annotations

from aqros_model_registry.domain.models import LifecycleState


class IllegalTransitionError(RuntimeError):
    """Raised when a requested lifecycle transition is not a permitted edge.

    Carries the offending ``from_state``/``to_state`` pair and the
    ``is_rollback`` flag so callers can surface a human-readable rejection
    reason (Requirements 11.3, 20.5).
    """

    def __init__(
        self,
        from_state: LifecycleState,
        to_state: LifecycleState,
        is_rollback: bool,
    ) -> None:
        self.from_state = from_state
        self.to_state = to_state
        self.is_rollback = is_rollback
        kind = "rollback" if is_rollback else "transition"
        super().__init__(
            f"Illegal {kind} from {from_state} to {to_state}: " "not a permitted lifecycle edge."
        )


# The forward-only chain (Requirement 11.2). Each pair is the single permitted
# forward successor of a state.
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
# ARCHIVED (Requirement 11.5). PRODUCTION is excluded so it can only reach
# ARCHIVED via DEPRECATED (Requirement 11.6); ARCHIVED is excluded as terminal
# (Requirement 11.4).
_ABANDONMENT_EDGES: frozenset[tuple[LifecycleState, LifecycleState]] = frozenset(
    (state, LifecycleState.ARCHIVED)
    for state in LifecycleState
    if state not in (LifecycleState.PRODUCTION, LifecycleState.ARCHIVED)
)

# Every legal edge that is NOT a rollback.
_FORWARD_AND_ABANDONMENT_EDGES: frozenset[tuple[LifecycleState, LifecycleState]] = (
    _FORWARD_EDGES | _ABANDONMENT_EDGES
)

# The sole rollback edge (Requirement 15.1): a previously-PRODUCTION,
# currently-DEPRECATED version re-promoted to PRODUCTION.
_ROLLBACK_EDGE: tuple[LifecycleState, LifecycleState] = (
    LifecycleState.DEPRECATED,
    LifecycleState.PRODUCTION,
)


def transition_allowed(
    from_state: LifecycleState,
    to_state: LifecycleState,
    is_rollback: bool,
) -> bool:
    """Return ``True`` if the transition is a permitted lifecycle edge, else raise.

    When ``is_rollback`` is ``True``, the only permitted edge is
    ``DEPRECATED → PRODUCTION`` (Requirement 15.1). Otherwise the transition is
    permitted only if it is a forward-chain edge (Requirement 11.2) or an
    abandonment edge from a non-``PRODUCTION`` state to ``ARCHIVED``
    (Requirement 11.5). ``ARCHIVED`` is terminal (Requirement 11.4) and
    ``PRODUCTION`` cannot reach ``ARCHIVED`` without first transitioning to
    ``DEPRECATED`` (Requirement 11.6) — both fall through to the illegal case.

    Raises:
        IllegalTransitionError: for every transition that is not a permitted
            edge (Requirement 11.3).
    """
    if is_rollback:
        if (from_state, to_state) == _ROLLBACK_EDGE:
            return True
        raise IllegalTransitionError(from_state, to_state, is_rollback)

    if (from_state, to_state) in _FORWARD_AND_ABANDONMENT_EDGES:
        return True

    raise IllegalTransitionError(from_state, to_state, is_rollback)
