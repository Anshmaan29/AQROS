# CLAUDE.md — Permanent Instructions for AQROS

> **Read this file first, every time, before doing any work in this repository.**
> This is the standing brief for any AI assistant (Claude, or any other) working on AQROS. It defines what we are building, how the code must be written, the rules that must never be broken, and how you should respond. When in doubt, follow this file. If a request conflicts with a **Hard Rule** in §7, stop and flag it rather than silently complying.

---

## 1. Project Vision

**AQROS = Autonomous Quant Research Operating System.**

We are building an **AI-native quantitative investment platform** — not a trading bot. It researches financial markets, understands macro conditions, analyzes companies, learns from history, reasons like a team of hedge-fund analysts, evaluates risk, and *eventually* executes trades autonomously **only after extensive validation**.

The system must behave like an **investment firm staffed by AI specialists**, not a chatbot and not a single model. Analysts propose, critics challenge, a portfolio manager synthesizes, a risk officer can veto, and every decision is written down, explained, and reviewed afterward.

Guiding ambition: build it the way Renaissance Technologies, Jane Street, Two Sigma, Citadel, or DeepMind would if they started today with modern AI, multi-agent systems, and cloud infrastructure.

**Non-negotiable character of the project:**
- It is **scientific**: every prediction, trade, and model must be reproducible, explainable, measurable, and version-controlled.
- It is **safe by construction**: the AI never blindly trades. Every decision passes through specialized agents that debate, estimate confidence, evaluate risk, and justify reasoning before anything executes.
- It is **self-improving**: it learns from historical trades, its own mistakes, and market-regime changes.
- It is **trust-gated**: capital-at-risk grows only as validated reliability grows. Research → backtest → paper → supervised live → bounded autonomous. Never skip a rung.

### 1.1 The five source-of-truth documents (in `docs/`)
These are already designed. **Do not redesign or contradict them** — implement them. Read the relevant one before working in its area.

| Document | Nickname | Covers |
|---|---|---|
| `docs/AI_QUANT_PLATFORM_BLUEPRINT.md` | **the body** | Distributed system, microservices, event backbone, failure/scaling/security |
| `docs/claude_aiBrain.md` | **the mind** | Cognitive architecture, the 18–20 agents, memory, consensus, confidence, regimes |
| `docs/claude_ROI.md` | **the foundation** | Knowledge & data layer, point-in-time correctness, feature store, ontologies |
| `docs/claude_MLResearchFramework.md` | **the discipline** | ML models, validation, overfitting prevention, drift, the research pipeline |
| `docs/Execution_Blueprint.md` | **the build plan** | Repo layout, roadmap, MVP, priorities, testing, CI/CD, infra — *how to build it* |
| `docs/about.md` | the pitch | Plain-language project summary |

**Order of authority when documents conflict:** `Execution_Blueprint.md` wins on *how to build*; the other three win on *what to build* in their domain; this `CLAUDE.md` wins on *rules and style*.

---

## 2. Tech Stack

Use these unless a source doc says otherwise. Prefer boring, proven, well-documented tools over clever new ones — this project must be maintainable for years.

**Languages**
- **Python** — data, ML, research, agents, most backend services. Primary language.
- **TypeScript** — frontend, and any Node-based tooling.
- (Later, only if latency demands it) a compiled language (Rust/Go/C++) for the ultra-low-latency order path. **Not needed for MVP/V1.**

**Backend & services**
- **FastAPI** (Python) for REST/HTTP services and internal APIs.
- **gRPC** for internal high-performance service-to-service calls (add when we split services, V1+).
- **Pydantic** for typed data models and config validation.

**Data & storage**
- **PostgreSQL** — transactional data (orders, accounts, metadata, security master).
- **TimescaleDB** (Postgres extension) — time-series (ticks, bars). Migrate hot data to **ClickHouse** only at large scale (V2+).
- **Redis** — online features, caching, working memory.
- **Object storage** — S3 / Cloudflare R2 / MinIO (local) for the data lake + artifacts.
- **Apache Iceberg** (or Parquet files early) — open table format for the lake (time-travel = reproducibility).
- **Neo4j** — knowledge graph (V2). **pgvector** first, then **Qdrant/Milvus** — vector store (V2).

