# Implementation Plan: Backtesting Engine

This plan implements `backend/backtesting-engine` (module `aqros_backtesting_engine`) and the new shared library `libs/aqros-strategy-core` (module `aqros_strategy_core`) exactly as specified in `requirements.md` (approved) and `design.md` (approved). The Backtesting Engine is the deterministic simulation plane: it replays historical market data through the shared strategy/risk/OMS core to measure how a strategy driven by an approved production model would have behaved, consuming Market Data, Model Registry, and Feature Store only through their published REST APIs, never querying the Training Pipeline, never modifying market data, and never bypassing the Risk Kernel. Determinism and point-in-time correctness are engineered structurally. Tasks are ordered shared-core → domain → adapters → migrations → API → app wiring → docker → unit tests (property-based) → integration tests → quality gates, mirroring how `backend/model-registry` was built. Result-artifact storage uses a local `ResultArtifactStore` adapter in the MVP (swappable for an object store later — no object-store dependency is introduced).

## 1. Project Scaffolding & Shared Strategy Core

- [x] 1.1 Create `libs/aqros-strategy-core/pyproject.toml` declaring package `aqros-strategy-core`, module `aqros_strategy_core`, dependency `aqros-core`, mirroring `libs/aqros-core`'s pyproject structure; add `src/aqros_strategy_core/__init__.py` and `src/aqros_strategy_core/py.typed`. The `[tool.uv.workspace] members = ["libs/*", "backend/*"]` glob already picks it up; add `"aqros_strategy_core"` to the root `pyproject.toml` `[tool.ruff.lint.isort].known-first-party` list.
  - _Requirements: 1.2, 13, 45.8_
- [x] 1.2 Implement the shared contracts in `libs/aqros-strategy-core` (the single home shared unmodified by backtest, paper, and live — CLAUDE.md §7.1): `contracts.py` (`OrderSide`, `OrderType` StrEnums and frozen/slots `OrderIntent`, `Order`, `Fill` value types), `strategy.py` (`Strategy` `Protocol` with `on_event(context: StrategyContext) -> list[OrderIntent]` and a `StrategyContext` that exposes only point-in-time-correct market data, features, and resolved-model outputs — never wall-clock, never future data), `sizing.py` (`PositionSizer` hook), and `risk.py` (`RiskCheck` port with `check(order_intent, context) -> RiskDecision` and a `RiskDecision` type — the Risk Kernel seam). All pure, no I/O, fully type-hinted.
  - _Requirements: 1.2, 13.1, 34.1, 45.8_
- [x] 1.3 Create `backend/backtesting-engine/pyproject.toml` declaring package `aqros-backtesting-engine`, module `aqros_backtesting_engine`, runtime dependencies (fastapi, uvicorn[standard], sqlalchemy[asyncio], asyncpg, alembic, httpx, pydantic, pydantic-settings, structlog, `aqros-core` and `aqros-strategy-core` as workspace sources) and a dev dependency group (pytest-asyncio, aiosqlite, testcontainers[postgres], psycopg[binary], hypothesis), matching `backend/model-registry/pyproject.toml`'s structure — no ML libraries.
  - _Requirements: 42.1, 42.5, 44_
- [x] 1.4 Create the package skeleton: `src/aqros_backtesting_engine/__init__.py`, `src/aqros_backtesting_engine/py.typed`, and empty `__init__.py` files for `domain/`, `adapters/`, `api/`, and `api/routes/`, per design.md Section 5's file layout.
  - _Requirements: 31.3, 41.3_
- [x] 1.5 Create `backend/backtesting-engine/README.md` documenting the service's purpose (the deterministic simulation plane / second trust-ladder rung), its three REST-only upstream dependencies (Market Data, Model Registry, Feature Store), the facts that it never queries the Training Pipeline, never modifies market data, and never bypasses the Risk Kernel, its determinism and point-in-time guarantees, local run instructions, and the port/DB assignments (8010 / 5437) with local result-artifact storage.
  - _Requirements: 1.1, 1.4, 42.2, 42.3_
