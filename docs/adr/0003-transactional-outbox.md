# ADR 0003 — Events are published through a transactional outbox

- **Status:** Accepted
- **Date:** 2026-10-05
- **Affects:** libs/aqros-outbox, every event-producing service

## Context

A service owns a Postgres database *and* must publish events. The naive
implementation writes the database, then publishes to the bus:

```python
await repo.save(order)      # committed
await bus.publish(event)    # crashes here → event lost forever
```

A crash between the two loses the event. A retry can publish it twice. This is
the classic dual-write problem, and the architecture review predicted it would
become expensive to retrofit.

While implementing this, the library was found to have **already made the bug
unavoidable**: `SqlAlchemyOutboxRepository.add()` opened its own session and
committed, so there was no API by which an event could be written inside the
caller's transaction. The module docstring promised "durably stored in the same
transaction" that the API could not deliver.

A second, independent bug: `claim_ready()` only selected `PENDING`/`FAILED` rows
and ignored its `claim_timeout_seconds` argument. A dispatcher that died
mid-dispatch left rows in `PROCESSING` forever — silently lost events, forever.
On the order path that means a lost `orders.filled`.

## Decision

**Events are written to an `outbox` table inside the same transaction as the
state change, then relayed to the bus by a background dispatcher.**

- `repo.stage(session, event)` enlists a **caller-owned** session and does not
  commit. The caller's `session.commit()` makes state and event durable
  together. This is the transactional-outbox guarantee.
- `repo.add(event)` remains for events *not* coupled to a local state change,
  and its docstring says so explicitly.
- Consumers dedupe on `event_id` (ULID). Delivery is at-least-once; exactly-once
  is not claimed, because it is not achievable without transactional
  coordination across the bus and the database.
- `claim_ready()` reclaims rows stuck in `PROCESSING` for longer than
  `claim_timeout_seconds`, so a crashed dispatcher cannot strand an event.

## Consequences

**Positive**

- An event cannot be lost to a crash between commit and publish, because they
  are one commit.
- A crash *after* commit cannot lose the event: the dispatcher picks it up on
  restart.
- Duplicate delivery is possible and expected, and is safe because consumers
  dedupe.

**Negative**

- Every event-producing write path must remember to call `stage()`. A future
  developer who calls `bus.publish()` directly reintroduces the bug silently.
  Mitigated by the port exposing `stage` as the obvious choice and by the
  outbox being wired in `app.py` for every service.
- Reads-your-writes: an event is visible to the dispatcher only after the
  transaction commits. Correct, and inherent to the pattern.
- The outbox table grows. Handled by `delete_processed(retention_hours)`.

**Neutral**

- The dispatcher is an extra moving part per service, and a second thing to
  monitor. `OutboxMetrics` exposes pending/processing/failed/dead-letter counts.

## Alternatives rejected

- **Publish to the bus first, then write the database.** Inverts the loss window
  to "event published, state lost", which is worse: subscribers act on something
  that never happened.
- **CDC / logical replication.** A better answer at scale, and probably the V2
  choice. Deferred because it adds Postgres-specific operational machinery before
  there is a Kafka deployment to consume from.
- **At-most-once via fire-and-forget.** Unacceptable for audit records, which are
  the whole point of the ledger.