**Event backbone**
- **Kafka / Redpanda** — the event log and source of truth (V1+). For the MVP, an **in-process event bus behind the same interface** is fine.

**ML & research**
- **pandas / polars** (polars preferred for large data), **numpy**.
- **scikit-learn** (baselines, pipelines), **LightGBM / XGBoost / CatBoost** (the tabular workhorses).
- **PyTorch** for deep learning (LSTM/GRU/Transformer/TFT/GNN) — V2+.
- **MLflow** — experiment tracking + model registry.
- **Optuna** — hyperparameter optimization.

**Agents / LLM layer** (V2)
- LLM reasoning via the latest Claude models (see §9). Orchestration can start simple (Python) and adopt an agent framework only if it earns its place.

**Infra & ops**
- **Docker** + **docker-compose** (local) → **Kubernetes** (V1+).
- **Terraform** for cloud infrastructure-as-code.
- **GitHub Actions** for CI/CD.
- **Prometheus + Grafana + Loki + OpenTelemetry** for metrics/logs/traces.
- **Vault / cloud KMS** for secrets.

**Frontend**
- **React + TypeScript**, **Vite** build, a component library (e.g. shadcn/ui or MUI). Charts via a mature lib (Recharts/ECharts). Keep it thin — logic lives server-side.

---

## 3. Folder Structure

This is a **monorepo** — one repository, many services, strict boundaries. The full layout is in `docs/Execution_Blueprint.md` §2; the summary:

```
aqros/
├── CLAUDE.md               ← you are here (permanent AI instructions)
├── steps_to_Create.md      ← beginner build guide
├── README.md               ← project intro
├── docs/                   ← the 5 design docs + about.md (source of truth)
├── libs/                   ← SHARED code: event schemas, domain types, and the
│                              backtest/paper/live shared strategy+risk core
├── backend/                ← microservices (one folder each)
│   └── <service>/
│       ├── api/            ← transport layer (FastAPI/gRPC handlers) — thin
│       ├── domain/         ← business logic — pure, no I/O, easy to test
│       ├── adapters/       ← DB, Kafka, external clients — I/O at the edges
│       ├── config/         ← typed config
│       ├── migrations/     ← DB migrations
│       └── tests/          ← unit + service tests
├── frontend/               ← React/TS UIs (research, control, admin)
├── agents/                 ← the cognitive layer (perception→analysts→PM→risk→…)
├── models/                 ← model code & specs (weights live in object storage)
├── datasets/               ← dataset/feature/label DEFINITIONS (not raw data)
├── training/               ← training pipelines, HPO, validation harness
├── backtesting/            ← backtest engine + cost simulator (uses shared core)
├── execution/              ← OMS, EMS, broker adapters (the money path)
├── research/               ← notebooks, hypotheses, negative-results log
├── infra/                  ← Terraform, environment configs (secret REFERENCES only)
├── docker/                 ← Dockerfiles + docker-compose
├── kubernetes/             ← Helm/Kustomize charts
├── monitoring/             ← Prometheus rules, Grafana dashboards (as code)
├── scripts/                ← dev bootstrap, backfills, migrations
└── tests/                  ← cross-service integration/system/simulation tests
```

**The standard service skeleton** (`api/domain/adapters/config/migrations/tests`) is used by **every** backend service — this is ports-and-adapters (hexagonal) architecture. Domain logic is pure and framework-free so it is fast to test and easy to reason about; all I/O sits at the edges.

**Boundary rule:** a service may depend on `libs/` but **never** on another service's internals. Cross-service communication is via events (Kafka) or published gRPC/REST contracts — never direct imports. Breaking this creates a distributed monolith and is forbidden.

---

## 4. Architecture Summary

AQROS is organized into **planes** (full detail in `docs/AI_QUANT_PLATFORM_BLUEPRINT.md`):

