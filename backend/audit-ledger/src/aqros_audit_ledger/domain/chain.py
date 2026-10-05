"""The tamper-evident hash chain.

CLAUDE.md §7.8: the audit ledger is append-only and tamper-evident by design, and
must never be modified. This module is the mechanism.

How tamper-evidence works
-------------------------
Each entry's hash covers its own content **and** the previous entry's hash::

    hash_n = SHA256(entry_id ‖ recorded_at ‖ event_type ‖ actor ‖ action
                    ‖ resource ‖ outcome ‖ payload ‖ hash_{n-1})

The genesis entry uses a fixed all-zero previous hash. Because every hash
depends on its predecessor, altering or deleting any historical entry changes
every subsequent hash. :func:`verify_chain` recomputes the chain and reports the
first divergence, which is where tampering occurred.

What this does *not* do
-----------------------
A hash chain proves the data has not been silently altered *relative to itself*.
It does not stop an attacker who can rewrite the whole table from recomputing
the chain — that requires the write path to be append-only and the storage to be
WORM (see the ledger's repository and the ADR). The chain is the second line of
defence, not the first.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

#: The previous-hash of the first entry. A fixed, obviously-fake sentinel so a
#: missing genesis link is never confused with a real one.
GENESIS_PREVIOUS_HASH = "0" * 64

#: Fields covered by the hash, in canonical order. Kept as data (not code) so a
#: verifier can be written against it and the two cannot drift.
HASHED_FIELDS: tuple[str, ...] = (
    "entry_id",
    "recorded_at",
    "event_type",
    "actor_id",
    "action",
    "resource",
    "outcome",
    "payload",
)


class Outcome(StrEnum):
    """Whether the audited action was permitted."""

    ALLOWED = "ALLOWED"
    DENIED = "DENIED"
    ERROR = "ERROR"


class ChainVerificationStatus(StrEnum):
    """Result of verifying a range of the chain."""

    VERIFIED = "VERIFIED"
    #: The recomputed hashes diverge from the stored ones.
    TAMPERED = "TAMPERED"
    #: A previous-hash link points at something other than the prior entry.
    BROKEN_LINK = "BROKEN_LINK"
    #: Nothing to verify.
    EMPTY = "EMPTY"


@dataclass(frozen=True)
class AuditEntry:
    """One immutable audit record.

    Frozen on purpose: an entry, once created, cannot be edited in place. The
    only way to "change" history is to append a correcting entry, which is the
    behaviour an audit trail requires.
    """

    entry_id: str
    recorded_at: datetime
    event_type: str
    actor_id: str
    action: str
    resource: str
    outcome: Outcome
    payload: dict[str, Any] = field(default_factory=dict)
    correlation_id: str = ""
    previous_hash: str = GENESIS_PREVIOUS_HASH
    entry_hash: str = ""

    def compute_hash(self) -> str:
        """Compute this entry's hash from its content and its predecessor's.

        The payload is canonicalised with sorted keys and no insignificant
        whitespace, so the same logical payload always produces the same hash
        regardless of insertion order or platform.
        """
        parts = [
            self.entry_id,
            self.recorded_at.isoformat(),
            self.event_type,
            self.actor_id,
            self.action,
            self.resource,
            self.outcome.value,
            canonical_json(self.payload),
            self.correlation_id,
            self.previous_hash,
        ]
        return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()

    def sealed(self) -> AuditEntry:
        """Return a copy with ``entry_hash`` populated."""
        return AuditEntry(
            entry_id=self.entry_id,
            recorded_at=self.recorded_at,
            event_type=self.event_type,
            actor_id=self.actor_id,
            action=self.action,
            resource=self.resource,
            outcome=self.outcome,
            payload=self.payload,
            correlation_id=self.correlation_id,
            previous_hash=self.previous_hash,
            entry_hash=self.compute_hash(),
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "recorded_at": self.recorded_at.isoformat(),
            "event_type": self.event_type,
            "actor_id": self.actor_id,
            "action": self.action,
            "resource": self.resource,
            "outcome": self.outcome.value,
            "payload": self.payload,
            "correlation_id": self.correlation_id,
            "previous_hash": self.previous_hash,
            "entry_hash": self.entry_hash,
        }


def canonical_json(payload: dict[str, Any]) -> str:
    """Serialise a payload deterministically.

    ``sort_keys`` plus fixed separators means the hash of an entry does not
    depend on dict ordering, which would otherwise make verification
    unreproducible across processes.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def chain(previous: AuditEntry | None, draft: AuditEntry) -> AuditEntry:
    """Link a draft entry to its predecessor and seal it.

    Args:
        previous: The last entry in the chain, or None for the genesis entry.
        draft: The entry to seal; its ``previous_hash``/``entry_hash`` are
            overwritten.

    Returns:
        The sealed entry, ready to persist.
    """
    prev_hash = previous.entry_hash if previous is not None else GENESIS_PREVIOUS_HASH
    linked = AuditEntry(
        entry_id=draft.entry_id,
        recorded_at=draft.recorded_at,
        event_type=draft.event_type,
        actor_id=draft.actor_id,
        action=draft.action,
        resource=draft.resource,
        outcome=draft.outcome,
        payload=draft.payload,
        correlation_id=draft.correlation_id,
        previous_hash=prev_hash,
    )
    return linked.sealed()


