"""In-memory ledger repository.

Implements the same append-only contract as the Postgres adapter, including
idempotency on ``entry_id``, so tests exercise real ledger behaviour.
"""

from __future__ import annotations

from datetime import datetime

from aqros_audit_ledger.domain.chain import AuditEntry
from aqros_audit_ledger.domain.service import LedgerRepository


class InMemoryLedgerRepository(LedgerRepository):
    """Dictionary-backed ledger, insertion-ordered."""

    def __init__(self) -> None:
        self._entries: dict[str, AuditEntry] = {}

    async def append(self, entry: AuditEntry) -> AuditEntry:
        # Idempotent on entry_id: a retried append returns the original rather
        # than duplicating the record.
        existing = self._entries.get(entry.entry_id)
        if existing is not None:
            return existing
        self._entries[entry.entry_id] = entry
        return entry

    async def head(self) -> AuditEntry | None:
        if not self._entries:
            return None
        return next(reversed(self._entries.values()))

    async def get(self, entry_id: str) -> AuditEntry | None:
        return self._entries.get(entry_id)

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
    ) -> list[AuditEntry]:
        rows = list(self._entries.values())
        if correlation_id is not None:
            rows = [r for r in rows if r.correlation_id == correlation_id]
        if actor_id is not None:
            rows = [r for r in rows if r.actor_id == actor_id]
        if event_type is not None:
            rows = [r for r in rows if r.event_type == event_type]
        if since is not None:
            rows = [r for r in rows if r.recorded_at >= since]
        if until is not None:
            rows = [r for r in rows if r.recorded_at <= until]
        return rows[offset : offset + limit]

    async def range_for_verification(
        self, start_id: str | None, end_id: str | None
    ) -> list[AuditEntry]:
        rows = list(self._entries.values())
        if start_id is not None:
            ids = [r.entry_id for r in rows]
            if start_id not in ids:
                return []
            rows = rows[ids.index(start_id) :]
        if end_id is not None:
            ids = [r.entry_id for r in rows]
            if end_id not in ids:
                return []
            rows = rows[: ids.index(end_id) + 1]
        return rows

    async def count(self) -> int:
        return len(self._entries)