- **Data plane** — ingest market/fundamental/alt/macro data → store point-in-time-correct in the lake → serve via the feature store.
- **Intelligence plane** — models (registry + inference) and the multi-agent brain.
- **Decision plane** — risk engine (with a hard kernel), portfolio optimizer, explainability.
- **Execution plane** — OMS + EMS + venue adapters.
- **Simulation plane** — backtest + paper simulator, sharing the exact same strategy/risk/OMS code as live.
- **Control & observability plane** — human control surface, metrics/logs/traces, and the immutable WORM audit ledger.

**The spine:** an event backbone (Kafka) is the **source of truth**; every database is a rebuildable projection of it. Services communicate asynchronously via events, and synchronously (in-process/gRPC) only on the latency-critical order path.

**The brain** (`docs/claude_aiBrain.md`): a five-tier cognitive stack — Perception → Cognition (analyst floor) → Deliberation (bull/bear + PM synthesis + consensus) → Governance (risk/red-team/compliance/sizing) → Action (execution + narrator) → Reflection (learn from every trade). An Orchestrator/CIO agent governs; a memory fabric (working/episodic/semantic/trade/mistake) is read by every tier.

**The data foundation** (`docs/claude_ROI.md`): everything is **bitemporal** — each fact has an `event_time` and a `knowledge_time` (when we could have known it). Point-in-time correctness is enforced by construction.

---

## 5. Coding Standards

- **Match the surrounding code.** Consistency beats personal preference. The codebase should read as if written by one careful engineer.
- **Ports-and-adapters everywhere.** Keep `domain/` pure (no DB/network/LLM calls); push I/O to `adapters/`. This makes logic testable and infrastructure swappable.
- **Type everything.** Python: full type hints + Pydantic models at boundaries. TypeScript: `strict` mode, no `any`. Types are documentation that can't go stale.
- **Small, single-purpose functions and modules.** If a file does two jobs, split it.
- **Fail loudly on the money path, gracefully on the alpha path.** On the order/risk path, never swallow errors — fail closed (reject the trade). On the research/signal path, degrade and alert.
- **No wall-clock time in domain logic.** Inject the clock (and any timestamps) so backtests and tests are deterministic and reproducible. This is critical for point-in-time correctness.
- **Idempotency on anything that mutates money state** — orders carry client-generated IDs; retries must never duplicate.
- **Every external call gets a timeout, a retry policy (idempotent), and a circuit breaker.**
- **Configuration via typed config + environment**, validated at startup (fail fast on bad config). No config values hard-coded in logic. No secrets in code, ever.
- **Structured logging with a correlation ID** threading each request/decision across services. No secrets or personal data in logs.
- **Tests are part of "done."** New money-path logic needs unit tests; new services need integration tests. A change to the backtest/live shared core must pass the deterministic golden-replay test.
- **Comment the "why," not the "what."** The code shows what; comments explain intent, trade-offs, and non-obvious decisions. Match the existing comment density.
- **Write an ADR** (`docs/adr/`) for any significant architectural decision: context → decision → consequences. This prevents re-litigating settled choices.

---

## 6. Development Principles

1. **Correctness on the money path over everything.** Losing a signal is a bad day; an uncontrolled order is existential.
2. **One codebase for backtest, paper, and live.** Only the data source and the fill simulator differ. Never fork the strategy/risk/OMS logic. (See Hard Rule §7.1.)
3. **Point-in-time correctness is sacred.** No query, feature, label, backtest, or memory recall may ever use data before its `knowledge_time`. Lookahead bias is structurally forbidden, not just discouraged.
4. **The AI proposes; the risk kernel disposes.** Hard, human-owned limits (max notional, order rate, drawdown) that no agent, consensus, or model can raise.
5. **Everything is versioned and reproducible.** Any model, dataset, feature, or result reconstructs bit-for-bit from an immutable manifest (data snapshot + code SHA + config).
6. **Confidence is first-class.** Every prediction carries a calibrated uncertainty that propagates to position size. Low confidence → small size or no trade.
7. **Every decision must justify itself.** No trade executes in autonomous mode without a coherent, auditable explanation.
8. **Ship a thin vertical slice first.** Build one narrow path end-to-end before widening any layer. Working-and-small beats broad-and-broken.
9. **Mocks sit behind real interfaces.** Stub the broker/vendor/auth behind the *real* interface so later stages compose onto earlier ones instead of reworking them. This is our main defense against technical debt.
10. **Assume every impressive backtest is a bug until proven otherwise.** Overfitting and data leakage are the primary enemies (see `docs/claude_MLResearchFramework.md` §8, §10).

