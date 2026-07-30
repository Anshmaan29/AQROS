# AQROS — Production Architecture Review (Pre-Implementation Gate)

> **Reviewer:** Principal Software Architect. **Scope:** entire platform — 4 completed services, all specs, shared libs, infra, and the Remaining Services Architecture doc. **Mandate:** make AQROS architecturally comparable to a quant firm's platform *before* the remaining services are built. **This document is findings only — no code was modified.**
>
> **Verdict up front:** the *foundation is genuinely strong* — clean ports-and-adapters, disciplined per-service DB ownership, bitemporal correctness, honest testing on the completed services, and a coherent forward architecture. But there are **structural gaps that must close before any money-path service is built**, and several claims in the forward architecture that are dangerous if taken literally on the money path. Details below, severity-bucketed in Phase 8.

---

## Phase 1 — Repository Audit (findings)

### 1.1 What is actually true today (verified on disk)

| Dimension | State | Assessment |
|---|---|---|
| Service skeleton | 4 services, identical `domain/adapters/api/config/app/main` + `migrations/` | ✅ Consistent, exemplary |
| Shared libs | **only `aqros-core`** (`app`, `config`, `health`, `logging`) | ⚠️ Missing event/clock/secrets/metrics/db/http primitives |
| Cross-service comms | **REST-over-httpx only**; no event bus anywhere | ⚠️ Matches MVP intent, but no `aqros-events` interface exists yet |
| DB ownership | one Postgres per service, no shared DB | ✅ Hard Rule §7.9 honored |
| Object storage | **none** — artifacts on local-disk volumes behind a port | 🔴 No durability; see 7.x |
| Event backbone | **none** (compose comment: "Redis/Kafka later") | ⚠️ Expected for MVP; retrofit risk (see Phase 5) |
| Specs | **only `training-pipeline`** | 🔴 3 of 4 services have zero spec/traceability |
| ADRs | **`docs/adr/` does not exist** | 🔴 Contradicts `CLAUDE.md §5` |
| CI gates | ruff + black + mypy + pytest (one job) | ⚠️ No SBOM/CVE/secret-scan/golden-replay/sign |
| Docker | shared `Dockerfile.service`, non-root, tag-pinned (not digest), not distroless/read-only | ⚠️ Supply-chain hardening missing |
| Health | `/health`,`/live`,`/ready` via `aqros-core`; readiness = DB ping + upstream reachability | ⚠️ Cascading-readiness risk (1.6) |
| Typecheck | auto-discovers `aqros_*` packages | ✅ New services auto-covered |

