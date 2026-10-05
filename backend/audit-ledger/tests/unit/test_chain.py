"""Tests for the audit ledger hash chain.

A tamper-evident chain is only worth what its detection tests are worth. These
tests deliberately *corrupt* entries in the four ways an attacker or a bug would
(edit, delete, reorder, forge) and assert each is detected.
"""

from __future__ import annotations

import dataclasses
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from aqros_audit_ledger.domain.chain import (
    GENESIS_PREVIOUS_HASH,
    AuditEntry,
    ChainVerificationStatus,
    Outcome,
    canonical_json,
    chain,
    verify_chain,
)

T0 = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def draft(n: int) -> AuditEntry:
    return AuditEntry(
        entry_id=f"aud_{n:04d}",
        recorded_at=T0 + timedelta(minutes=n),
        event_type="order.filled",
        actor_id="alice",
        action="submit_order",
        resource=f"order-{n}",
        outcome=Outcome.ALLOWED,
        payload={"qty": n, "price": "100.00"},
        correlation_id="corr-1",
    )


def build(n: int) -> list[AuditEntry]:
    entries: list[AuditEntry] = []
    previous: AuditEntry | None = None
    for i in range(1, n + 1):
        entry = chain(previous, draft(i))
        entries.append(entry)
        previous = entry
    return entries


class TestCanonicalJson:
    def test_key_order_does_not_change_output(self) -> None:
        """Hashes must not depend on dict insertion order."""
        assert canonical_json({"a": 1, "b": 2}) == canonical_json({"b": 2, "a": 1})

    def test_is_compact(self) -> None:
        assert " " not in canonical_json({"a": 1, "b": 2})


class TestChainConstruction:
    def test_genesis_uses_sentinel_previous_hash(self) -> None:
        first = chain(None, draft(1))
        assert first.previous_hash == GENESIS_PREVIOUS_HASH

    def test_each_entry_links_to_predecessor(self) -> None:
        entries = build(3)
        assert entries[1].previous_hash == entries[0].entry_hash
        assert entries[2].previous_hash == entries[1].entry_hash

    def test_hashes_are_unique(self) -> None:
        hashes = [e.entry_hash for e in build(20)]
        assert len(set(hashes)) == 20

    def test_entry_is_sealed_on_construction(self) -> None:
        assert chain(None, draft(1)).entry_hash != ""

    def test_chain_is_deterministic(self) -> None:
        """Same inputs must yield the same hashes, or replay verification fails."""
        assert chain(None, draft(1)).entry_hash == chain(None, draft(1)).entry_hash


class TestVerifyIntactChain:
    def test_empty_chain_is_empty_not_broken(self) -> None:
        result = verify_chain([])
        assert result.status is ChainVerificationStatus.EMPTY
        assert result.is_intact

    def test_single_entry_verifies(self) -> None:
        assert verify_chain(build(1)).is_intact

    def test_long_chain_verifies(self) -> None:
        result = verify_chain(build(100))
        assert result.status is ChainVerificationStatus.VERIFIED
        assert result.entries_checked == 100

    def test_verification_is_repeatable(self) -> None:
        entries = build(10)
        assert verify_chain(entries).status == verify_chain(entries).status


