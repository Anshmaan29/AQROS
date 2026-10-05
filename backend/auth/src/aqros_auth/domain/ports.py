"""Ports for the auth service — the interfaces adapters implement.

Domain code depends only on these abstractions (ports-and-adapters,
CLAUDE.md §3), which is what lets the real Postgres repositories be swapped for
in-memory fakes in tests without touching business logic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from aqros_auth.domain.approvals import ApprovalRequest, ApprovalStatus
from aqros_auth.domain.policy import Principal, Role


class UserRepository(ABC):
    """Persistence for human operators and service principals."""

    @abstractmethod
    async def get_by_username(self, username: str) -> UserRecord | None: ...

    @abstractmethod
    async def get_by_principal_id(self, principal_id: str) -> UserRecord | None: ...

    @abstractmethod
    async def create(self, record: UserRecord) -> UserRecord: ...

    @abstractmethod
    async def update(self, record: UserRecord) -> UserRecord: ...

    @abstractmethod
    async def list_all(self) -> list[UserRecord]: ...

    @abstractmethod
    async def exists(self, username: str) -> bool: ...


class ApprovalRepository(ABC):
    """Persistence for four-eyes approval requests."""

    @abstractmethod
    async def get(self, request_id: str) -> ApprovalRequest | None: ...

    @abstractmethod
    async def save(self, request: ApprovalRequest) -> ApprovalRequest: ...

    @abstractmethod
    async def list_by_status(self, status: ApprovalStatus) -> list[ApprovalRequest]: ...

    @abstractmethod
    async def list_all(self) -> list[ApprovalRequest]: ...


class AuditSink(ABC):
    """Where authorization and approval decisions are recorded.

    Separate from the ledger service on purpose: auth must not fail closed
    merely because the audit service is down, or a ledger outage would take the
    whole platform offline. Implementations should queue and retry.
    """

    @abstractmethod
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
        details: dict[str, Any] | None = None,
    ) -> None: ...


class UserRecord:
    """Stored user record.

    A concrete class rather than a Protocol so the ORM adapter has one obvious
    shape to map to and mypy can enforce it.
    """

    def __init__(
        self,
        *,
        principal_id: str,
        username: str,
        display_name: str,
        password_hash: str,
        roles: list[Role],
        is_service: bool = False,
        is_active: bool = True,
        created_at: datetime | None = None,
        last_login_at: datetime | None = None,
    ) -> None:
        self.principal_id = principal_id
        self.username = username
        self.display_name = display_name
        self.password_hash = password_hash
        self.roles = roles
        self.is_service = is_service
        self.is_active = is_active
        self.created_at = created_at
        self.last_login_at = last_login_at

    def to_principal(self) -> Principal:
        return Principal(
            principal_id=self.principal_id,
            display_name=self.display_name,
            roles=frozenset(self.roles),
            is_service=self.is_service,
            is_active=self.is_active,
        )