### 1.2 Architecture consistency — **PASS with caveats**
The four services are strikingly consistent (this is the platform's biggest strength). The risk is *drift as the team grows*: consistency is currently maintained by copy-paste, not by shared code. Copy-paste is itself a finding (1.3, Phase 4).

### 1.3 Ports/adapters correctness — **PASS**, but duplicated plumbing
`domain/` is genuinely pure across all four; I/O is at the edges. **However**, four near-identical copies now exist of: `adapters/db.py` (engine/session/ping), the httpx upstream-client pattern (feature-store→market-data, dataset-builder→2 upstreams, training-pipeline→dataset-builder), and the health/readiness wiring. This is *convention duplication* that will diverge. → **fold into `aqros-core`** (Phase 4).

### 1.4 Event flow — **NOT IMPLEMENTED**
There is no event emission anywhere today. Every completed service is a synchronous REST client of its upstream. The forward architecture's entire event catalog (§0.5 of the Remaining doc) is *net-new* and must be **retrofitted into the four existing services** (they must start emitting `market.bars.*`, `models.trained`, etc.). This retrofit is not currently scoped anywhere. → **Finding:** the event backbone is not just "add Kafka" — it's "add event production to four services that have none," which touches their `domain`/`adapters`. Must be a planned milestone (see Phase 3 / roadmap).

### 1.5 Naming — **PASS**
Services are nouns, hyphenated dirs, snake_case modules, `/v1` APIs, past-tense event names in the doc. Minor: the doc uses "Risk Management" / "risk-engine" / `aqros_risk_engine` interchangeably — fine, but pin the canonical trio (dir `risk-engine`, module `aqros_risk_engine`, display "Risk Management") in each spec to avoid drift.

### 1.6 Health endpoints — **cascading-readiness risk**
feature-store/dataset-builder/training-pipeline register readiness checks that ping *upstream* services. If market-data is briefly down, feature-store reports **not-ready**, which (under K8s/LB) removes it from rotation, which cascades. → **Finding:** distinguish *liveness* (self), *readiness* (self + own DB), and *dependency health* (reported but **non-fatal** for readiness). Upstream reachability should degrade behavior + alert, not fail readiness. This matters more once these sit behind K8s. **Files:** each service's `app.py` health registration; ideally standardized in `aqros-core.health` with a `critical: bool` flag per check.

### 1.7 Configuration consistency — **one real footgun**
`.env.example` defines a single `AQROS_DATABASE_URL` described as "read by whichever service you're running." In compose this is overridden per-container (fine), but for local multi-service dev it's a genuine trap (run two services, they collide on one DB). → **Finding:** either per-service env files are documented as mandatory, or `aqros-core` derives `database_url` from `service_name` + a base DSN. Low severity, high annoyance.

### 1.8 Versioning / API conventions — **PASS**
`/v1` URL versioning, typed Pydantic schemas, `ErrorResponse` envelope, OpenAPI. Missing: a documented **deprecation policy** and an **API changelog** convention (needed once external/UI clients exist).

### 1.9 Workspace / migration strategy — **PASS**
uv workspace, Alembic per DB-owning service, forward-only. Good. Missing: a **migration test in CI** that runs `alembic upgrade head` + `downgrade base` against a container for every service (training-pipeline has one; make it a universal gate).

---

## Phase 2 — Existing Specs Review

**Only `training-pipeline` has a spec.** This is the dominant Phase-2 finding.

### 2.1 🔴 Three completed services have no spec (traceability + reproducibility gap)
market-data, feature-store, dataset-builder were built with **no `requirements.md`/`design.md`/`tasks.md`**. There is therefore no traceable record of *why* they are shaped as they are, no acceptance criteria to verify against, and no design to onboard against. For a platform whose entire thesis is *reproducibility and auditability*, the engineering process itself is unreproducible for 75% of the built system.
→ **Recommendation (high):** retroactively author at minimum a `design.md` (and a short ADR set) for each of the three services capturing: responsibilities, ports, DB schema, event contracts (once added), and key decisions. Full `requirements.md` is ideal but the design + ADRs are the must-have. **Files to create:** `.kiro/specs/{market-data,feature-store,dataset-builder}/design.md` + `docs/adr/*`.

### 2.2 training-pipeline spec — internally consistent, minor items
I reviewed it deeply (it's traceable: 26 numbered correctness properties mapped to requirements, both user-requested corrections reflected in code). Issues:
- **Synchronous training in the request cycle (Key Decision 7).** Fine for MVP, but a long training run holds an HTTP connection open with no async job/queue. → *Scalability finding:* V1 needs an async job model (this is exactly what Scheduling & Retraining should own). Flag in the spec as a known V1 evolution.
- **Hyperparameter default `penalty="l2"`** emits a sklearn FutureWarning (deprecated → removal in 1.10). Spec-mandated, but revisit before the sklearn bump.
- **No spec-level statement of event emission** (`models.trained`) — because events don't exist yet. Add when the backbone lands.

### 2.3 Cross-spec: no shared "platform conventions" spec
Each spec re-derives conventions (health, DB, config, testing). → **Recommendation (medium):** a single `docs/PLATFORM_CONVENTIONS.md` (or ADR) that specs reference, so they stop restating boilerplate and can't contradict each other.

---

## Phase 3 — Remaining Services Architecture Review (challenging my own doc)

I wrote the Remaining Services doc; here I attack it honestly. It is directionally sound but has **five material issues** and several gaps.

### 3.1 🔴 Money-path decision flow: dual sync/async path is a race hazard
The doc has Strategy emit `orders.intended` (async event) **and** Risk expose synchronous `POST /v1/risk/check` (gRPC). It never states which is authoritative. If both are live, an intent could be actioned twice (once via the event consumer, once via the sync call) or out of order.
→ **Fix:** state explicitly that **the execution decision path is synchronous request/response only** (`Strategy →(gRPC)→ Risk →(gRPC)→ OMS`), per Blueprint §5's "trading hot path is internal gRPC/in-process." `orders.intended`/`signals.generated`/`risk.*` **events are observability + audit projections only — never execution triggers.** **File:** `docs/REMAINING_SERVICES_ARCHITECTURE.md` §3, §5, §7 (add an explicit "decision path vs observation path" statement).

### 3.2 🔴 Position ownership ambiguity: Risk in-memory book vs Portfolio ledger
Risk "maintains an in-memory position/exposure book" *and* reads Portfolio via `PositionBookSource`, while Portfolio is called "authoritative." Who wins during a live decision? And what prevents two in-flight orders from both passing a buying-power check against the same balance (a classic double-spend / TOCTOU race)?
→ **Fix:** (a) **Portfolio is the single authoritative position/P&L ledger.** (b) Risk maintains a *derived* fast projection **plus a pre-trade reservation ledger**: on approve, Risk atomically **reserves** buying power/exposure for the in-flight order; the reservation is committed on `orders.filled` or released on reject/cancel/timeout. This closes the in-flight double-spend race the doc currently ignores. **File:** §5 (add "buying-power reservation" to Risk responsibilities + a `reservations` table).

### 3.3 🔴 "Every DB is a rebuildable projection of Kafka" is false on the money path
The doc repeats this platform-wide. It is true for research/feature projections; it is **dangerous for orders/fills**. An order sent to a broker is a real-world side effect; a fill that happened at the venue during an internal outage **cannot be reconstructed by replaying internal Kafka** — it can only be recovered by **broker reconciliation**. Treating the venue as reconstructable-from-log invites a recovery procedure that loses real money state.
→ **Fix:** state the authority hierarchy explicitly: **for executed reality, the broker/venue is the ultimate source of truth; the internal event log is the source of truth for internal state transitions; recovery of the order/position state = replay internal log *then* reconcile against broker before resuming.** **File:** §0.4, §7 (OMS), §15 (DR).

### 3.4 🔴 Reliable event publishing: dual-write problem unaddressed (transactional outbox)
Services own a Postgres DB *and* must publish events. The naive "write DB, then publish to Kafka" loses events on a crash between the two (or double-publishes). The doc's event catalog assumes reliable delivery but specifies no mechanism.
→ **Fix:** mandate the **transactional outbox pattern** in `libs/aqros-events`: events are written to an `outbox` table in the *same DB transaction* as the state change; a relay publishes them to Kafka at-least-once with the event's ULID as the idempotency key; consumers dedupe. Critical for `orders.*`, `risk.*`, and **every audit event** (the doc's "fire-and-forget audit append" would lose audit records — unacceptable for a WORM ledger). **File:** §0.4 + Audit Ledger §10 + Appendix A.

### 3.5 🟠 Event ordering / partitioning unspecified
Kafka guarantees ordering only within a partition. The doc never states partition keys. Fills, positions, and risk decisions must be causally ordered **per account/instrument**.
→ **Fix:** specify partition-by-`account_id` (or `account:instrument`) for `orders.*`/`positions.*`/`pnl.*`/`risk.*`; market data by symbol shard. **File:** §0.4.

### 3.6 🟠 Missing services / ownership gaps in the 15
- **Auth / Identity / Control-Plane is never designed as an owner**, yet "four-eyes," RBAC, promote/demote, and kill-switch arming are referenced across Registry, Risk, and Notification. Four-eyes is currently *hand-waved in three places with no single authority*. → **Add a design** for an Auth + Control-Plane service (OIDC/mTLS/OPA/RBAC/four-eyes workflow engine). This is a **V1 blocker** — you cannot arm capital without it. It sits at the reserved `:8001` (auth) + a control-plane surface behind the gateway `:8000`.
- **Reference data / Security Master ownership is ambiguous.** Blueprint §4.1 calls the security master "the root entity every dataset joins to," but no service owns it. Market Data implicitly? → **Assign explicit ownership** (likely Market Data or a dedicated reference-data service) and state the bitemporal identifier crosswalk lives there.
- **Inference Service** (Blueprint §3.3, V1 GPU serving) is folded into Strategy via `inference_client` with no section. Acceptable for small/CPU models in early V1, but → **state the deferral explicitly** and the trigger for splitting it out (model size/latency/GPU).
- **Schema Registry** for events is mentioned but unowned/undeployed. → assign to the platform/Deployment concern with a concrete tool (e.g., Redpanda schema registry).

### 3.7 🟠 Recovery / startup ordering underspecified platform-wide
OMS reconcile-before-accept is stated; the *platform* startup contract is not. Risk must rebuild its book + reservations before accepting checks; Portfolio must reconcile before serving authoritative positions. Compose uses `depends_on: service_started` (not `service_healthy`) for several deps — "started" ≠ "ready to serve."
→ **Fix:** every money-path service must **gate readiness on reconciliation/rebuild completion** (report not-ready until its book is rebuilt and broker-reconciled). **File:** §5, §7, §4 + compose `condition: service_healthy`.

### 3.8 🟠 Backpressure & clock discipline
- **Backpressure:** KEDA-on-lag scales consumers but nothing bounds producers (market-data flood → strategy). Specify bounded queues + drop/aggregate policy on the alpha path (never on the order path). 
- **Clock:** the injected `Clock` gives determinism (good), but live cross-service wall-clock discipline (NTP/PTP, monotonic sequence numbers for event ordering, and the `event_time` vs `knowledge_time` assignment authority at ingest) is unspecified. → add a clock/sequencing statement. **File:** §0.4.

### 3.9 Positives worth preserving
The shared-core invariant (§0.3), the fail-closed-money/fail-open-alpha split, the trust ladder, the sovereign kernel purity, per-service DBs, and the dependency ordering are all correct and should not be diluted. The dependency graph has **no hard runtime cycles** (Registry↔Backtesting is async-event-decoupled; the runtime request path Strategy→Risk→OMS→Broker is acyclic).

---

## Phase 4 — Shared Libraries Review

**Keep the three proposed, extend `aqros-core`, add exactly one more. Reject the rest.**

### 4.1 `aqros-core` — **extend it (this removes real, verified duplication)**
Today it's `app/config/health/logging`. Fold in the plumbing currently copy-pasted across all four services:
- **`aqros_core.db`** — `create_engine/create_session_factory/session_scope/ping` (currently duplicated 4×; I copied it myself for training-pipeline). *Verified duplication — justified.*
- **`aqros_core.http`** — a resilient async HTTP client with **timeout + idempotent retry + circuit breaker + correlation-ID propagation** (every service hand-rolls httpx with ad-hoc error handling; Blueprint §5/§15 mandate uniform timeout/retry/CB on *every* external call — currently not uniform). *Verified duplication + a correctness requirement — justified.*
- **`aqros_core.clock`** — the injected `Clock` port (determinism is a Hard Rule; it belongs in shared code, not re-invented per service).
- **`aqros_core.secrets`** — `SecretsClient` port with `EnvSecrets`/`VaultSecrets` adapters (Phase 6).
- **`aqros_core.observability`** — Prometheus `/metrics` endpoint + OTel tracing exporter + correlation-ID middleware (Phase 7 — currently absent everywhere).
- **`aqros_core.health`** — add a `critical: bool` per check (fixes 1.6).

### 4.2 `aqros-events` — **yes, and it must include the outbox**
Not just `EventBus` + `InProcess`/`Kafka` adapters + versioned envelope, but the **transactional outbox relay** (3.4) and a consumer-side **idempotent-dedupe helper**. Without these it's a footgun. Justified.

### 4.3 `aqros-exec-core` — **yes, unconditionally**
This is the embodiment of Hard Rule §7.1 (one core for backtest/paper/live). Non-negotiable. Build its skeleton *before* any harness.

### 4.4 ➕ **`aqros-domain` (new, small) — recommended**
Canonical typed primitives: **`Money` (Decimal-based, never float)**, `Instrument`/identifier + bitemporal `event_time`/`knowledge_time` types, `Price`/`Quantity`. Rationale: money-as-`float` is a latent bug class a trading firm never tolerates; the bitemporal pair is used by *data* services too (not just execution), so it can't live in `aqros-exec-core`. This is genuine cross-cutting reuse (Blueprint §2.1 explicitly names `libs/common-types`). Small, high-value, prevents an entire bug class. **Justified.**

### 4.5 ➕ **`aqros-testing` (dev-only) — mild recommend**
Shared testcontainers-Postgres fixture, `ASGITransport` client fixture, and base fakes. Real duplication across the four services' integration tests, but low-severity and can wait. Justified as convenience, not critical.

### 4.6 Rejected (unnecessary abstraction)
- A generic "repository framework" / ORM base — the per-service repositories are simple and clear; a framework would obscure them. **Reject.**
- A "service framework" that wraps FastAPI beyond `create_app` — `create_app` is already the right amount. **Reject.**
- Per-service client SDK libs (`aqros-market-data-client`, etc.) — premature; the resilient `aqros_core.http` + local decoupled DTOs (the pattern already in use) is sufficient until there are many consumers. **Reject for now.**

---

## Phase 5 — Distributed Systems Review

| Concern | Current / Planned | Finding & required fix |
|---|---|---|
| Service ownership | 1 DB per service | ✅ Clear |
| Network boundaries | REST now; gRPC hot path + zones planned | ✅ design OK; enforce zones in K8s (Phase 7) |
| Timeouts | ad-hoc per httpx client | 🟠 **Not uniform.** Centralize in `aqros_core.http` |
| Retry policies | ad-hoc; training-pipeline deliberately **zero-retry** to Dataset Builder | ⚠️ zero-retry there is *by design* (fail-fast). Elsewhere, standardize **idempotent** retry + backoff |
| Circuit breakers | **none implemented** | 🔴 Blueprint mandates CB on every external call. Add to `aqros_core.http` |
| Idempotency | OMS `client_order_id`, broker idem key, event ULID (planned) | 🟠 **Thread one idempotency/correlation key signal→intent→risk-decision→order** so a retried intent can't create two orders (3.1/3.2) |
| Event ordering | unspecified | 🟠 partition-by-account (3.5) |
| Exactly-once | not claimed; at-least-once + dedupe (planned) | ✅ correct stance — but **enforce dedupe in every consumer** (Portfolio has it; make it a library helper in `aqros-events`) |
| Duplicate delivery | dedupe on `venue_exec_id`, `order_id+seq` | ✅ good where specified; generalize |
| Event replay | "rebuild from log" | 🔴 **false for money path** (3.3) — replay-then-reconcile |
| Clock sync | injected clock (determinism) | 🟠 no live NTP/PTP/sequence discipline (3.8) |
| Backpressure | KEDA-on-lag | 🟠 no producer-side bound (3.8) |
| Failure propagation | fail-closed money / fail-open alpha | ✅ correct doctrine; enforce in code review |
| Startup order | OMS reconcile-first; rest unspecified | 🟠 readiness must gate on rebuild/reconcile (3.7); compose `service_healthy` not `service_started` |
| Shutdown | httpx/engine close on lifespan (verified in training-pipeline) | ⚠️ money path needs **drain + cancel-on-disconnect on SIGTERM** (armed at broker); specify graceful shutdown contract |
| Schema evolution | additive, registry (planned) | 🟠 registry unowned (3.6); pick Redpanda schema registry |
| API versioning | `/v1` | ✅; add deprecation policy |
| Data consistency | strong (Postgres) per service | ✅ |
| Eventual consistency | cross-service via events | ⚠️ document *which* reads are eventually consistent (e.g., dashboards) vs strongly consistent (pre-trade check) |
| Transaction boundaries | per-service DB tx | 🔴 **dual-write** across DB+Kafka → **outbox** (3.4) |

**Dead-man's-switch / cancel-on-disconnect** is designed (good) but must be tested with chaos (broker disconnect mid-order) *before* live.

---

## Phase 6 — Security Review

| Area | State | Finding |
|---|---|---|
| Authentication | **dev-only, none real** | 🔴 OIDC + WebAuthn (humans) + SPIFFE/mTLS (services) is a **V1-blocker**, unowned (3.6). Design Auth now |
| Authorization | RBAC/OPA/four-eyes referenced, **no owner** | 🔴 four-eyes hand-waved across 3 services; needs one Control-Plane authority |
| Secret management | `.env` today; `detect-private-key` pre-commit | 🟠 Vault planned. **CI has no secret scan** (pre-commit is bypassable) → add gitleaks/trufflehog to CI |
| Artifact signing | cosign planned, not built | 🟠 hard gate before live; Registry must refuse unsigned |
| Supply chain | tag-pinned images, no SBOM, no CVE gate | 🔴 pin base images **by digest**, add SBOM (syft) + CVE scan (grype/trivy) to CI, sign images (cosign/SLSA) |
| Docker security | non-root ✅; **not distroless, not read-only rootfs, has apt layer** | 🟠 move to distroless or hardened slim + read-only rootfs + drop caps |
| Container perms | non-root uid 1000 ✅ | ✅ |
| Dependency risk | uv lock ✅; no automated CVE/update | 🟠 add Dependabot/renovate + CVE gate |
| LLM prompt injection | addressed in doc (V2) | ✅ design-level; enforce input sanitization + treat all ingested text as untrusted |
| Event spoofing | **no event auth planned** | 🟠 once Kafka lands, events need producer identity (mTLS + signed envelopes for privileged topics like `risk.limit.changed`, `killswitch.armed`) |
| API abuse | rate-limit at gateway (planned) | ✅ design OK |
| Broker safety | cancel-on-disconnect, egress-whitelist, creds in Vault | ✅ well-designed; test it |
| Risk kernel isolation | pure domain, in-memory ceilings | 🟠 good; also load kernel ceilings from a **separate protected store** changeable only via four-eyes, not the service's own migrations |
| Audit integrity | hash-chain + object-lock (planned) | 🔴 **fire-and-forget append loses records** → outbox/guaranteed delivery (3.4) |
| Model integrity | signing planned | 🟠 enforce verify-before-load in Strategy/Live |

**Net:** security is *designed* well for V2 but *nothing is implemented*, and several items (Auth, secret-scan in CI, image digest-pinning, audit delivery guarantee) are **V1-blockers that must precede any capital**.

---

## Phase 7 — Production Readiness (assume it manages millions)

| Area | State | Finding |
|---|---|---|
| HA | single-node everything | 🔴 acceptable for MVP; **multi-AZ Postgres + replica'd services are a pre-capital gate** |
| Backup | **none** | 🔴 no backup for any Postgres DB today; add automated PITR backups (even in MVP for registry/experiment metadata) |
| Restore | untested | 🔴 a backup you haven't restored doesn't exist — add restore game-day |
| RPO | undefined | 🔴 define: **order/audit ledgers RPO ≈ 0** (synchronous replication); research projections RPO = hours (rebuildable) |
| RTO | "minutes" (aspirational) | 🟠 define per tier; test |
| DB replication | none | 🔴 money-path DBs need sync replication before capital |
| Object storage durability | **local disk volumes — zero durability** | 🔴 **datasets + models live on a single disk**; a disk loss destroys all "reproducible" artifacts. Move to MinIO now (local), S3/R2 later. This contradicts the reproducibility thesis and is the highest-value cheap fix |
| Disaster recovery | replay-from-log (flawed on money path, 3.3) | 🔴 replay-then-reconcile; document DR runbook |
| Multi-region | V2 design | ✅ deferred appropriately |
| Horizontal scaling | stateless services ✅ | ✅ design OK |
| Resource isolation | node pools planned; **no compose resource limits** | 🟠 add limits; enforce trading-pool taints in K8s |
| Observability | **no metrics/traces exposed today** | 🔴 add Prometheus `/metrics` + OTel to `aqros-core` now (cheap, foundational) |
| Incident response | none | 🟠 define sev levels + on-call once capital nears |
| Runbooks | none | 🔴 CLAUDE/Blueprint mandate per-service runbooks; none exist |
| Deployment | compose; K8s/ArgoCD planned | ✅ staged appropriately |

---

## Phase 8 — Final Recommendations

### 8.1 🔴 Critical (must fix before building money-path services)
1. **Object storage durability.** Stand up MinIO in compose now; point dataset-builder + training-pipeline artifact adapters at it (adapter swap only — the ports already exist). *Files:* `docker-compose.yml`, `backend/dataset-builder/.../adapters/parquet_storage.py` (or a new `s3_storage.py`), `backend/training-pipeline/.../adapters/local_artifact_store.py` (or new `s3_artifact_store.py`), both configs. **Why:** artifacts on a single disk make the reproducibility guarantee false.
2. **Transactional outbox in `aqros-events`.** No reliable event/audit delivery without it. *Files:* new `libs/aqros-events`, and an `outbox` table per event-producing service.
3. **Money-path recovery model.** Replace "rebuild from log" with **replay-then-reconcile-against-broker**; define authority hierarchy. *File:* `docs/REMAINING_SERVICES_ARCHITECTURE.md` §0.4/§7/§15.
4. **Sync decision path vs async observation path.** State it explicitly; events never trigger execution. *File:* Remaining doc §3/§5/§7.
5. **Position ownership + buying-power reservation** (in-flight double-spend race). *File:* Remaining doc §5.
6. **Auth + Control-Plane service design** (four-eyes/RBAC/OIDC/mTLS owner). *New spec:* `.kiro/specs/auth-control-plane/`.
7. **Backups + RPO/RTO definition + restore test** for at least registry/order/audit DBs.
8. **CI supply-chain gates:** secret scan (gitleaks), SBOM (syft), CVE scan (grype), image digest-pinning. *Files:* `.github/workflows/ci.yml`, `docker/Dockerfile.service`.
9. **Observability baseline in `aqros-core`:** Prometheus `/metrics` + OTel + correlation-ID propagation.

### 8.2 🟠 High priority
- Retroactive `design.md` + ADRs for market-data, feature-store, dataset-builder (Phase 2.1).
- Extend `aqros-core` with `db`, `http` (timeout/retry/CB), `clock`, `secrets`, `health.critical` (Phase 4).
- Event partitioning-by-account + consumer dedupe helper (3.5, Phase 5).
- Readiness gates on reconcile/rebuild; compose `service_healthy` (3.7).
- Fix cascading readiness (1.6) — non-critical dependency checks.
- Reference-data / security-master ownership decision (3.6).
- Universal Alembic up/down migration test in CI.

### 8.3 🟡 Medium
- `aqros-domain` (Decimal `Money`, instrument, bitemporal types).
- `docs/PLATFORM_CONVENTIONS.md` referenced by all specs.
- Per-service `.env` guidance or derive DB URL from service name (1.7).
- Graceful-shutdown/drain contract (SIGTERM → cancel-on-disconnect).
- Distroless/read-only-rootfs hardening of `Dockerfile.service`.
- Event auth (signed envelopes for privileged topics).

### 8.4 🟢 Nice-to-have
- `aqros-testing` shared fixtures.
- API deprecation policy + changelog convention.
- Dependabot/renovate.
- Inference service split trigger documented.

### 8.5 Technical debt (present now)
- 4× copy-pasted `db.py` / httpx-client / health wiring (→ Phase 4).
- Stray `_tmp_trainers_draft.py` at repo root (delete).
- training-pipeline `penalty="l2"` deprecation.
- `Dockerfile.service` carries an `apt` layer + tag-pinned bases (harden).

### 8.6 Missing documentation
- `docs/adr/` (empty — **zero ADRs exist**; CLAUDE §5 mandates them).
- Per-service runbooks (mandated, none exist).
- DR runbook, incident-response/on-call, restore procedure.
- Specs for 3 of 4 built services.
- Platform conventions doc.

### 8.7 Missing tests
- Universal migration up/down test.
- **Golden deterministic replay harness** (the money-path gate — doesn't exist; must exist before paper/live).
- Chaos/failure-injection suite (broker disconnect, reconciliation break, kill-switch cancel-all).
- Leakage-injection meta-test for the (future) Backtesting engine.
- Cross-service integration/system tests (`tests/` is empty).
- Contract-conformance suite for BrokerGateway adapters.

### 8.8 Missing architecture decisions (ADRs to write)
- ADR: in-process event bus → Kafka migration + outbox.
- ADR: object storage choice + when to leave local/MinIO.
- ADR: sync gRPC decision path vs async event observation path.
- ADR: position/risk-book authority + buying-power reservation.
- ADR: money-path recovery = replay-then-reconcile.
- ADR: Auth/OPA/four-eyes ownership.
- ADR: `Money` as Decimal; bitemporal type home.
- ADR: readiness semantics (critical vs non-critical checks).

### 8.9 Future scalability concerns
- Synchronous training in-request (→ async jobs, V1).
- Single-node Postgres per service (→ replicas/partitioning near capital).
- Risk sub-µs path may need a compiled sidecar later (contract already isolates it — good).
- TimescaleDB → ClickHouse migration path for tick scale (already anticipated).
- Vector/graph stores at V2 scale (anticipated).

### 8.10 Overall architecture score

**7.0 / 10** (pre-implementation).

- **Foundation quality (built services): ~8.5/10** — clean, consistent, disciplined, honestly tested. Genuinely above-average.
- **Forward architecture (design): ~7.5/10** — coherent and correctly staged, but the five money-path issues (dual-path, position authority, replay-vs-reconcile, outbox, ordering) and the unowned Auth/Control-Plane must close, or they become expensive to retrofit.
- **Production readiness: ~4.5/10** — expected at this stage, but object-storage durability, backups/RPO/RTO, observability, and supply-chain gates are cheap now and very costly later.

**To reach 9+ (Jane Street / HRT / Citadel-comparable) before implementation proceeds:** close the nine Critical items, own Auth/Control-Plane, write the missing ADRs, and stand up the golden-replay + chaos harnesses as the money-path gates. The bones are right; the money-path rigor and operational scaffolding are what separate "good" from "trusted-with-capital." None of these require rework of the four completed services — they are *additive*, which is exactly the position a pre-implementation review wants to be in.

---

*End of review. No source files were modified.*
