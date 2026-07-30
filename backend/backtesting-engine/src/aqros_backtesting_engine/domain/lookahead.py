"""The Backtesting Engine's look-ahead guard.

Point-in-time correctness (Requirement 4) and the absence of look-ahead
bias (Requirement 5) are structural, non-negotiable properties of the
engine (CLAUDE.md §7.2): no decision made at ``Simulation_Clock`` time ``t``
may use any fact whose ``Knowledge_Time`` is after ``t``. Every component
that reads a fact with a knowledge time — the ``Simulation_Engine`` reading
a ``Bar``, the ``FeatureStoreClient`` boundary, the corporate-action
applier, the fill model — funnels that read through ``assert_knowable``
before using the value.

``assert_knowable`` is a pure function: given a ``knowledge_time`` and the
current simulation ``clock``, it either returns ``None`` (the fact was
knowable) or raises ``LookAheadViolationError`` (it was not). It performs no
I/O and reads no wall-clock time — the only "now" it ever compares against
is the ``clock`` value the caller passes in, which itself comes only from
the injected ``Simulation_Clock`` (Requirement 4.2). A tripped guard fails
the ``Backtest_Run`` with a diagnostic identifying the offending access
rather than silently proceeding (Requirements 4.4, 5.4, 37.3; design.md
Decision 3, Section 8 "Point-in-Time Correctness and Look-Ahead
Prevention").
"""

from __future__ import annotations

from datetime import datetime

__all__ = ["LookAheadViolationError", "assert_knowable"]


class LookAheadViolationError(RuntimeError):
    """Raised when a component attempts to read a fact before it was knowable.

    Carries the offending ``knowledge_time`` and the ``clock`` time it was
    checked against (and optionally a human-readable ``context`` describing
    what was being accessed — e.g. ``"AAPL bar 2024-01-05 close"`` or
    ``"feature aapl_momentum_5d"``) so the diagnostic identifies exactly
    which access violated point-in-time correctness (Requirements 5.4,
    37.3). The caller is expected to fail the ``Backtest_Run`` and record
    this error's message as the human-readable failure reason (Requirement
    37.5).
    """

    def __init__(
        self,
        knowledge_time: datetime,
        clock: datetime,
        context: str | None = None,
    ) -> None:
        self.knowledge_time = knowledge_time
        self.clock = clock
        self.context = context
        location = f" ({context})" if context is not None else ""
        message = (
            f"Look-ahead violation{location}: knowledge_time={knowledge_time.isoformat()} "
            f"is after the current simulation clock={clock.isoformat()}"
        )
        super().__init__(message)


def assert_knowable(
    knowledge_time: datetime,
    clock: datetime,
    *,
    context: str | None = None,
) -> None:
    """Raise ``LookAheadViolationError`` if ``knowledge_time`` is after ``clock``.

    A fact is knowable at ``clock`` time exactly when
    ``knowledge_time <= clock`` (Requirement 4.1, 4.4). Returns ``None`` when
    the fact was knowable; otherwise raises with ``knowledge_time``,
    ``clock``, and ``context`` attached so the diagnostic names the
    offending access (Requirements 5.4, 37.3).

    Pure and deterministic: given the same ``knowledge_time`` and ``clock``,
    this function always produces the same outcome. It performs no I/O and
    reads no wall-clock time.

    Args:
        knowledge_time: the time at which the fact under test could first
            have been known to the platform.
        clock: the current ``Simulation_Clock`` time the caller is deciding
            at.
        context: an optional human-readable description of what was being
            accessed, included in the raised error's diagnostic message.

    Raises:
        LookAheadViolationError: if ``knowledge_time > clock``.
    """
    if knowledge_time > clock:
        raise LookAheadViolationError(knowledge_time, clock, context)