- [x] 1.6 Create `tests/__init__.py`, `tests/unit/__init__.py`, and `tests/integration/__init__.py` scaffolding matching design.md Section 5's test layout, under `backend/backtesting-engine/`.
  - _Requirements: 43.1, 43.4_

## 2. Domain Layer — Models & Ports

- [x] 2.1 Implement `domain/models.py` exactly as design.md Section 4: StrEnums `RunStatus`, `AssetClass`, `OrderSide`, `OrderType`, `OrderStatus`, `CorporateActionType`, `EventKind`; and frozen/slots dataclasses `BacktestConfiguration`, `ResolvedModel`, `RunManifest`, `Instrument`, `Bar` (with `event_time` + derived `knowledge_time`), `CorporateAction`, `Event` (with the total `ordering_key`), `SimulatedOrder`, `Position`, `CashLedger`, `Portfolio`, `TradeLogEntry`, `EquityPoint`, `DrawdownSummary`, `PerformanceMetrics`, `RiskMetrics`, `BenchmarkComparison`, `BacktestResult`. `OrderIntent`/`Order`/`Fill` are imported from `aqros_strategy_core`; upstream shapes are local decoupled copies (never import `aqros_market_data`, `aqros_model_registry`, `aqros_feature_store`, or `aqros_training_pipeline`).
  - _Requirements: 3.1, 7, 8, 11, 12, 19, 20, 21, 25, 30_
- [x] 2.2 Implement `domain/ports.py`: `MarketDataClient` ABC (`get_bars`, `get_instrument`, `get_corporate_actions`) with `MarketDataUnavailableError`; `ModelRegistryClient` ABC (`resolve_production`, `get_version`, `download_artifact`) with `ModelNotFoundError` / `ModelRegistryUnavailableError`; `FeatureStoreClient` ABC (`get_feature_values` with an `as_of` cutoff) with `FeatureStoreUnavailableError`; `ResultArtifactStore` ABC (`write_artifact`, `read_artifact`) with `ArtifactAlreadyExistsError` and `ArtifactIntegrityError`; `BacktestRunRepository` ABC; `Clock` ABC; `CalendarProvider` ABC — per design.md Sections 5.1 and 6.
  - _Requirements: 1.4, 1.5, 2.1, 9.1, 10.1, 38, 39_

## 3. Domain Layer — Deterministic Simulation Kernel

- [x] 3.1 Implement `domain/calendar.py` (`Trading_Calendar`): pure, deterministic exchange sessions excluding weekends and holidays and applying half-days, with timezone-aware, DST-safe session open/close resolution and no wall-clock reads.
  - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_
- [x] 3.2 Implement `domain/events.py`: `Event`/`EventKind` and the total ordering key `(event_time, kind_priority, sequence)` with the fixed intra-instant priority (corporate action → market bar → order-eligible → equity sample); a pure comparator/sort used everywhere events are ordered.
  - _Requirements: 6.1, 6.2, 6.4, 31.4_
- [x] 3.3 Implement `domain/lookahead.py` (`look_ahead_guard`): a pure `assert_knowable(knowledge_time, clock)` that raises when `knowledge_time > clock`, so any component read past the simulation clock fails loudly.
  - _Requirements: 4.4, 5.4, 37.3_
- [x] 3.4 Implement `domain/replay.py` (`Historical_Replay`): build the totally-ordered `Event_Stream` from bars, corporate actions, and equity-sample markers for the configured universe and period; assign each bar a `knowledge_time` equal to its exchange session close (Decision 3); advance the clock only to the current event's time, never ahead; record instruments with no data rather than failing silently.
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 5.2, 5.3, 6.1_
- [x] 3.5 Implement `domain/latency.py`: `LatencyModel` ABC plus `ZeroLatency` (MVP default), `FixedLatency`, and `ConfigurableLatency`; `eligible_time(emitted_at, rng)` computes the earliest fill-eligible time, drawing any stochastic delay only from the seeded RNG.
  - _Requirements: 15.1, 15.2, 15.3, 15.4_
- [x] 3.6 Implement `domain/slippage.py`: `SlippageModel` ABC plus `ZeroSlippage` and `FixedBpsSlippage`; deterministic price adjustment, any stochastic component drawn only from the seeded RNG.
  - _Requirements: 16.1, 16.2, 16.3, 16.4_
