# ADR 0006 — The audit ledger is append-only by construction, and hash-chained

- **Status:** Accepted
- **Date:** 2026-10-05
- **Affects:** audit-ledger, auth, every service that audits decisions

## Context

CLAUDE.md §7.8 says the WORM audit ledger must never be modified. The review
correctly noted that "fire-and-forget audit append" loses records, which is
unacceptable for a ledger — an audit trail that can silently drop entries is
worse than none, because it invites false confidence.

The review also flagged that nothing enforced §7.8: it was a sentence in a
document, not a property of the code.

## Decision

**Append-only is structural, hash-chained, and enforced at the database.**

Three independent layers, so no single mistake voids the guarantee:

1. **The repository port has no `update` and no `delete`.**
   `LedgerRepository` exposes only `append`, `get`, `head`, `list_entries`,
   `range_for_verification`, and `count`. WORM is a property of the type surface,
   not a convention a reviewer must remember. A test asserts the exact method
   set, so adding a mutator fails CI.

2. **Entries are hash-chained.**
   `hash_n = SHA256(entry_id ‖ recorded_at ‖ event_type ‖ actor ‖ action ‖
   resource ‖ outcome ‖ canonical_payload ‖ correlation_id ‖ hash_{n-1})`.
   The genesis entry uses a fixed all-zero previous hash. Payloads are
   canonicalised (sorted keys, fixed separators) so a hash never depends on dict
   ordering.

   Verification reports the **first** divergence, which is where tampering began;
   later mismatches are usually cascading. `verify_chain` distinguishes
   `TAMPERED` (content altered) from `BROKEN_LINK` (an entry missing or
   reordered), because those indicate different faults.

3. **A database trigger rejects UPDATE and DELETE outright.**
   `trg_audit_entries_append_only` raises on any mutation, regardless of which
   role issues it. This is the last line of defence against a compromised
   service account or a direct `psql` session, neither of which can be prevented
   by application code alone.

Additionally: appends are **idempotent on `entry_id`**, so a retried request
cannot fork the chain or create a duplicate record.

## Consequences

**Positive**

- Silent alteration of history is detectable, and detection is a single endpoint
  (`GET /v1/audit/verify`) suitable for a scheduled job.
- No code path exists to rewrite history, so "we would never do that" is backed
  by the type system and the database.
- Idempotent appends mean an at-least-once event stream produces a clean ledger.

**Negative**

- A hash chain proves the data is unaltered *relative to itself*. An attacker who
  can rewrite the whole table can recompute the entire chain. That is precisely
  why layers 1 and 3 exist — the chain is defence in depth, not the only defence.
- No deletion means no GDPR-style erasure. Correct for a trading audit trail,
  but it must be reconciled with privacy obligations deliberately, not by
  accident.
- Verification is O(n) over the range. Fine at current scale; a very large ledger
  needs periodic checkpointing of intermediate hashes.

**Neutral**

- Entries carry a monotonic `sequence`, independent of wall-clock time, so chain
  order survives two appends in the same millisecond.

## Alternatives rejected

- **Plain append-only table with no hash chain.** Satisfies WORM but cannot
  *prove* integrity after a privileged database edit. Rejected as insufficient.
- **Blockchain / Merkle tree.** Stronger properties, far more machinery, and no
  additional guarantee against an attacker who controls the writer. Rejected.
- **Object-lock (WORM) bucket as the primary store.** A good durability and
  retention control, and MinIO/S3 object-lock should be applied to the ledger
  bucket as well. Kept as a storage-tier control, not as a substitute for the
  chain.