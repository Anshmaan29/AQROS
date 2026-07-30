# backtesting-engine

The **simulation plane** of AQROS: a deterministic harness that replays
historical market data through the platform's shared strategy, risk, and
order-management core to measure how a strategy driven by an approved
production model would have behaved — without ever touching real capital. It
is the second rung of the trust ladder (research → **backtest** → paper →
supervised live → bounded autonomous) and the primary structural defense
against overfitting and look-ahead bias before a strategy is allowed near
money (CLAUDE.md §1, §6, §10).

**This service never forks the money-path logic.** Strategy, risk,
position-sizing, and order-management decisions all live once in the shared
`libs/aqros-strategy-core` library and are invoked, never reimplemented, by
this engine (CLAUDE.md Hard Rule §7.1). The Backtesting Engine differs from
paper and live execution only in its data source (historical replay instead
of a live feed) and its execution mechanism (a fill/slippage/commission model
instead of a real venue) — the decision logic itself is identical code.

## Service boundary: three upstreams, all REST-only, no cross-service databases

The Backtesting Engine reads from exactly **three** upstream services, and
only through their published REST APIs (CLAUDE.md §7.9 — never another
service's database or internals):

- **Market Data Service** — historical OHLCV bars and instrument metadata,
  consumed as read-only inputs. The engine never writes, updates, or deletes
  any historical market data.
- **Model Registry** — resolves the model a strategy evaluates: the current
  production champion by default, or an explicitly pinned version when
  configured. The engine never obtains a model artifact by any other path.
- **Feature Store** — resolves engineered feature definitions and
  point-in-time values (`as_of = current simulation clock time`) when a
  strategy requires them.

**It never queries the Training Pipeline, for any purpose.** The Backtesting
Engine has no relationship with the Training Pipeline whatsoever — models
come only from the Model Registry, which is itself the Training Pipeline's
sole downstream consumer.

**It never modifies market data.** All historical market data retrieved from
the Market Data Service is treated as strictly read-only.

**It never bypasses the Risk Kernel.** Every simulated order routes through
the same shared risk-check path that paper and live execution use before it
may be filled. There is no configuration, flag, or code path that bypasses,
disables, or relaxes the shared risk logic or the Risk Kernel, and the engine
never modifies a Risk Kernel limit. Rejected orders are recorded in the trade
log and the run continues.

The engine owns its own Postgres database (run metadata, trade log, equity
curve, results) and never opens a database connection to the Market Data
Service's, Model Registry's, Feature Store's, Training Pipeline's, or Dataset
Builder's databases.

## Determinism and point-in-time guarantees

Two properties are engineered structurally, not merely encouraged:

- **Determinism.** Running the same model version(s), the same historical
  market data, the same configuration, and the same parameters always
  produces byte-for-byte identical results. The only source of "now" inside a
  run is an injected `Simulation_Clock` (no domain component reads
  wall-clock time); every stochastic value (slippage, latency) is drawn from
  one seeded random source; every collection is traversed in a fixed, total,
  documented order; and all cash arithmetic uses `Decimal`. Every run is
  fully described by an immutable `Run_Manifest` sufficient to reproduce its
  result bit-for-bit.
- **Point-in-time correctness.** No decision at simulation time `t` may use
  any information whose knowledge time is after `t` — look-ahead bias is
  forbidden by construction. A pure look-ahead guard rejects any read whose
  knowledge time exceeds the simulation clock and fails the run with a
  diagnostic identifying the offending access, rather than silently
  proceeding.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/backtests` | Submit a Backtest Configuration and start a run (idempotent via `Idempotency-Key`) |
| `GET` | `/v1/backtests/{run_uuid}` | Report a run's status (pending / running / completed / failed) |
| `GET` | `/v1/backtests/{run_uuid}/result` | Full result: trade log, equity curve, drawdown, performance + risk metrics, benchmark, final portfolio |
| `GET` | `/v1/backtests` | List runs, optionally filtered by strategy, model name, or status |
| `GET` | `/v1/backtests/{run_uuid}/manifest` | The immutable Run Manifest for a run |
| `GET` | `/health`, `/health/live`, `/health/ready` | Health probes (readiness checks DB + result-artifact store) |
| `GET` | `/docs`, `/openapi.json` | OpenAPI docs |

## Running locally

Via Docker Compose (brings up its three upstreams too):

```bash
docker compose up -d market-data-db market-data \
  feature-store-db feature-store \
  model-registry-db model-registry \
  backtesting-engine-db backtesting-engine
docker exec -w /app/backend/backtesting-engine <container> python -m alembic upgrade head
curl localhost:8010/health/ready
```

Without Docker, against a local Postgres:

```bash
uv sync --group dev
uv run --package aqros-backtesting-engine alembic -c backend/backtesting-engine/alembic.ini upgrade head
uv run --package aqros-backtesting-engine uvicorn aqros_backtesting_engine.app:app --port 8010
```

## Configuration (env vars, prefix `AQROS_`)

| Variable | Default | Purpose |
|---|---|---|
| `AQROS_DATABASE_URL` | `postgresql+asyncpg://aqros:aqros@localhost:5437/aqros_backtesting_engine` | Own dedicated database (run metadata, trade log, equity curve, results) |
| `AQROS_MARKET_DATA_BASE_URL` | `http://localhost:8002` | Market Data Service REST API base URL |
| `AQROS_MODEL_REGISTRY_BASE_URL` | `http://localhost:8004` | Model Registry REST API base URL |
| `AQROS_FEATURE_STORE_BASE_URL` | `http://localhost:8003` | Feature Store REST API base URL |
| `AQROS_RESULT_ARTIFACT_DIR` | `/data/backtesting-engine/artifacts` | Local, versioned result-artifact directory |
| `AQROS_UPSTREAM_REQUEST_TIMEOUT_SECONDS` | service default | Timeout applied to every upstream REST call |

## Port / database assignments

| Setting | Value |
|---|---|
| Service port | `8010` |
| Postgres port | `5437` |
| Database name | `aqros_backtesting_engine` |

This continues the existing port sequence: market-data 8002/5432,
feature-store 8003/5433, dataset-builder 8008/5434, training-pipeline
8009/5435, model-registry 8004/5436, backtesting-engine 8010/5437.

Result artifacts (reports, trade-log exports, equity curves, drawdown series,
metric sets, and run manifests) are persisted to a **local artifact
directory** behind the `ResultArtifactStore` interface in the MVP, checksummed
on write and re-verified on read, mounted as a dedicated volume. An
object-store-backed implementation (S3/MinIO/R2) can be configured later
without any change to domain or API logic — no object-store dependency is
introduced.

## The shared strategy/risk/OMS core

Strategy, risk, position-sizing, and order-management logic is **not**
implemented in this service. It lives once in `libs/aqros-strategy-core`
(module `aqros_strategy_core`) and is shared unmodified across backtest,
paper, and live execution. This engine is that library's first consumer: it
supplies a historical data source and a fill simulator, and invokes the
shared `Strategy` protocol and `RiskCheck` port exactly as paper and live
execution will. The strategy/risk/OMS contracts are never forked or
reimplemented here.

## Tests

- `tests/unit/` — pure domain logic: the deterministic simulation core, event
  ordering, the look-ahead guard, the trading calendar, corporate-action
  application, latency/slippage/commission/fill models, position/portfolio/
  cash tracking, and metric computations, all against fakes for every port
  (`MarketDataClient`, `ModelRegistryClient`, `FeatureStoreClient`,
  `ResultArtifactStore`, `BacktestRunRepository`, `Clock`,
  `CalendarProvider`). Includes property-based tests for the core invariants
  (deterministic replay, event-ordering totality, cash-ledger consistency,
  position reconstruction from fills, absence of look-ahead bias) and a
  deterministic golden-replay test.
- `tests/integration/` — real Postgres via testcontainers, the full API via
  `httpx.AsyncClient`, with the Market Data Service, Model Registry, and
  Feature Store integrations exercised against faked clients (no live
  upstream instances required), and an Alembic schema check.

```bash
uv run pytest backend/backtesting-engine                       # unit + integration
uv run pytest backend/backtesting-engine -m "not integration"   # unit only, no Docker
```