- [x] 3.7 Implement `domain/commission.py`: `CommissionModel` ABC plus `ZeroCommission`, `PerShareCommission`, and `PctNotionalCommission`; deterministic cost computation using `Decimal`.
  - _Requirements: 17.1, 17.2, 17.3, 17.4_
- [x] 3.8 Implement `domain/fills.py`: `FillModel` ABC plus `ImmediateFillModel` and `LiquidityCappedFillModel`; composes the slippage and commission models, supports market and limit orders (filling a limit only when point-in-time data satisfies its limit price), caps partial fills at point-in-time liquidity, and never fills against data before the order's eligible time (no look-ahead). Deterministic given inputs, params, and seed.
  - _Requirements: 14.1, 14.4, 14.5, 18.1, 18.2, 18.3, 18.4, 18.5_
- [x] 3.9 Implement `domain/corporate_actions.py`: point-in-time application of splits/reverse splits (adjust quantity + cost basis + subsequent price references, preserving total value except explicitly recorded rounding), cash dividends (credit cash), stock dividends (adjust position), symbol changes/mergers (map to successor), and delistings (resolve per terms, record in trade log) — using only actions whose `knowledge_time <= clock`.
  - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6_
- [x] 3.10 Implement `domain/portfolio.py`: `Position` (signed quantity, average cost, realized/unrealized P&L, long/short/flat), `CashLedger` (`Decimal` arithmetic, initialized to starting cash, debited/credited by fills + commissions), `Portfolio` (positions ordered by symbol + cash; point-in-time total valuation), and margin/leverage/buying-power/maintenance-margin plus deterministic forced liquidation (leverage disabled by default; blocked fills recorded).
  - _Requirements: 19.1, 19.2, 19.3, 19.4, 20.1, 20.2, 20.3, 20.4, 21.1, 21.2, 21.3, 21.4, 21.5, 22.1, 22.2, 22.3, 22.4, 22.5_
- [x] 3.11 Implement `domain/metrics.py`: pure, deterministic performance metrics (total/annualized return, Sharpe, Sortino, win rate — net of costs; undefined reported as explicitly undefined), risk metrics (volatility, max drawdown, value-at-risk, gross/net exposure), the drawdown series + max magnitude/duration, and benchmark comparison (excess return) computed from Market Data only.
  - _Requirements: 23.1, 23.2, 23.3, 23.4, 24.1, 24.2, 24.3, 24.4, 27.1, 27.2, 27.3, 27.4, 28.1, 28.2, 28.3, 28.4_

## 4. Domain Layer — Orchestration

- [x] 4.1 Implement `domain/simulation.py` (`Simulation_Engine`): the pure core loop that, per Event in ordering-key order, advances the injected `Simulation_Clock`, applies corporate actions, dispatches market bars to the shared `Strategy` (features requested `as_of = clock`), routes every emitted `OrderIntent` through the shared `RiskCheck` before any fill (recording rejections in the trade log and continuing), applies latency → fill → slippage → commission, updates the portfolio/cash, and samples the equity curve — all deterministic, no I/O, no wall-clock, using one seeded RNG and total ordering.
  - _Requirements: 6.3, 13.1, 13.3, 13.4, 25.1, 25.2, 25.3, 25.4, 31.1, 34.1, 34.2, 34.5_
- [x] 4.2 Implement `BacktestService` in `domain/services.py`: orchestrate a run — resolve the model via the Model Registry (production champion by default, explicit pin allowed, fail if none), build the trading calendar and replay, execute the `Simulation_Engine`, assemble the immutable `RunManifest` (code SHA, config, resolved model(s)+checksums, feature versions, market-data selection + knowledge-time boundary, calendar source, corporate actions applied/unavailable, library versions, seed), assemble the `BacktestResult`, and drive the `PENDING → RUNNING → COMPLETED | FAILED` lifecycle, failing the run (never a partial-as-complete result) on any upstream error, look-ahead-guard trip, or no-approved-model condition, recording a human-readable reason.
  - _Requirements: 9.2, 9.3, 9.4, 9.6, 9.7, 12.1, 12.2, 12.3, 12.4, 29.4, 32.1, 32.2, 32.3, 33.1, 37.1, 37.2, 37.3, 37.4, 37.5_