---

## 7. Rules (Hard Rules — never break these)

These are **inviolable.** If a request would break one, refuse and explain, or ask for confirmation with the risk spelled out.

1. **NEVER duplicate logic between backtest and live.** The strategy, risk, sizing, and order logic live **once** in `libs/` and are shared by backtest, paper, and live. If you find yourself copying this logic, stop — refactor it into the shared core instead.
2. **NEVER introduce lookahead / future data.** No feature, label, or query may use information timestamped after the decision moment. Always respect `knowledge_time`. When unsure, treat data as *not yet known*.
3. **NEVER let the AI raise its own risk limits.** Hard kernel ceilings are human-owned and changed only via four-eyes approval. No agent, model, or automated loop may modify them.
4. **NEVER auto-promote a model to real capital.** Retraining/learning produces *candidates*. Promotion to live requires passing the full validation gauntlet AND human approval.
5. **NEVER commit secrets** (API keys, broker credentials, `.env`). Use environment variables / a secrets manager and references only. If you spot a secret in the repo, flag it immediately.
6. **NEVER skip a trust rung.** No live capital before paper parity holds; no autonomy before supervised-live proves out. Research/paper are always safe (no real money).
7. **NEVER trade real money without: a passing risk check, a coherent explanation, and (until fully autonomous) human approval.** Fail closed if any is missing.
8. **NEVER modify the WORM audit ledger.** It is append-only and tamper-evident by design.
9. **NEVER let a service reach into another service's database or internals.** Communicate through events or published APIs only.
10. **NEVER reward a lucky win or punish an unlucky-but-sound loss** in the learning loop. Reflect on *reasoning quality*, not just outcome.

---

## 8. How Claude Should Respond

- **Be direct and concrete.** Give a recommendation, not a survey of options. If you weigh choices, state your pick and why in a sentence or two.
- **Respect the stage.** We build MVP → V1 → V2. Don't pull V2 complexity (multi-agent brain, Kafka, K8s, GPU serving) into MVP work. Check `docs/Execution_Blueprint.md` §7 for what belongs where.
- **Follow the docs; don't redesign them.** If you think a design doc is wrong, say so explicitly and explain — don't silently deviate.
- **Explain for a beginner when asked, precisely for an engineer when building.** The project owner is early-career and building this for a placement/startup — when explaining concepts, use plain language and analogies; when writing code, keep it clean, typed, and commented.
- **Before non-trivial work, restate the plan in 2–4 bullets** so we're aligned, then proceed.
- **Prefer editing existing files over creating new ones.** Don't create documentation files unless asked. Keep the repo tidy.
- **Small, reviewable changes.** One logical change at a time. Show what changed and why.
- **Flag risk explicitly.** If something touches the money path, security, or point-in-time correctness, call it out and be extra careful.
- **Never fabricate results.** If tests fail, say so with the output. If a step was skipped, say it. Report honestly.
- **When you finish, state plainly what you did, what you verified, and what's left.** No hedging when it's done and verified; no false confidence when it isn't.
- **Ask before destructive or hard-to-reverse actions** (deleting files, force-pushing, dropping data, external calls that publish data).

---

## 9. Preferred Libraries (quick reference)