@dataclass(frozen=True)
class ChainVerification:
    """The outcome of verifying a range of the chain."""

    status: ChainVerificationStatus
    entries_checked: int
    first_invalid_entry_id: str | None = None
    expected_hash: str | None = None
    actual_hash: str | None = None
    detail: str = ""

    @property
    def is_intact(self) -> bool:
        return self.status in (
            ChainVerificationStatus.VERIFIED,
            ChainVerificationStatus.EMPTY,
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "entries_checked": self.entries_checked,
            "first_invalid_entry_id": self.first_invalid_entry_id,
            "expected_hash": self.expected_hash,
            "actual_hash": self.actual_hash,
            "detail": self.detail,
        }


def verify_chain(entries: list[AuditEntry]) -> ChainVerification:
    """Verify a contiguous run of entries.

    Checks, in order:
      1. each entry's stored hash equals its recomputed hash (content integrity);
      2. each entry's ``previous_hash`` equals the prior entry's hash (linkage).

    Reports the *first* failure, since that is where tampering began — later
    mismatches are usually just cascading consequences.

    Entries must be supplied in chain order. An empty list is not an error.
    """
    if not entries:
        return ChainVerification(status=ChainVerificationStatus.EMPTY, entries_checked=0)

    expected_previous = GENESIS_PREVIOUS_HASH
    checked = 0

    for entry in entries:
        # (2) Linkage: does this entry point at the one before it?
        if entry.previous_hash != expected_previous:
            return ChainVerification(
                status=ChainVerificationStatus.BROKEN_LINK,
                entries_checked=checked,
                first_invalid_entry_id=entry.entry_id,
                expected_hash=expected_previous,
                actual_hash=entry.previous_hash,
                detail=(
                    f"entry {entry.entry_id} does not link to its predecessor "
                    "(an entry may be missing or reordered)"
                ),
            )

        # (1) Content integrity: is the entry's own hash still correct?
        recomputed = entry.compute_hash()
        if recomputed != entry.entry_hash:
            return ChainVerification(
                status=ChainVerificationStatus.TAMPERED,
                entries_checked=checked,
                first_invalid_entry_id=entry.entry_id,
                expected_hash=recomputed,
                actual_hash=entry.entry_hash,
                detail=f"entry {entry.entry_id} content has been altered",
            )

        expected_previous = entry.entry_hash
        checked += 1

    return ChainVerification(
        status=ChainVerificationStatus.VERIFIED,
        entries_checked=checked,
        detail=f"verified {checked} entries",
    )