- [x] 4.3 Implement `BacktestQueryService` in `domain/services.py`: `get_run`/status, `get_result`, `get_manifest`, and `list_runs(strategy_id=None, model_name=None, status=None)` — thin read-only wrappers over the repository and result-artifact store, raising typed not-found errors for unknown runs/results/manifests.
  - _Requirements: 36.2, 36.3, 36.4, 36.5, 36.8_

## 5. Adapters Layer

- [x] 5.1 Implement `adapters/db.py`: `create_engine(settings)`, `create_session_factory(engine)`, async `session_scope`, `ping(engine) -> bool` — copy the exact pattern from `aqros_model_registry.adapters.db`.
  - _Requirements: 41.1_
- [x] 5.2 Implement `adapters/orm.py`: `BacktestRunORM`, `TradeLogEntryORM`, `EquityPointORM`, `BacktestResultORM` (SQLAlchemy 2.0 `DeclarativeBase`) exactly matching design.md's Database Schema, including `UNIQUE (run_uuid)` on `backtest_runs` and on `backtest_results`, the append-only `trade_log_entries`/`equity_points` (no update/delete path), write-once results, and the indexes on run_uuid/status/strategy_id/model_name.
  - _Requirements: 30.1, 38.2, 38.3, 40.2, 40.4_
- [x] 5.3 Implement `adapters/repository.py` (`SqlAlchemyBacktestRunRepository`): `create_run`, `set_status`, `append_trade_log`, `append_equity_points`, `write_result` (refused if a result already exists), `get_run`, `get_result`, `get_manifest`, `list_runs` — translating ORM rows to/from the frozen domain dataclasses via private `_to_domain_*` helpers, taking an `AsyncSession` via constructor and never committing.
  - _Requirements: 38.2, 38.3, 38.4, 40.2, 40.4_
- [x] 5.4 Implement `adapters/market_data_client.py` (`HttpMarketDataClient`): wraps an injected `httpx.AsyncClient`; `get_bars` calls `GET /v1/instruments/{symbol}/bars?start&end&interval&limit&offset` (paginating), `get_instrument` calls `GET /v1/instruments/{symbol}`, and `get_corporate_actions` calls the Market Data corporate-actions endpoint where it exists and otherwise reports the feed unavailable (never synthesizing); raises `MarketDataUnavailableError` on error/unreachability; translates JSON to local decoupled dataclasses; never imports `aqros_market_data`.
  - _Requirements: 2.1, 2.4, 3.5, 8.7_
- [x] 5.5 Implement `adapters/model_registry_client.py` (`HttpModelRegistryClient`): `resolve_production` calls `GET /v1/models/{model_name}/production`, `get_version` calls `GET /v1/models/{model_name}/versions/{version}` (plus `/lineage`), `download_artifact` calls `.../artifact`; captures identity, checksum, and lineage; raises `ModelNotFoundError` / `ModelRegistryUnavailableError`; never queries the Training Pipeline; never imports `aqros_model_registry`.
  - _Requirements: 9.1, 9.4, 9.5, 9.6, 45.1_
- [x] 5.6 Implement `adapters/feature_store_client.py` (`HttpFeatureStoreClient`): `get_feature_values` calls `GET /v1/instruments/{symbol}/features/{feature_name}?feature_version&start&end&as_of&limit&offset`, always passing `as_of = current Simulation_Clock time` so only point-in-time-correct values return; raises `FeatureStoreUnavailableError` on error; never imports `aqros_feature_store`.
  - _Requirements: 10.1, 10.2, 10.4_
- [~] 5.7 Implement `adapters/local_result_artifact_store.py` (`LocalResultArtifactStore`): path `{base_dir}/{run_uuid}/{name}`; `write_artifact` computes and stores a checksum and raises `ArtifactAlreadyExistsError` rather than overwriting; `read_artifact` recomputes and compares the checksum, raising `ArtifactIntegrityError` on mismatch; swappable for an object store with no domain/API change.
  - _Requirements: 38.5, 39.1, 39.2, 39.3, 39.4, 39.5_
