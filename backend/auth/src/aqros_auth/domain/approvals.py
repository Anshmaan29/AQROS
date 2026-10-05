"""The four-eyes approval workflow.

CLAUDE.md §7.3 and §7.4 forbid the AI from raising its own risk limits or
promoting a model to real capital. This is the state machine that makes those
actions possible *for humans only*, and only with two distinct people.

Design notes that matter:

* **Terminal states are absorbing.** Once rejected or executed, a request can
  never be revived. A withdrawn-then-reused approval id must not resurrect it.
* **Transitions are validated, not assumed.** ``ApprovalRequest.transition``
  refuses illegal moves (e.g. approving an already-executed request) rather than
  trusting the caller.
* **The clock is injected.** No wall-clock time in domain logic
  (CLAUDE.md §5) — approvals expire, and tests must control that.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any


class ApprovalStatus(StrEnum):
    """Lifecycle of a four-eyes approval request."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTED = "EXECUTED"
    EXPIRED = "EXPIRED"
    WITHDRAWN = "WITHDRAWN"


#: States from which no further transition is legal.
TERMINAL_STATUSES: frozenset[ApprovalStatus] = frozenset(
    {
        ApprovalStatus.REJECTED,
        ApprovalStatus.EXECUTED,
        ApprovalStatus.EXPIRED,
        ApprovalStatus.WITHDRAWN,
    }
)

#: Legal transitions.
ALLOWED_TRANSITIONS: dict[ApprovalStatus, frozenset[ApprovalStatus]] = {
    ApprovalStatus.PENDING: frozenset(
        {
            ApprovalStatus.APPROVED,
            ApprovalStatus.REJECTED,
            ApprovalStatus.EXPIRED,
            ApprovalStatus.WITHDRAWN,
        }
    ),
    # Approved but not yet executed: it can still be rejected or expire, so a
    # change of mind before execution is possible.
    ApprovalStatus.APPROVED: frozenset(
        {
            ApprovalStatus.EXECUTED,
            ApprovalStatus.REJECTED,
            ApprovalStatus.EXPIRED,
            ApprovalStatus.WITHDRAWN,
        }
    ),
    ApprovalStatus.REJECTED: frozenset(),
    ApprovalStatus.EXECUTED: frozenset(),
    ApprovalStatus.EXPIRED: frozenset(),
    ApprovalStatus.WITHDRAWN: frozenset(),
}


class InvalidTransitionError(ValueError):
    """Raised when a transition is not legal from the current status."""


class SeparationOfDutiesError(PermissionError):
    """Raised when a principal tries to approve their own request."""


@dataclass(frozen=True)
class ApprovalEvent:
    """One entry in a request's audit trail."""

    from_status: ApprovalStatus | None
    to_status: ApprovalStatus
    actor_id: str
    at: datetime
    reason: str = ""


@dataclass
class ApprovalRequest:
    """A request for a four-eyes-protected action.

    Attributes:
        request_id: Unique identifier (client-supplied for idempotency).
        action: The protected action (e.g. ``"promote_model"``).
        resource: What the action targets (e.g. ``"momentum_v3"``).
        payload: Action-specific arguments, recorded verbatim for audit.
        requester_id: Who asked.
        status: Current lifecycle status.
        approver_id: Who approved, once approved.
        created_at / expires_at: Injected clock timestamps.
        ttl: How long the request stays actionable.
    """

    request_id: str
    action: str
    resource: str
    payload: dict[str, Any]
    requester_id: str
    created_at: datetime
    ttl: timedelta = field(default_factory=lambda: timedelta(hours=24))
    status: ApprovalStatus = ApprovalStatus.PENDING
    approver_id: str | None = None
    decided_at: datetime | None = None
    executed_at: datetime | None = None
    events: list[ApprovalEvent] = field(default_factory=list)

    @property
    def expires_at(self) -> datetime:
        return self.created_at + self.ttl

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def _append(self, to_status: ApprovalStatus, actor_id: str, at: datetime, reason: str) -> None:
        self.events.append(
            ApprovalEvent(
                from_status=self.status,
                to_status=to_status,
                actor_id=actor_id,
                at=at,
                reason=reason,
            )
        )
        self.status = to_status

    def _assert_transition(self, to_status: ApprovalStatus) -> None:
        allowed = ALLOWED_TRANSITIONS[self.status]
        if to_status not in allowed:
            raise InvalidTransitionError(
                f"cannot move from {self.status.value} to {to_status.value}"
            )

    def approve(self, approver_id: str, now: datetime, reason: str = "") -> None:
        """Approve the request.

        Raises:
            SeparationOfDutiesError: if the approver is the requester (§7.3/§7.4).
            InvalidTransitionError: if the request is not pending, or has expired.
        """
        if approver_id == self.requester_id:
            # This is the single most important check in the whole service.
            raise SeparationOfDutiesError(
                "a principal cannot approve their own request (four-eyes requires two people)"
            )

        # Expiry is checked before legality so an expired-but-pending request
        # reports the real problem.
        if now > self.expires_at:
            if self.status is ApprovalStatus.PENDING:
                self._append(ApprovalStatus.EXPIRED, approver_id, now, "expired before approval")
            raise InvalidTransitionError("approval request has expired")

        self._assert_transition(ApprovalStatus.APPROVED)
        self.approver_id = approver_id
        self.decided_at = now
        self._append(ApprovalStatus.APPROVED, approver_id, now, reason)

    def reject(self, actor_id: str, now: datetime, reason: str) -> None:
        """Reject the request. Rejection is final."""
        self._assert_transition(ApprovalStatus.REJECTED)
        self.decided_at = now
        self._append(ApprovalStatus.REJECTED, actor_id, now, reason)

    def withdraw(self, actor_id: str, now: datetime, reason: str = "") -> None:
        """Withdraw the request (typically the requester changing their mind)."""
        self._assert_transition(ApprovalStatus.WITHDRAWN)
        self._append(ApprovalStatus.WITHDRAWN, actor_id, now, reason)

    def execute(self, actor_id: str, now: datetime, reason: str = "") -> None:
        """Execute an approved request.

        Only an APPROVED request may execute — this is what stops an unapproved
        change from reaching the risk kernel.
        """
        self._assert_transition(ApprovalStatus.EXECUTED)
        self.executed_at = now
        self._append(ApprovalStatus.EXECUTED, actor_id, now, reason)

    def expire_if_due(self, now: datetime) -> bool:
        """Expire the request if its TTL has passed. Returns True if expired."""
        if self.is_terminal:
            return False
        if now <= self.expires_at:
            return False
        self._append(ApprovalStatus.EXPIRED, "system", now, "ttl elapsed")
        return True

    def snapshot(self) -> dict[str, Any]:
        """A JSON-safe view for the API."""
        return {
            "request_id": self.request_id,
            "action": self.action,
            "resource": self.resource,
            "payload": self.payload,
            "requester_id": self.requester_id,
            "approver_id": self.approver_id,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
            "executed_at": self.executed_at.isoformat() if self.executed_at else None,
            "history": [
                {
                    "from": e.from_status.value if e.from_status else None,
                    "to": e.to_status.value,
                    "actor_id": e.actor_id,
                    "at": e.at.isoformat(),
                    "reason": e.reason,
                }
                for e in self.events
            ],
        }
