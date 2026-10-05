# ADR 0001 — The decision path is synchronous; events are observation only

- **Status:** Accepted
- **Date:** 2026-10-05
- **Affects:** strategy-engine, risk-engine, portfolio, oms, live-trading-engine, api-gateway

## Context

`docs/REMAINING_SERVICES_ARCHITECTURE.md` describes Strategy emitting an
`orders.intended` event *and* Risk exposing a synchronous
`POST /v1/risk/check`. Both were specified; neither was marked authoritative.

If both paths are live, a single intent can be actioned twice — once by the
event consumer and once by the synchronous caller — or out of order. On the money
path that means duplicate orders, which is the one failure the whole design is
built to prevent (CLAUDE.md §5, §7.7).

The architecture review flagged this as 🔴 critical: it is a race that only
appears under concurrency, i.e. in production, not in a demo.

## Decision

**The execution decision path is synchronous request/response only:**

```
Strategy ──gRPC/REST──▶ Risk ──gRPC/REST──▶ OMS ──▶ Broker
```

**Events (`orders.intended`, `signals.generated`, `risk.*`, `positions.*`) are
observability and audit projections. They never trigger execution.**

Concretely:

1. Risk and OMS are called synchronously; neither consumes an event to decide.
2. The gateway refuses to proxy the money path at all (see ADR 0002), so the
   only way to reach Risk is the internal service call.
3. Events remain the source of truth for *internal state transitions* and for
   rebuilding read models, which is why they are still emitted.

## Consequences

**Positive**

- One order intent produces at most one order. The double-execution race is
  structurally impossible rather than guarded against.
- Failures are synchronous and therefore *visible*: an order that was rejected
  is rejected to the caller, not discovered later as a discrepancy.
- Audit and replay keep working, because events still describe what happened.

**Negative**

- The order path inherits the latency of its slowest hop. Accepted: correctness
  beats microseconds, and the design docs place this on an internal network.
- A temporary Risk outage fails *closed* (no order), rather than queueing an
  intent for later. Correct for the money path; alpha-path services degrade
  differently.

**Neutral**

- Consumers that want to know "what orders happened" still subscribe to events.
  They must understand they are observing, not deciding.

## Alternatives rejected

- **Event-driven execution with a single consumer, dedupe on `client_order_id`.**
  Dedupe is a mitigation, not a proof, and it fails open if the dedupe store is
  unavailable. Rejected for the money path.
- **Both paths, with the event path marked "advisory".** "Advisory" was the
  ambiguity that caused the problem; it invites a future PR that makes it
  authoritative. Rejected.