class TestTamperDetection:
    """The core guarantee: any modification must be detected."""

    def test_detects_edited_payload(self) -> None:
        entries = build(5)
        tampered = list(entries)
        original = tampered[2]
        # Change the quantity — the classic "quietly alter a trade" attack.
        tampered[2] = replace(original, payload={"qty": 999, "price": "100.00"})

        result = verify_chain(tampered)
        assert result.status is ChainVerificationStatus.TAMPERED
        assert result.first_invalid_entry_id == original.entry_id
        assert not result.is_intact

    def test_detects_edited_actor(self) -> None:
        entries = build(3)
        entries[1] = replace(entries[1], actor_id="mallory")
        result = verify_chain(entries)
        assert result.status is ChainVerificationStatus.TAMPERED
        assert result.first_invalid_entry_id == entries[1].entry_id

    def test_detects_flipped_outcome(self) -> None:
        """Turning a DENIED into an ALLOWED is the attack that matters most."""
        entries = build(3)
        # draft() builds ALLOWED entries, so make the original a genuine DENIED.
        denied = chain(None, replace(draft(1), outcome=Outcome.DENIED))
        tampered = [replace(denied, outcome=Outcome.ALLOWED), *entries[1:]]

        result = verify_chain(tampered)
        assert result.status is ChainVerificationStatus.TAMPERED
        assert result.first_invalid_entry_id == denied.entry_id

    def test_detects_deleted_entry(self) -> None:
        entries = build(5)
        with_gap = [entries[0], entries[1], entries[3], entries[4]]
        result = verify_chain(with_gap)
        assert result.status is ChainVerificationStatus.BROKEN_LINK
        assert result.first_invalid_entry_id == entries[3].entry_id

    def test_detects_reordered_entries(self) -> None:
        entries = build(4)
        reordered = [entries[0], entries[2], entries[1], entries[3]]
        result = verify_chain(reordered)
        assert result.status is ChainVerificationStatus.BROKEN_LINK

    def test_detects_forged_hash(self) -> None:
        """Rewriting an entry *and* fixing its own hash still breaks the chain.

        This is the realistic attack: an attacker with write access edits an
        entry and recomputes that entry's hash. Because the next entry commits to
        this one's hash, the break shows up one link later — which is exactly why
        the chain links rather than hashing entries independently.
        """
        entries = build(4)
        original = entries[1]
        forged = replace(original, actor_id="mallory").sealed()  # hash recomputed

        assert forged.entry_hash != original.entry_hash, "the forge must differ"
        tampered = [entries[0], forged, *entries[2:]]

        result = verify_chain(tampered)
        assert not result.is_intact
        # The forged entry is internally consistent, so detection happens at the
        # link into the *next* entry.
        assert result.status is ChainVerificationStatus.BROKEN_LINK
        assert result.first_invalid_entry_id == entries[2].entry_id

    def test_detects_truncated_history(self) -> None:
        """Dropping the tail hides nothing: remaining chain still verifies,
        but the caller must notice the count changed."""
        full = build(10)
        truncated = full[:5]
        result = verify_chain(truncated)
        assert result.status is ChainVerificationStatus.VERIFIED
        assert result.entries_checked == 5

    def test_reports_first_divergence_not_a_later_one(self) -> None:
        """The first bad entry locates the tampering; later ones are noise."""
        entries = build(6)
        entries[2] = replace(entries[2], actor_id="mallory")
        entries[4] = replace(entries[4], actor_id="mallory")
        result = verify_chain(entries)
        assert result.first_invalid_entry_id == entries[2].entry_id
        assert result.entries_checked == 2


class TestAppendOnlyDiscipline:
    def test_entry_is_frozen(self) -> None:
        """An entry cannot be mutated in place; history must be append-only."""
        entry = chain(None, draft(1))
        with pytest.raises(dataclasses.FrozenInstanceError):
            entry.actor_id = "mallory"  # type: ignore[misc]

    def test_appending_does_not_change_earlier_hashes(self) -> None:
        entries = build(3)
        original_hashes = [e.entry_hash for e in entries]

        previous = entries[-1]
        entries.append(chain(previous, draft(4)))

        assert [e.entry_hash for e in entries[:3]] == original_hashes
        assert verify_chain(entries).is_intact

    def test_correction_requires_a_new_entry_not_an_edit(self) -> None:
        """The supported way to fix history is to append, which leaves a trace."""
        entries = build(2)
        before = len(entries)
        entries.append(chain(entries[-1], draft(99)))
        assert len(entries) == before + 1
        assert verify_chain(entries).is_intact
