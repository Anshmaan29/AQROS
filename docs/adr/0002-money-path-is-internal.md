# ADR 0002 — The money path is internal-only; the gateway refuses to proxy it

- **Status:** Accepted
- **Date:** 2026-10-05
- **Affects:** api-gateway, risk-engine, portfolio, oms, live-trading-engine, audit-ledger

## Context

`api-gateway` is the single north-south ingress: it handles auth handoff,
authorization, rate limiting, and audit for everything reaching the platform.

The design docs list the gateway as the ingress for public APIs, and separately
require the trading hot path (Strategy → Risk → OMS → Broker) to be internal
gRPC/in-process only, "never exposed through the gateway". That was a stated
intent with no enforcement, so a well-meaning route addition could have put
`/v1/oms/...` on the public edge and bypassed auth, rate limits, and audit
entirely.

## Decision

**Exposure is a property of the service, not of the route.**

`backend/api-gateway/src/aqros_api_gateway/domain/topology.py` declares each
service as `Exposure.PUBLIC` or `Exposure.INTERNAL`. The proxy route refuses to
forward to any `INTERNAL` service.

The money-path services are `INTERNAL`: `risk-engine`, `portfolio`, `oms`,
`live-trading-engine`, `paper-trading-engine`, `parity-monitor`,
`audit-ledger`.

Two supporting decisions:

1. **Refusal returns 404, not 403.** A 403 confirms the service exists, turning
   the public edge into an internal-service enumeration oracle.
2. **Compose binds them to loopback** (`127.0.0.1:8005:8005`, not
   `0.0.0.0`). Defence in depth: even a mistake in the gateway cannot publish
   them to the host network.

## Consequences

**Positive**

- Reaching the money path from outside requires editing the topology, which is a
  reviewed, tested change — not an accident in a route table.
- Public surface shrinks to research and control surfaces, which is what an
  operator actually needs.
- Enforced twice, independently (gateway policy + compose binding), so neither
  layer is a single point of failure.

**Negative**

- An operator debugging a rejected trade cannot curl the gateway at it; they use
  an internal session or the audit ledger. Accepted: this is deliberate friction
  on exactly the operations that must be controlled.
- Internal services need their own observability. Mitigated by `/metrics` and
  health endpoints on every service, reachable inside the compose network.

**Neutral**

- The gateway knows the full topology, so it can report it (`/v1/topology`),
  including which services are internal. This is information an operator wants;
  the route still refuses to proxy to it.

## Alternatives rejected

- **Authenticate-and-allow the money path through the gateway.** Permissible in
  principle for a control surface, but it makes the gateway a required hop for
  every pre-trade check — adding latency and a failure mode to the order path
  for no benefit. Revisit only if a human-facing order console needs it, and then
  per-route with its own authz.
- **Rely on the internal Docker network alone.** Defense in depth is not
  redundant; a container that is accidentally published, or a flat network, is
  exactly the case where one layer is not enough.