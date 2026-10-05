"""Append-only ledger service: idempotent append, read, and verify."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from aqros_audit_ledger.domain.chain import (
    AuditEntry,
    ChainVerification,
    Outcome,
    chain,
    verify_chain,
)


class AppendOnlyViolationError(RuntimeError):
    """Raised when something attempts to modify or delete existing history.

    CLAUDE.md §7.8 makes this unconditional: the ledger has no update or delete
    path, so any attempt is a bug or an intrusion and must be loud.
    """


class LedgerRepository(ABC):
    """Storage port for the ledger.

    Note the shape of this interface: there is no ``update`` and no ``delete``.
    That is deliberate — an append-only port makes the WORM guarantee structural
    rather than a rule the implementation has to remember.
    """

    @abstractmethod
    async def append(self, entry: AuditEntry) -> AuditEntry:
        """Append a sealed entry. Must be idempotent on ``entry_id``."""

    @abstractmethod
    async def head(self) -> AuditEntry | None:
        """Return the most recent entry, or None if the ledger is empty."""

    @abstractmethod
    async def get(self, entry_id: str) -> AuditEntry | None: ...

    @abstractmethod
    async def list_entries(
        self,
        *,
        correlation_id: str | None = None,
        actor_id: str | None = None,
        event_type: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEntry]: ...

    @abstractmethod
    async def range_for_verification(
        self, start_id: str | None, end_id: str | None
    ) -> list[AuditEntry]:
        """Return a contiguous run of entries for chain verification."""

    @abstractmethod
    async def count(self) -> int: ...


@dataclass(frozen=True)
class AppendRequest:
    """A request to record one audited action."""

    event_type: str
    actor_id: str
    action: str
    resource: str
    outcome: Outcome
    payload: dict[str, Any]
    correlation_id: str = ""
    #: Client-supplied id for idempotent retries. A retried append with the same
    #: id must not create a second entry — an audit trail with duplicate
    #: records is its own kind of corruption.
    entry_id: str | None = None


class AuditLedgerService:
    """Application service for the WORM audit ledger."""

    def __init__(self, repository: LedgerRepository) -> None:
        self._repository = repository

    async def append(self, request: AppendRequest, now: datetime | None = None) -> AuditEntry:
        """Append one entry, linked to the current head.

        Concurrency: the head is read and linked immediately before the write.
        A single-writer deployment (or serialised transactions) keeps the chain
        linear; the repository's idempotency on ``entry_id`` makes a retry safe.
        """
        recorded_at = now if now is not None else datetime.now(UTC)
        head = await self._repository.head()

        draft = AuditEntry(
            entry_id=request.entry_id or f"aud_{uuid.uuid4().hex}",
            recorded_at=recorded_at,
            event_type=request.event_type,
            actor_id=request.actor_id,
            action=request.action,
            resource=request.resource,
            outcome=request.outcome,
            payload=request.payload,
            correlation_id=request.correlation_id,
        )
        sealed = chain(head, draft)
        return await self._repository.append(sealed)

    async def verify(
        self, start_id: str | None = None, end_id: str | None = None
    ) -> ChainVerification:
        """Verify the chain across a range of entries."""
        entries = await self._repository.range_for_verification(start_id, end_id)
        return verify_chain(entries)

    async def query(
        self,
        *,
        correlation_id: str | None = None,
        actor_id: str | None = None,
        event_type: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEntry]:
        """Read entries. There is intentionally no write-side counterpart."""
        return await self._repository.list_entries(
            correlation_id=correlation_id,
            actor_id=actor_id,
            event_type=event_type,
            since=since,
            until=until,
            limit=limit,
            offset=offset,
        )

    async def get(self, entry_id: str) -> AuditEntry | None:
        return await self._repository.get(entry_id)

    async def count(self) -> int:
        return await self._repository.count()
