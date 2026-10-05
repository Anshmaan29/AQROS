# ADR 0004 — Portfolio is the authority for positions; Risk reserves in-flight buying power

- **Status:** Accepted
- **Date:** 2026-10-05
- **Affects:** portfolio, risk-engine, oms

## Context

The design docs gave Risk "an in-memory position/exposure book" *and* told it to
read Portfolio for positions, while calling Portfolio "authoritative". Two
sources of truth, with no statement of which wins during a live decision.

That ambiguity hides a real race. Two orders in flight at the same time can each
pass a buying-power check against the same balance:

```
t0  cash = 100,000;  order A checks → 100,000 available → OK
t1  cash = 100,000;  order B checks → 100,000 available → OK   (A not yet filled)
t2  both fill; cash goes negative
```

This is the classic double-spend / TOCTOU race, and it is the difference between
a bad day and an uncontrolled book.

The docs also repeat platform-wide that "every database is a rebuildable
projection of the event log". That is true for research projections and **false
on the money path**: a fill that happened at the venue during an internal outage
cannot be reconstructed by replaying internal events. It can only be recovered by
reconciling against the broker. Treating the venue as log-reconstructable invites
a recovery procedure that loses real money state.

## Decision

**1. Portfolio is the single authoritative position/P&L ledger.**
Risk's in-memory book is an explicitly *derived* cache for latency, never a
second source of truth.

**2. Risk maintains a reservation ledger for in-flight orders.** On approving an
order, Risk atomically *reserves* the cash/exposure it consumes. The reservation
is committed on `orders.filled` and released on reject, cancel, or timeout. Two
concurrent checks therefore see disjoint available balances.

**3. Money-path recovery is replay-then-reconcile.**
Recovery order is: replay the internal event log → reconcile against the broker
→ only then resume accepting orders. The broker is the ultimate authority for
*executed reality*; the internal log is the authority for *internal state
transitions*.

## Consequences

**Positive**

- The double-spend race is closed rather than narrowed.
- Recovery cannot silently invent or lose fills: the broker is consulted before
  trading resumes.
- One authority per fact makes reconciliation bugs diagnosable.

**Negative**

- Reservations must be released on every terminal path, or buying power leaks
  and the system stops trading. This is the main operational risk of this design;
  it needs a sweeper for expired reservations and alerting on reservation age.
- Risk cannot serve a check without either Portfolio reachable or a warm cache.
  Hence: **fail closed** if it cannot verify current state.

**Neutral**

- Replay-then-reconcile is slower to recover than replay-only. Correct trade: the
  delay buys certainty about real money state.

## Alternatives rejected

- **Let the venue's buying power be the check.** Pushes the limit downstream to
  a broker that rejects asynchronously, which is too late to attribute the
  rejection to a specific decision.
- **Serialise all order submission through a single actor.** Stronger than
  reservations but serialises the whole platform behind one process; it becomes
  the throughput ceiling and a single point of failure.