- [~] 5.8 Implement `adapters/calendar_provider.py` (`StaticCalendarProvider`): supplies versioned, deterministic exchange trading-calendar data (sessions, holidays, half-days, time zone) to the domain `Trading_Calendar`, exposing a calendar source/version string for the manifest.
  - _Requirements: 7.6_
- [~] 5.9 Implement `adapters/clock.py`: `SystemClock` (adapter, real UTC now) and `SimulationClock` (the injected, monotonic logical clock that advances only as events are processed and is the domain's sole "now").
  - _Requirements: 4.2, 6.3, 31.1_

## 6. Database Migrations

- [~] 6.1 Create `backend/backtesting-engine/alembic.ini`, `migrations/env.py`, and `migrations/script.py.mako`, mirroring `backend/model-registry`'s Alembic setup (async engine config from `Settings`, target metadata pointed at `adapters.orm.Base.metadata`).
  - _Requirements: 38.1_
- [~] 6.2 Create `migrations/versions/0001_initial_schema.py`: `upgrade()` creates `backtest_runs`, `trade_log_entries`, `equity_points`, and `backtest_results` with every column from design.md's Database Schema, the `UNIQUE (run_uuid)` constraints, and the indexes; `downgrade()` drops them symmetrically — same style as `backend/model-registry/migrations/versions/0001_initial_schema.py`.
  - _Requirements: 30.1, 38.2, 38.3, 40.4_

## 7. API Layer

- [~] 7.1 Implement `api/schemas.py`: `SubmitBacktestRequest`, `BacktestRunResponse` (status), `BacktestResultResponse` (trade log, equity curve, drawdown, performance + risk metrics, benchmark, final portfolio), `RunManifestResponse`, `BacktestRunListItem`, and the shared `ErrorResponse(error, detail)` envelope — each response schema with a `from_domain(...)` converter, mirroring `aqros_model_registry.api.schemas`.
  - _Requirements: 36.1, 36.2, 36.3, 36.4, 36.5, 36.8_
- [~] 7.2 Implement `api/deps.py`: FastAPI DI functions reading the three upstream clients and the result-artifact store off `request.app.state`, `get_session` for request-scoped sessions, the repository constructor, and `get_backtest_service` / `get_backtest_query_service` composing the domain services from injected ports — mirroring `aqros_model_registry.api.deps`.
  - _Requirements: 41.3_
- [~] 7.3 Implement `api/routes/runs.py`: `POST /v1/backtests` (validate the `BacktestConfiguration` with a typed 422 on error, require an `Idempotency-Key` header, assign a `Run_Uuid`, persist `PENDING`, start background execution, return the `Run_Uuid`), `GET /v1/backtests/{run_uuid}` (status + failure reason), `GET /v1/backtests` (list with `strategy_id`/`model_name`/`status` filters), and `GET /v1/backtests/{run_uuid}/manifest` — typed 404s for unknown resources.
  - _Requirements: 29.1, 29.2, 30.4, 36.1, 36.2, 36.4, 36.5, 36.6, 36.8_
- [~] 7.4 Implement `api/routes/results.py`: `GET /v1/backtests/{run_uuid}/result` returning the full `Backtest_Result`; typed 404 if the run or result does not exist.
  - _Requirements: 36.3, 36.8_

## 8. App Wiring

- [~] 8.1 Implement `config.py`: `Settings` extending `aqros_core.config.BaseServiceSettings` with `service_name="backtesting-engine"`, `port=8010`, `database_url` defaulting to `postgresql+asyncpg://aqros:aqros@localhost:5437/aqros_backtesting_engine`, pool-size settings, `market_data_base_url` / `model_registry_base_url` / `feature_store_base_url` (`AnyHttpUrl`), an optional `risk_kernel_base_url`, `upstream_request_timeout_seconds`, and `result_artifact_dir` — all overridable via `AQROS_*` env vars, mirroring `aqros_model_registry.config`.
  - _Requirements: 42.4, 42.5_
- [~] 8.2 Implement `app.py`: module-level `Settings()`, `create_engine`/`create_session_factory`, a `HealthRegistry` registering `database` (via `db.ping`) and `artifact_store` (result-artifact store reachable/writable), and a `_build_app()` wrapping `aqros_core.app.create_app`'s lifespan to attach the three httpx-backed upstream clients, the session factory, and the `LocalResultArtifactStore` to `app.state` (closing the httpx clients and disposing the engine on shutdown), run backtests as background tasks, and include the `runs` and `results` routers.
  - _Requirements: 41.1, 41.2, 13 (async execution), 33.1_
- [~] 8.3 Implement `main.py`: trivial uvicorn entrypoint reading `Settings()` and running `aqros_backtesting_engine.app:app` — copy `aqros_model_registry.main`'s pattern.
  - _Requirements: 42.1_

## 9. Docker & Compose Integration

- [~] 9.1 Update the root `docker-compose.yml`: add the `backtesting-engine-db` Postgres service (port 5437, DB `aqros_backtesting_engine`) and a new `backtesting-engine` service exposing port 8010 with `AQROS_DATABASE_URL`, `AQROS_MARKET_DATA_BASE_URL=http://market-data:8002`, `AQROS_MODEL_REGISTRY_BASE_URL=http://model-registry:8004`, `AQROS_FEATURE_STORE_BASE_URL=http://feature-store:8003`, `AQROS_RESULT_ARTIFACT_DIR`, a `backtesting-engine-artifacts` volume, and `depends_on: backtesting-engine-db (service_healthy), market-data / model-registry / feature-store (service_started)`, plus the `backtesting-engine-db-data` and `backtesting-engine-artifacts` named volumes — using the exact block in design.md's Docker Deployment section.
  - _Requirements: 42.2, 42.3, 42.4_
- [~] 9.2 Add `"aqros_backtesting_engine"` (and, if not already added in task 1.1, `"aqros_strategy_core"`) to the root `pyproject.toml`'s `[tool.ruff.lint.isort].known-first-party` list.
  - _Requirements: 44.1_
- [~] 9.3 Verify (no edit expected) that `backend/backtesting-engine` and `libs/aqros-strategy-core` are picked up by the existing `[tool.uv.workspace] members = ["libs/*", "backend/*"]` glob and that `docker/Dockerfile.service`'s parameterized `SERVICE`/`MODULE`/`PORT` build args require no changes to build this service.
  - _Requirements: 42.1_

## 10. Unit Tests (fakes for every port + property-based tests)

- [~] 10.1 Create fakes: `FakeMarketDataClient`, `FakeModelRegistryClient`, `FakeFeatureStoreClient`, `FakeResultArtifactStore`, `FakeBacktestRunRepository`, `FakeClock`, and `FakeCalendarProvider` implementing every port from `domain/ports.py` in-memory, for use across all unit tests.
  - _Requirements: 43.1_
- [~] 10.2 `tests/unit/test_calendar.py`: property tests for **Property 21** (trading-calendar determinism; exchange-aware, DST-safe timestamps; weekends/holidays excluded, half-days applied), plus concrete DST-transition and half-day examples.
  - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6_
- [~] 10.3 `tests/unit/test_replay_ordering.py`: property tests for **Property 4** (total, deterministic event ordering) and **Property 6** (bar knowledge-time convention: a bar dated D is visible only at/after its session close), plus concrete tie-break examples.
  - _Requirements: 3.5, 5.2, 5.3, 6.1, 6.2, 6.3, 6.4_
- [~] 10.4 `tests/unit/test_lookahead.py`: property tests for **Property 5** (no look-ahead; the guard rejects any read whose knowledge time exceeds the clock and fails the run with a diagnostic).
  - _Requirements: 4.1, 4.4, 5.1, 5.4, 37.3_
- [~] 10.5 `tests/unit/test_latency.py`: property tests for **Property 13** (latency participates in deterministic replay without introducing look-ahead; fill eligibility = emission + modeled latency; stochastic latency from the seeded RNG), covering zero/fixed/configurable models.
  - _Requirements: 15.1, 15.2, 15.3, 15.4_
- [~] 10.6 `tests/unit/test_slippage_commission.py`: property tests for **Property 14** (slippage and commission are deterministic and reflected net; commission debited from cash and included in the trade log and net metrics).
  - _Requirements: 16.1, 16.2, 16.3, 16.4, 17.1, 17.2, 17.3, 17.4_
- [~] 10.7 `tests/unit/test_fills.py`: property tests for **Property 12** (execution only via the Fill_Model; deterministic client order ids never duplicated on re-processing) and **Property 15** (deterministic fills; limit-price honored; partial fills capped at point-in-time liquidity; unfilled/partial recorded rather than assumed complete).
  - _Requirements: 14.1, 14.2, 14.4, 14.5, 18.1, 18.3, 18.4, 18.5, 45.6_
- [~] 10.8 `tests/unit/test_corporate_actions.py`: property tests for **Property 20** (corporate actions applied point-in-time and sourced only from Market Data; splits/dividends/symbol-changes/mergers/delistings adjust positions and cash consistently; never synthesized or applied before knowable).
  - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 45.3_
- [~] 10.9 `tests/unit/test_portfolio_cash.py`: property tests for **Property 16** (position reconstruction solely from the ordered fills), **Property 17** (portfolio value = cash + point-in-time position value), **Property 18** (cash-ledger consistency with the trade log via exact Decimal arithmetic), and **Property 19** (deterministic margin/leverage/buying-power and deterministic forced liquidation on maintenance breach; leverage disabled by default blocks breaching fills).
  - _Requirements: 19.1, 19.2, 19.3, 19.4, 20.1, 20.2, 20.3, 20.4, 21.1, 21.2, 21.3, 21.4, 21.5, 22.1, 22.2, 22.3, 22.4, 22.5_
- [~] 10.10 `tests/unit/test_metrics.py`: property tests for **Property 22** (deterministic performance/risk/drawdown; undefined metrics reported as explicitly undefined) and **Property 23** (deterministic benchmark comparison from Market Data only, or absent without failing).
  - _Requirements: 23.1, 23.3, 23.4, 24.1, 24.3, 27.1, 27.2, 27.3, 28.1, 28.2, 28.3, 28.4_
- [~] 10.11 `tests/unit/test_simulation.py`: property tests for **Property 11** (the simulation drives the shared `aqros_strategy_core`, never a fork; differs from paper/live only in data source and fill mechanism) and **Property 26** (the Risk Kernel is never bypassed: every order routes through the shared `RiskCheck` before any fill, no bypass/relax path exists, no kernel limit is modified, and rejections are recorded while the run continues), against the fakes.
  - _Requirements: 1.2, 1.3, 13.1, 34.1, 34.2, 34.3, 34.4, 34.5, 45.5, 45.8_
- [~] 10.12 `tests/unit/test_services.py`: property/example tests for **Property 7** (model resolution only via the Registry — production default or explicit pin — recorded with checksum + lineage; fail if none), **Property 8** (features requested point-in-time via `as_of = clock`), **Property 10** (Run_Manifest completeness and immutability), **Property 24** (stable, immutable Run_Uuid referenced by all artifacts), and **Property 29** (invalid configurations rejected before a run begins), against the fakes.
  - _Requirements: 4.3, 9.1, 9.2, 9.3, 9.4, 9.6, 9.7, 10.1, 10.2, 12.1, 12.2, 12.3, 12.4, 29.1, 29.2, 29.3, 29.4, 30.1, 30.2, 30.3, 32.1, 32.2, 32.3, 45.4_
- [~] 10.13 `tests/unit/test_determinism.py`: property test for **Property 3** (byte-identical replay: two runs with the same Run_Manifest produce identical trade log, fills, positions, cash, portfolio snapshots, equity curve, drawdown, and metrics) plus a deterministic **golden-replay** test running a fixed configuration against fixed historical data and a fixed model and asserting the `Backtest_Result` matches a stored golden result exactly.
  - _Requirements: 25.1, 25.2, 25.3, 25.4, 31.1, 31.2, 31.3, 31.4, 43.3_
- [~] 10.14 Configure every property test above with `hypothesis`, `@settings(max_examples=100)` minimum, and a `# Feature: backtesting-engine, Property N: <property text>` comment tag directly above each property test function, per design.md's Testing Strategy.
  - _Requirements: 43.2_

## 11. Integration Tests

- [~] 11.1 Create `tests/integration/conftest.py`: `postgres_container` (testcontainers `PostgresContainer`), `engine`, `session_factory`, `db_session` fixtures, and a `client` fixture building the FastAPI app via `httpx.AsyncClient` + `ASGITransport` with `get_session`, `get_market_data_client`, `get_model_registry_client`, and `get_feature_store_client` overridden with in-memory fakes (no live upstreams required), `get_result_artifact_store` bound to a real `LocalResultArtifactStore` on a tmp dir, and `app.state` populated manually since `ASGITransport` never runs the lifespan — mirroring `aqros_model_registry`'s integration conftest.
  - _Requirements: 43.4_
- [~] 11.2 Create `tests/integration/test_api.py`: end-to-end happy path (submit config → run completes → get status/result/manifest → verify metrics/equity curve/trade log) validating **Property 1** (REST-only ingestion; Training Pipeline never contacted) and **Property 5** (no look-ahead) end-to-end; a reproducibility test asserting a re-run from the same manifest reproduces the result identically (**Property 25**); 404-path tests for every endpoint (**Property 30**); an artifact-integrity test (**Property 27**); and an immutability test asserting a `COMPLETED` run's result/manifest cannot be mutated (**Property 28**).
  - _Requirements: 1.4, 5.1, 25.1, 27.1, 28.1, 32.2, 33.4, 36.8, 39.3, 40.1, 40.2_
- [~] 11.3 Create `tests/integration/test_repository.py`: exercises the repository against the real Postgres container, asserting the `UNIQUE (run_uuid)` constraint, the write-once/one-per-run behavior of `backtest_results` (a second write is refused), and the append-only behavior of `trade_log_entries` and `equity_points` (**Property 24**, **Property 28**).
  - _Requirements: 30.1, 38.2, 38.3, 40.2, 40.4_
- [~] 11.4 Create `tests/integration/test_migrations.py`: runs the Alembic `0001_initial_schema` upgrade/downgrade against the testcontainers Postgres, mirroring `aqros_model_registry`'s own `test_migrations.py`.
  - _Requirements: 43.4_
- [~] 11.5 Create `tests/test_health.py`: concrete examples covering Requirement 41.1's readiness composition (database + result-artifact store healthy → 200; one check failing → 503).
  - _Requirements: 41.1_

## 12. Quality Gates & Final Verification

- [~] 12.1 Run `ruff check backend/backtesting-engine libs/aqros-strategy-core` and fix any findings.
  - _Requirements: 44.1_
- [~] 12.2 Run `black --check backend/backtesting-engine libs/aqros-strategy-core` and fix any formatting issues.
  - _Requirements: 44.2_
- [~] 12.3 Run `mypy --strict` against `backend/backtesting-engine/src` and `libs/aqros-strategy-core/src` and resolve any type errors, ensuring every public interface is fully type-hinted.
  - _Requirements: 44.3_
- [~] 12.4 Run the full `pytest` suite for `backend/backtesting-engine` (unit + integration) and confirm a non-zero exit code on any failure or setup error.
  - _Requirements: 43.5_
- [~] 12.5 Build the `backtesting-engine` Docker image via `docker compose build backtesting-engine` and verify `docker compose up` brings up `backtesting-engine` and `backtesting-engine-db` (with its upstreams) and `/health/ready` reports healthy.
  - _Requirements: 42.1, 42.2, 42.3, 41.1_
- [~] 12.6 Traceability check: confirm every requirement (1.1 through 45.8) in `requirements.md` is satisfied by at least one completed task, and that all 30 correctness properties in `design.md` are covered by a test task; confirm the Training Pipeline is never queried, no cross-service database connection is opened, historical market data is never modified, the Risk Kernel is never bypassed, the strategy/risk/OMS logic is not forked (it lives in `libs/aqros-strategy-core`), and no object-store dependency was introduced (local `ResultArtifactStore` adapter only).
  - _Requirements: all (1-45)_
