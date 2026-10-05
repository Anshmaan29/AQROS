"""Postgres repository for the audit ledger.

There is deliberately **no** ``update`` or ``delete`` method. The WORM property
comes from the port having no such operations, so the "never modify the ledger"
rule (CLAUDE.md §7.8) is enforced by the type surface rather than by review.
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aqros_audit_ledger.adapters.orm import AuditEntryModel
from aqros_audit_ledger.domain.chain import AuditEntry, Outcome
from aqros_audit_ledger.domain.service import LedgerRepository


def _row_to_entry(row: AuditEntryModel) -> AuditEntry:
    return AuditEntry(
        entry_id=row.entry_id,
        recorded_at=row.recorded_at,
        event_type=row.event_type,
        actor_id=row.actor_id,
        action=row.action,
        resource=row.resource,
        outcome=Outcome(row.outcome),
        payload=json.loads(row.payload or "{}"),
        correlation_id=row.correlation_id,
        previous_hash=row.previous_hash,
        entry_hash=row.entry_hash,
    )


class SqlAlchemyLedgerRepository(LedgerRepository):
    """Append-only Postgres-backed ledger."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def append(self, entry: AuditEntry) -> AuditEntry:
        """Insert an entry, or return the existing one if the id is already present."""
        async with self._session_factory() as session:
            existing = await session.get(AuditEntryModel, entry.entry_id)
            if existing is not None:
                # Idempotent replay: return what was stored the first time so a
                # retried request cannot fork the chain.
                return _row_to_entry(existing)
            session.add(
                AuditEntryModel(
                    entry_id=entry.entry_id,
                    recorded_at=entry.recorded_at,
                    event_type=entry.event_type,
                    actor_id=entry.actor_id,
                    action=entry.action,
                    resource=entry.resource,
                    outcome=entry.outcome.value,
                    payload=json.dumps(entry.payload, separators=(",", ":"), default=str),
                    correlation_id=entry.correlation_id,
                    previous_hash=entry.previous_hash,
                    entry_hash=entry.entry_hash,
                )
            )
            await session.commit()
        return entry

    async def head(self) -> AuditEntry | None:
        """The most recently appended entry."""
        async with self._session_factory() as session:
            row = await session.scalar(
                select(AuditEntryModel).order_by(AuditEntryModel.sequence.desc()).limit(1)
            )
            return _row_to_entry(row) if row else None

    async def get(self, entry_id: str) -> AuditEntry | None:
        async with self._session_factory() as session:
            row = await session.get(AuditEntryModel, entry_id)
            return _row_to_entry(row) if row else None

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
        async with self._session_factory() as session:
            stmt = select(AuditEntryModel).order_by(AuditEntryModel.sequence)
            if correlation_id is not None:
                stmt = stmt.where(AuditEntryModel.correlation_id == correlation_id)
            if actor_id is not None:
                stmt = stmt.where(AuditEntryModel.actor_id == actor_id)
            if event_type is not None:
                stmt = stmt.where(AuditEntryModel.event_type == event_type)
            if since is not None:
                stmt = stmt.where(AuditEntryModel.recorded_at >= since)
            if until is not None:
                stmt = stmt.where(AuditEntryModel.recorded_at <= until)
            stmt = stmt.limit(limit).offset(offset)
            rows = (await session.scalars(stmt)).all()
            return [_row_to_entry(r) for r in rows]

    async def range_for_verification(
        self, start_id: str | None, end_id: str | None
    ) -> list[AuditEntry]:
        """Return a contiguous, chain-ordered run for verification.

        Ordering is by the monotonic ``sequence``, not by timestamp: two entries
        appended in the same millisecond must still verify in append order.
        """
        async with self._session_factory() as session:
            stmt = select(AuditEntryModel).order_by(AuditEntryModel.sequence)
            if start_id is not None:
                start = await session.get(AuditEntryModel, start_id)
                if start is None:
                    return []
                stmt = stmt.where(AuditEntryModel.sequence >= start.sequence)
            if end_id is not None:
                end = await session.get(AuditEntryModel, end_id)
                if end is None:
                    return []
                stmt = stmt.where(AuditEntryModel.sequence <= end.sequence)
            rows = (await session.scalars(stmt)).all()
            return [_row_to_entry(r) for r in rows]

    async def count(self) -> int:
        async with self._session_factory() as session:
            return int(await session.scalar(func.count(AuditEntryModel.entry_id)) or 0)
