"""Audit ledger HTTP surface.

Deliberately **append, read, and verify only**. There is no update endpoint and
no delete endpoint — not because they are hidden, but because they do not exist
(CLAUDE.md §7.8).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from aqros_audit_ledger.api.schemas import (
    AppendEventRequest,
    AuditEntryResponse,
    LedgerStatsResponse,
    VerificationResponse,
)
from aqros_audit_ledger.domain.chain import Outcome
from aqros_audit_ledger.domain.service import AppendRequest, AuditLedgerService

router = APIRouter(prefix="/v1/audit")


def get_ledger(request: Request) -> AuditLedgerService:
    """Return the configured ledger service."""
    return cast(AuditLedgerService, request.app.state.ledger_service)


LedgerDep = Annotated[AuditLedgerService, Depends(get_ledger)]


@router.post(
    "/events",
    response_model=AuditEntryResponse,
    status_code=201,
    tags=["audit"],
)
async def append_event(payload: AppendEventRequest, ledger: LedgerDep) -> AuditEntryResponse:
    """Append an audit record.

    Idempotent on ``entry_id``: re-sending the same id returns the original
    entry with 200 rather than creating a duplicate.
    """
    existing = None
    if payload.entry_id:
        existing = await ledger.get(payload.entry_id)
        if existing is not None:
            return AuditEntryResponse(**existing.to_record())

    entry = await ledger.append(
        AppendRequest(
            event_type=payload.event_type,
            actor_id=payload.actor_id,
            action=payload.action,
            resource=payload.resource,
            outcome=Outcome(payload.outcome),
            payload=payload.payload,
            correlation_id=payload.correlation_id,
            entry_id=payload.entry_id,
        )
    )
    return AuditEntryResponse(**entry.to_record())


@router.get("/events", response_model=list[AuditEntryResponse], tags=["audit"])
async def query_events(
    ledger: LedgerDep,
    correlation_id: str | None = None,
    actor_id: str | None = None,
    event_type: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[AuditEntryResponse]:
    """Read audit records. Read-only by construction."""
    entries = await ledger.query(
        correlation_id=correlation_id,
        actor_id=actor_id,
        event_type=event_type,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    return [AuditEntryResponse(**e.to_record()) for e in entries]


@router.get("/events/{entry_id}", response_model=AuditEntryResponse, tags=["audit"])
async def get_event(entry_id: str, ledger: LedgerDep) -> AuditEntryResponse:
    """Fetch one audit record."""
    entry = await ledger.get(entry_id)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="audit entry not found")
    return AuditEntryResponse(**entry.to_record())


@router.get("/verify", response_model=VerificationResponse, tags=["audit"])
async def verify_chain(
    ledger: LedgerDep,
    start_id: str | None = None,
    end_id: str | None = None,
) -> VerificationResponse:
    """Verify hash-chain integrity over a range.

    This is the endpoint an auditor (or a scheduled job) calls to prove the
    ledger has not been altered. A non-``VERIFIED`` result is an incident.
    """
    result = await ledger.verify(start_id, end_id)
    return VerificationResponse(**result.to_record())


@router.get("/stats", response_model=LedgerStatsResponse, tags=["audit"])
async def stats(ledger: LedgerDep) -> LedgerStatsResponse:
    """Ledger size and current head hash."""
    total = await ledger.count()
    entries = await ledger.query(limit=1)
    head_hash = entries[0].entry_hash if entries else None
    return LedgerStatsResponse(
        total_entries=total,
        head_hash=head_hash,
        intact=(await ledger.verify()).is_intact,
    )
