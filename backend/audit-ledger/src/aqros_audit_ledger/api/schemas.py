"""Request/response schemas for the audit ledger."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AppendEventRequest(BaseModel):
    """Record one audited action.

    ``entry_id`` is optional but should be supplied by any caller that retries;
    it makes the append idempotent so a network retry cannot duplicate a record.
    """

    event_type: str = Field(..., min_length=1, max_length=128)
    actor_id: str = Field(..., min_length=1, max_length=128)
    action: str = Field(..., min_length=1, max_length=128)
    resource: str = Field(..., min_length=1, max_length=256)
    outcome: str = Field(..., pattern="^(ALLOWED|DENIED|ERROR)$")
    payload: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str = Field(default="", max_length=128)
    entry_id: str | None = Field(default=None, max_length=64)


class AuditEntryResponse(BaseModel):
    """One stored, hash-sealed audit entry."""

    entry_id: str
    recorded_at: datetime
    event_type: str
    actor_id: str
    action: str
    resource: str
    outcome: str
    payload: dict[str, Any]
    correlation_id: str
    previous_hash: str
    entry_hash: str


class VerificationResponse(BaseModel):
    """The result of verifying a range of the hash chain."""

    status: str
    entries_checked: int
    first_invalid_entry_id: str | None = None
    expected_hash: str | None = None
    actual_hash: str | None = None
    detail: str = ""


class LedgerStatsResponse(BaseModel):
    """Ledger size and head hash, for operational dashboards."""

    total_entries: int
    head_hash: str | None
    intact: bool