| Purpose | Use | Avoid / note |
|---|---|---|
| Data frames | **polars** (large), **pandas** (small/compat) | — |
| Numerical | **numpy** | — |
| Tabular ML | **LightGBM**, **XGBoost**, **CatBoost**, **scikit-learn** | Start with a linear baseline *always* |
| Deep learning | **PyTorch** | V2+; don't reach for DL on tabular data |
| Experiment tracking / registry | **MLflow** | — |
| Hyperparameter tuning | **Optuna** | Budget-aware, nested CV |
| Explainability | **SHAP** | — |
| API framework | **FastAPI** + **Pydantic** | — |
| Internal RPC | **gRPC** (+ protobuf) | V1+ |
| DB access | **SQLAlchemy** / **asyncpg**; **Alembic** for migrations | Never share DBs across services |
| Time-series DB | **TimescaleDB** → **ClickHouse** (scale) | — |
| Cache / online store | **Redis** | — |
| Vector store | **pgvector** → **Qdrant/Milvus** | — |
| Graph | **Neo4j** | V2 |
| Streaming | **Kafka / Redpanda** (in-proc bus for MVP) | — |
| Validation/tests | **pytest**, **hypothesis** (property tests) | Deterministic, seeded |
| LLM reasoning | latest **Claude** models (Opus/Sonnet, see below) | V2; keep prompts/versioned |
| Frontend | **React + TypeScript + Vite** | Keep thin |
| Charts | **Recharts / ECharts** | — |
| Config | **Pydantic Settings** / env | No secrets in code |

**Claude model IDs** (for the LLM layer, when we build V2): Opus 4.8 = `claude-opus-4-8`, Sonnet 5 = `claude-sonnet-5`, Haiku 4.5 = `claude-haiku-4-5-20251001`. Default to the most capable model for reasoning-heavy agents; use a smaller/faster model for cheap, high-volume steps.

---

## 10. Naming Conventions

- **Files/folders:** lowercase with hyphens for services (`market-data`, `risk-engine`); snake_case for Python modules (`feature_store.py`); PascalCase for React components (`RiskPanel.tsx`).
- **Python:** `snake_case` functions/variables, `PascalCase` classes, `UPPER_SNAKE` constants. Type hints mandatory.
- **TypeScript:** `camelCase` variables/functions, `PascalCase` types/components, `UPPER_SNAKE` constants.
- **Services** are **nouns**: `market-data`, `feature-store`, `risk-engine`, `oms`, `ems`, `model-registry`.
- **Events/topics** are `subject.verb` in past tense: `orders.filled`, `signals.generated`, `market.ticks.<shard>`, `risk.rejected`. Namespaced by domain.
- **Database:** `snake_case` tables (plural: `orders`, `positions`) and columns; every time-sensitive table has `event_time` and `knowledge_time` columns.
- **Use the project's ubiquitous language consistently** — the same word means the same thing in code, DB, docs, and UI. Key terms: *instrument, signal, regime, episode, thesis, confidence, feature, label, backtest, paper, live, kernel, agent*. Never invent a synonym for an existing concept.
- **Model/dataset versions** are immutable and content-addressed (e.g., `momentum_v3`, dataset `hash`), never mutated in place.
- **Branches:** `main` (always releasable) + short-lived `feature/<short-name>` or `fix/<short-name>`. Trunk-based; merge via PR.

---

## 11. Quick Start for an AI Session

When you begin work in this repo:
1. **Read this file.** Then read `docs/about.md` for context.
2. **Identify the stage** (MVP/V1/V2) the task belongs to — check `docs/Execution_Blueprint.md` §6–7.
3. **Read the relevant design doc** for the area you're touching (body/mind/foundation/discipline).
4. **Restate the plan** in a few bullets, then build the smallest working slice.
5. **Respect the Hard Rules (§7).** Especially: shared backtest/live core, no lookahead, risk kernel sovereignty, no secrets.
6. **Test what you build**, report honestly, keep changes small and reviewable.

> **The one-line soul of this project:** *the edge is not the model — it is the disciplined, reproducible, explainable, self-improving system that safely turns a portfolio of weak, diverse signals into decisions, without ever fooling itself or bypassing its own safety.* Build accordingly.
