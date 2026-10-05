"""In-memory adapters for tests and local development.

These are not toys: they implement the same contracts as the Postgres adapters
(including the four-eyes invariants), so a test that passes here is testing the
real rules. ``InMemoryApprovalRepository`` deliberately shares the same
``ApprovalRequest`` domain object, so the state machine cannot be bypassed.
"""

from __future__ import annotations

from datetime import datetime

from aqros_auth.domain.approvals import ApprovalRequest, ApprovalStatus
from aqros_auth.domain.ports import ApprovalRepository, AuditSink, UserRecord, UserRepository


class InMemoryUserRepository(UserRepository):
    """Dictionary-backed user store."""

    def __init__(self) -> None:
        self._by_username: dict[str, UserRecord] = {}
        self._by_principal: dict[str, UserRecord] = {}

    async def get_by_username(self, username: str) -> UserRecord | None:
        return self._by_username.get(username)

    async def get_by_principal_id(self, principal_id: str) -> UserRecord | None:
        return self._by_principal.get(principal_id)

    async def create(self, record: UserRecord) -> UserRecord:
        if record.username in self._by_username:
            raise ValueError(f"username already exists: {record.username}")
        self._by_username[record.username] = record
        self._by_principal[record.principal_id] = record
        return record

    async def update(self, record: UserRecord) -> UserRecord:
        existing = self._by_principal.get(record.principal_id)
        if existing is None:
            raise ValueError(f"unknown principal: {record.principal_id}")
        if existing.username != record.username:
            self._by_username.pop(existing.username, None)
            self._by_username[record.username] = record
        self._by_principal[record.principal_id] = record
        return record

    async def list_all(self) -> list[UserRecord]:
        return list(self._by_principal.values())

    async def exists(self, username: str) -> bool:
        return username in self._by_username


class InMemoryApprovalRepository(ApprovalRepository):
    """Dictionary-backed approval store, keyed by request id."""

    def __init__(self) -> None:
        self._items: dict[str, ApprovalRequest] = {}

    async def get(self, request_id: str) -> ApprovalRequest | None:
        return self._items.get(request_id)

    async def save(self, request: ApprovalRequest) -> ApprovalRequest:
        self._items[request.request_id] = request
        return request

    async def list_by_status(self, status: ApprovalStatus) -> list[ApprovalRequest]:
        return [r for r in self._items.values() if r.status is status]

    async def list_all(self) -> list[ApprovalRequest]:
        return list(self._items.values())


class RecordingAuditSink(AuditSink):
    """Captures audit events in memory so tests can assert on them."""

    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def record(
        self,
        *,
        event_type: str,
        principal_id: str,
        action: str,
        resource: str,
        outcome: str,
        reason: str = "",
        correlation_id: str = "",
        details: dict[str, object] | None = None,
    ) -> None:
        self.events.append(
            {
                "event_type": event_type,
                "principal_id": principal_id,
                "action": action,
                "resource": resource,
                "outcome": outcome,
                "reason": reason,
                "correlation_id": correlation_id,
                "details": details or {},
                "at": datetime.now().isoformat(),
            }
        )

    def of_type(self, event_type: str) -> list[dict[str, object]]:
        return [e for e in self.events if e["event_type"] == event_type]
