# Design Document: Backtesting Engine

## Overview

The Backtesting Engine (`backend/backtesting-engine`, module `aqros_backtesting_engine`) is the **simulation plane** of AQROS: a deterministic harness that replays historical market data through the platform's shared strategy, risk, and order-management core to measure how a strategy driven by an approved production model would have behaved, without ever touching real capital. It is the second rung of the trust ladder (research → **backtest** → paper → supervised live → bounded autonomous) and the primary structural defense against overfitting and look-ahead bias before a strategy is allowed near money (Requirements 1, 13; CLAUDE.md §1, §6, §10).

The engine consumes historical market data **exclusively** through the Market Data Service's REST API, resolves models **exclusively** through the Model Registry's REST API (production champions by default), and resolves engineered features **exclusively** through the Feature Store's REST API — always as of the current simulation time. It never opens a database connection to any other service, never queries the Training Pipeline, never modifies historical market data, never retrains a model, and never bypasses the Risk Kernel once that integration exists (Requirements 1.4, 1.5, 2.2, 9.5, 45; CLAUDE.md Hard Rules §7.1, §7.2, §7.3, §7.4, §7.9).

Two properties are engineered structurally rather than merely encouraged. **Determinism**: the same model version(s), historical data, configuration, parameters, code commit, and seed always produce byte-for-byte identical results, because the only source of "now" is an injected `Simulation_Clock`, all stochastic values come from one seeded RNG, all collections are traversed in a total documented order, and all cash arithmetic uses `Decimal` (Requirements 25, 31; CLAUDE.md §5). **Point-in-time correctness**: no decision at simulation time `t` may read any fact whose knowledge time is after `t`; a pure look-ahead guard fails the run on any violation (Requirements 4, 5, 37.3; CLAUDE.md §7.2).

The service follows the domain/adapters/api layering established by `aqros_market_data`, `aqros_feature_store`, `aqros_dataset_builder`, `aqros_training_pipeline`, and `aqros_model_registry`: pure domain logic behind ports, concrete I/O adapters implementing those ports, and a thin FastAPI layer wired by dependency injection. It extends `aqros_core` exactly as the Model Registry does — wrapping `create_app`'s lifespan, attaching its clients, session factory, and result-artifact store to `app.state`, and registering `database` and `artifact_store` readiness checks. To honor "one codebase for backtest, paper, and live" (CLAUDE.md §7.1), the strategy/risk/sizing/OMS contracts do not live here: this design introduces a new shared library `libs/aqros-strategy-core` (module `aqros_strategy_core`), and the Backtesting Engine is its first consumer, never a fork.

Ports and databases continue the platform's sequential allocation: **HTTP port 8010** and a dedicated Postgres `backtesting-engine-db` on **port 5437**, following market-data (8002/5432), feature-store (8003/5433), model-registry (8004/5436), dataset-builder (8008/5434), and training-pipeline (8009/5435). No reserved compose slot exists yet, so this design adds new `backtesting-engine` and `backtesting-engine-db` entries (Requirement 42).

## Key Design Decisions

Each decision resolves an interpretive point the requirements leave open, with its rationale.

### 1. The engine is a deterministic harness around a new shared strategy core, never a fork

**Decision:** Introduce a new shared library `libs/aqros-strategy-core` (module `aqros_strategy_core`) that defines the contracts shared unmodified across backtest, paper, and live: a `Strategy` protocol (`on_event(context) -> list[OrderIntent]`), the `OrderIntent` / `Order` / `Fill` value types, a position-sizing hook, and a `RiskCheck` port (the Risk Kernel seam). The Backtesting Engine invokes this core and supplies only a historical data source and a fill simulator; it never reimplements or forks strategy, risk, sizing, or OMS logic (Requirements 1.2, 1.3, 13, 45.8; CLAUDE.md §7.1).

**Rationale:** The Hard Rule §7.1 demands one codebase for backtest/paper/live, differing only in data source and execution mechanism. The core does not exist yet (only `libs/aqros-core` is present), so the design creates it now with the engine as its first consumer, guaranteeing that a strategy validated in backtest is the exact code that will run in paper and live.

### 2. Determinism is engineered, not hoped for

**Decision:** The simulation depends on an injected `Simulation_Clock` as its sole "now" (no domain component reads wall-clock time), draws every stochastic value (slippage, latency) from a single seeded RNG, imposes a total ordering on every event and every traversal of orders/instruments, and computes all cash with `Decimal`. Identical `Run_Manifest` inputs therefore yield byte-for-byte identical results (Requirements 4.2, 12.4, 15.4, 16.4, 21.3, 25, 31).

**Rationale:** Determinism is a cardinal, non-negotiable property (Requirement 31); any nondeterminism is a defect. Centralizing "now" and randomness and forbidding unordered traversal removes every common source of nondeterminism (wall-clock reads, hash-seed randomization, unordered-collection iteration, floating-point accumulation order).

### 3. Bar knowledge-time convention plus a hard look-ahead guard

**Decision:** Market Data bars carry no `knowledge_time`, so the engine assigns each bar a knowledge time equal to its session close on the exchange calendar (a daily bar dated `D` is knowable only at or after `D`'s close). A strategy sees a bar only once the `Simulation_Clock` reaches that knowledge time; orders emitted from a bar-close decision become fill-eligible no earlier than the next session/bar plus modeled latency. A pure `look_ahead_guard` rejects any read whose knowledge time exceeds the clock and fails the run with a diagnostic (Requirements 4, 5, 5.3, 5.4, 37.3; CLAUDE.md §7.2).

**Rationale:** Look-ahead bias must be structurally impossible, not discouraged. Since the upstream feed gives no explicit knowledge time, the engine adopts a conservative, documented convention (a bar is knowable only at its close) and backs it with a guard that fails loudly, so an impressive backtest can never be the artifact of leaked future information.

### 4. Pull-only REST integration through three ports, each with an in-memory fake

**Decision:** The engine reads all upstream data by pulling from published REST APIs through three ports with adapters and fakes: `MarketDataClient` / `HttpMarketDataClient`, `ModelRegistryClient` / `HttpModelRegistryClient`, and `FeatureStoreClient` / `HttpFeatureStoreClient`. Each is an ABC with an in-memory fake for tests. The engine holds no connection to any service database and never contacts the Training Pipeline or Dataset Builder (Requirements 1.4, 1.5, 2.1, 9.1, 10.1, 45.1; CLAUDE.md §7.9).

**Rationale:** Ports-and-adapters keeps domain logic pure and upstream integrations swappable and fakeable, and the strict one-directional REST dependency honors §7.9 (no cross-service DB access) and §7.4/§7.9 (never query the Training Pipeline).

### 5. Model resolution: production champion by default, explicit pin allowed, always via the Registry

**Decision:** When a configuration names a model without a version, the engine resolves the current production champion via `GET /v1/models/{model_name}/production`; when it pins a version, the engine resolves exactly that version via `GET /v1/models/{model_name}/versions/{version}` and records which was used. The resolved identity, checksum, and lineage are pinned into the manifest. If no production model exists and none is pinned, the run fails. The engine never obtains a model by any other path (Requirements 9, 12.1, 45.4).

**Rationale:** Backtests must reflect governed models and never bypass promotion. Defaulting to the champion mirrors what paper/live would run; allowing a recorded explicit pin supports research on a specific version without weakening governance.

### 6. Corporate actions are sourced only from Market Data and never synthesized

**Decision:** The engine defines `CorporateAction` and a `MarketDataClient.get_corporate_actions(symbol, start, end, as_of)` port method; the HTTP adapter calls Market Data's corporate-actions endpoint **where it exists**. As a recorded assumption: Market Data does **not** expose a corporate-actions endpoint today and MUST NOT be redesigned here. Therefore the engine (a) consumes whatever price-adjustment convention the bars already carry and records that convention in the manifest, and (b) records "corporate actions unavailable" per instrument where no feed exists and proceeds without synthesizing or inferring anything. Splits, reverse splits, dividends, symbol changes, mergers, and delistings are applied point-in-time when present (Requirements 8, 2.2, 45.3).

**Rationale:** Market history has exactly one owner; the engine must never fabricate corporate-action data or mutate market data. Modeling the port now keeps the engine ready for the feed while making the current gap an explicit, auditable dependency rather than a silent omission.

### 7. A deterministic trading calendar with exchange-aware, DST-safe timestamps

**Decision:** A `TradingCalendar` domain component yields deterministic exchange sessions (excluding weekends and holidays, applying half-days). Every `Simulation_Clock` and event timestamp is timezone-aware in the exchange time zone; daylight-saving transitions resolve deterministically from the calendar's rules. The calendar source/version is pinned in the manifest (Requirement 7).

**Rationale:** Replay and decisions must align with when each market was actually open, identically on every run. A pure, versioned calendar makes session boundaries reproducible across holidays and DST transitions.

### 8. An asset-class abstraction with equities as the only MVP implementation

**Decision:** Instruments, positions, fills, and cash effects sit behind an `AssetClass` abstraction so futures, options, forex, and crypto can be added later purely as new implementations, without touching the simulation core loop, event ordering, determinism, or point-in-time logic. Equities is the only MVP implementation (Requirement 11).

**Rationale:** The core must be asset-class agnostic. Isolating asset-specific behavior behind an abstraction prevents equity assumptions from leaking into the core and keeps future asset classes additive.

### 9. A deterministic latency model port, zero-latency by default

**Decision:** A `LatencyModel` port determines the delay between an order's emission and the earliest clock time the fill model may execute it. The MVP default is `ZeroLatency`; `FixedLatency` and `ConfigurableLatency` are also provided. The fill model evaluates an order only against data at or after emission-plus-latency; any stochastic latency draws from the seeded RNG; the selection and parameters are pinned in the manifest (Requirement 15).

**Rationale:** Modeling execution delay must never break determinism or introduce look-ahead. A pluggable port with a seeded stochastic source keeps latency a first-class, reproducible part of replay.

### 10. Pluggable, deterministic fill / slippage / commission models

**Decision:** `FillModel`, `SlippageModel`, and `CommissionModel` are ABCs, each with a zero/trivial default and one non-trivial implementation (e.g. fixed-basis-points slippage, per-share/percentage commission), all selectable via configuration. The `FillModel` composes slippage and commission, caps partial fills at point-in-time liquidity, and supports market and limit orders — filling a limit order only when point-in-time data satisfies its limit price (Requirements 12, 13, 14, 16, 17, 18).

**Rationale:** Execution assumptions must be explicit and swappable without changing strategy code, and every model must be deterministic so identical inputs and seed yield identical fills.

### 11. Margin and leverage behind config, disabled by default

**Decision:** Leverage is disabled by default (MVP). When disabled, buying power equals available cash and any fill that would breach it is blocked and recorded. When enabled, the engine derives buying power from cash, position values, and the leverage limit, tracks margin used and maintenance margin, and performs deterministic forced liquidation on a maintenance breach. The same configuration and core drive both modes, so enabling leverage later requires no core-loop change (Requirement 22).

**Rationale:** The MVP runs unleveraged, but the architecture must support leverage without a rewrite. Expressing both modes through the same config and core keeps the extension purely additive and fully deterministic.

### 12. Immutable run identity, a complete manifest, and a swappable checksummed artifact store

**Decision:** A globally unique, immutable `Run_Uuid` is assigned at creation and referenced by every artifact (Requirement 30). A `RunManifest` pins the code SHA, configuration, resolved model version(s) and their checksums, feature versions, market-data selection with its knowledge-time boundary, calendar source, corporate actions applied, library versions, and seed (Requirement 12). A `ResultArtifactStore` port (`LocalResultArtifactStore` in the MVP, object-store-swappable — mirroring the Model Registry's `ArtifactStore`) persists reports, trade log, equity curve, and manifest, checksum-verified on write and re-verified on read (Requirement 39). Once a run is `COMPLETED`, its results are immutable with no update or delete path (Requirements 38.2, 40).

**Rationale:** Reproducibility and integrity are sacred: any result must reconstruct bit-for-bit from an immutable manifest, and stored artifacts must be tamper-evident and never conflated across runs. Reusing the proven artifact-store pattern preserves the swap-later property with no MVP infrastructure requirement.

### 13. Asynchronous run execution with an explicit status lifecycle

**Decision:** Submitting a configuration returns a `Run_Uuid` immediately; the run executes in the background through the lifecycle `PENDING → RUNNING → COMPLETED | FAILED`. A run that fails or is interrupted is marked `FAILED` and never presents a partial result as complete; recovery is only via a new run with a new UUID unless future checkpointing is added. Runs are independent and deterministic, so future batch/parameter-sweep orchestration adds no core change (Requirements 33, 35, 36.1, 37).

**Rationale:** Long simulations should not block the API, and a broken run must never be mistaken for a valid one. Independent, deterministic runs make batch and parameter sweeps a purely additive future capability.

### 14. Risk Kernel sovereignty and non-bypass

**Decision:** Every `OrderIntent` routes through the shared `RiskCheck` port — the same path paper and live will use — before the fill model may execute it. Where the platform Risk Kernel is configured, the engine submits every order to it and never fills a rejected order. There is no configuration, flag, or code path that bypasses, disables, or relaxes the shared risk checks or the kernel, and the engine never modifies a kernel limit. Rejections are recorded in the trade log and the run continues (Requirements 13.3, 13.4, 34, 45.5; CLAUDE.md §7.3).

**Rationale:** A strategy validated in backtest must have been validated under the same guardrails it will face with capital. Routing through the shared risk path with no bypass makes that guarantee structural.

### 15. Purity and layering mirror the existing services

**Decision:** `domain/` is pure (simulation loop, event ordering, portfolio/cash/position, metrics, calendar, corporate-action application, look-ahead guard — no I/O, no wall-clock); `adapters/` holds the HTTP clients, SQLAlchemy repositories, `db.py`, the local result-artifact store, the calendar provider, and the system clock; `api/` holds routes, schemas, and DI. The service reuses the same skeleton, `Clock` port, `db.py`, and repository-per-aggregate pattern as the Model Registry and Training Pipeline (Requirements 31, 41).

**Rationale:** Consistency with the established layering keeps the simulation core and metric logic deterministically unit-testable and the service maintainable like every other AQROS backend.

## 1. Overall Architecture

```mermaid
graph TB
    subgraph "Backtesting Engine Service (port 8010)"
        API[Backtest_API<br/>FastAPI routes]
        SVC[BacktestService /<br/>BacktestQueryService]
        subgraph "Pure Domain (no I/O, no wall-clock)"
            SIM[Simulation_Engine<br/>replay + core loop]
            ORD[Event ordering<br/>total order key]
            LAG[look_ahead_guard]
            PORT[Portfolio / Position /<br/>Cash_Ledger]
            EXE[Fill / Slippage /<br/>Commission / Latency]
            CAL[Trading_Calendar]
            CA[Corporate-action application]
            MET[Metrics / Drawdown /<br/>Benchmark]
        end
        subgraph "Shared core (libs/aqros_strategy_core)"
            STR[Strategy protocol]
            RC[RiskCheck port]
            SIZ[Position-sizing hook]
        end
        subgraph Ports
            P1[MarketDataClient]
            P2[ModelRegistryClient]
            P3[FeatureStoreClient]
            P4[ResultArtifactStore]
            P5[BacktestRunRepository]
            P6[Clock]
            P7[CalendarProvider]
        end
        API --> SVC --> SIM
        SIM --> ORD --> LAG
        SIM --> STR --> RC
        STR --> SIZ
        SIM --> EXE --> PORT
        SIM --> CAL
        SIM --> CA
        SVC --> MET
        SIM --> P1 & P2 & P3 & P6 & P7
        SVC --> P4 & P5
    end

    subgraph Adapters
        A1[HttpMarketDataClient]
        A2[HttpModelRegistryClient]
        A3[HttpFeatureStoreClient]
        A4[LocalResultArtifactStore]
        A5[SqlAlchemyBacktestRunRepository]
        A6[SystemClock / SimulationClock]
        A7[StaticCalendarProvider]
    end
    P1 -.implements.-> A1
    P2 -.implements.-> A2
    P3 -.implements.-> A3
    P4 -.implements.-> A4
    P5 -.implements.-> A5
    P6 -.implements.-> A6
    P7 -.implements.-> A7

    A1 -->|"REST only:<br/>GET /v1/instruments/{symbol}/bars"| MD[Market Data Service<br/>port 8002]
    A2 -->|"REST only:<br/>GET /v1/models/.../production"| MR[Model Registry<br/>port 8004]
    A3 -->|"REST only:<br/>GET .../features/{name}?as_of"| FS[Feature Store<br/>port 8003]
    A5 --> PG[(backtesting-engine-db<br/>Postgres :5437)]
    A4 --> VOL[(Local artifact volume<br/>— swappable for object store)]

    TP[Training Pipeline<br/>port 8009]
    SIM -.->|"NEVER queried"| TP
```

The engine's only outbound dependencies are the three upstream REST APIs; it never opens a database connection to any other service and never contacts the Training Pipeline (Requirements 1.5, 45.1; CLAUDE.md §7.9). The `Simulation_Engine` and metric logic are pure: they receive all external data through injected adapters and read "now" only from the injected `Simulation_Clock` (Requirements 4.2, 41.3, 41.4).

## 2. Data Flow

```mermaid
flowchart TD
    A[POST /v1/backtests<br/>Backtest_Configuration + Idempotency-Key] --> B[Validate config<br/>typed 422 on error]
    B -->|valid| C[Assign Run_Uuid<br/>persist PENDING]
    C --> D[Background execution: RUNNING]
    D --> E[Resolve model via Model Registry<br/>production or pinned version]
    E -->|none & unpinned| XF[FAIL: no approved model]
    E --> F[Fetch bars via Market Data<br/>scoped to universe + period]
    F -->|error/unreachable| XU[FAIL: upstream unreachable]
    F --> G[Build Trading_Calendar sessions<br/>assign bar knowledge_time = session close]
    G --> H[Build totally-ordered Event_Stream]
    H --> I[Simulation loop per Event]
    I --> J[Strategy.on_event → OrderIntents<br/>features via Feature Store as_of=clock]
    J --> K[RiskCheck / Risk_Kernel]
    K -->|rejected| L[record rejection in Trade_Log; continue]
    K -->|approved| M[Latency + Fill + Slippage + Commission]
    M --> N[Update Position / Cash_Ledger / Portfolio]
    N --> O[Sample Equity_Curve at interval]
    O -->|next event| I
    I -->|stream exhausted| P[Compute metrics / drawdown / benchmark]
    P --> Q[Assemble Run_Manifest + Backtest_Result]
    Q --> R[Persist results + checksummed artifacts<br/>mark COMPLETED immutable]
```

Every data read inside the loop passes the `look_ahead_guard`: any value whose knowledge time exceeds the current clock is either excluded (feature `as_of` cutoff) or, if a component attempts a raw read past the clock, fails the run with a diagnostic (Requirements 4.4, 5.4, 37.3).

## 3. Component Diagram

| Component | Responsibility | Requirements |
|---|---|---|
| `Backtest_API` | FastAPI routers for submit/status/result/list/manifest; OpenAPI; typed 404s; idempotency keys | 30.4, 36 |
| `BacktestService` | Orchestrate a run: resolve model, drive replay, persist results and artifacts, manage status lifecycle | 1.1, 13, 33, 37 |
| `Simulation_Engine` (`domain/simulation.py`) | Pure core loop: advance clock event by event, dispatch to shared core, route through risk, apply fills, update portfolio | 6, 13, 31 |
| `Historical_Replay` (`domain/replay.py`) | Build the totally-ordered `Event_Stream` from bars + corporate actions + calendar; assign bar knowledge times | 3, 6 |
| `look_ahead_guard` (`domain/lookahead.py`) | Pure guard rejecting any read whose knowledge time > clock; fail run with diagnostic | 4, 5, 37.3 |
| `Trading_Calendar` (`domain/calendar.py`) | Deterministic exchange sessions; exchange-aware, DST-safe timestamps | 7 |
| `Corporate_Actions` (`domain/corporate_actions.py`) | Apply splits/dividends/symbol-changes/mergers/delistings point-in-time from Market Data only | 8 |
| `Latency_Model` / `Slippage_Model` / `Commission_Model` / `Fill_Model` | Pluggable deterministic execution simulation | 12, 14, 15, 16, 17, 18 |
| `Portfolio` / `Position` / `Cash_Ledger` (`domain/portfolio.py`) | Decimal cash, signed positions, point-in-time valuation, margin/leverage | 19, 20, 21, 22 |
| `Metrics` (`domain/metrics.py`) | Performance, risk, drawdown, benchmark comparison — deterministic, from equity curve + trade log | 23, 24, 27, 28 |
| `MarketDataClient` / `ModelRegistryClient` / `FeatureStoreClient` (ports + Http adapters + fakes) | Sole REST channels to upstreams; never a DB; never the Training Pipeline | 1.4, 2, 9, 10 |
| `ResultArtifactStore` (port; `LocalResultArtifactStore` adapter) | Versioned, checksum-verified, swappable artifact bytes traceable to Run_Uuid | 38.5, 39 |
| `BacktestRunRepository` | Postgres persistence: runs, append-only trade log + equity points, write-once results | 38, 40 |
| `Clock` (port; `SimulationClock` / `SystemClock`) | Injected "now"; simulation clock advances only on events | 4.2, 6.3, 31 |
| Shared `Strategy` / `RiskCheck` / sizing (`libs/aqros_strategy_core`) | Strategy decisions, risk seam, sizing — shared unmodified with paper/live | 1.2, 13, 34, 45.8 |

## 4. Domain Model

`domain/models.py` — pure, frozen/slots dataclasses and `StrEnum`s, no I/O, mirroring `aqros_model_registry.domain.models` conventions. All upstream payloads (bars, model records, features) are **local decoupled copies**; `aqros_market_data`, `aqros_model_registry`, `aqros_feature_store`, and `aqros_training_pipeline` are never imported (CLAUDE.md §7.9). The `Strategy`, `OrderIntent`, `Order`, `Fill`, and `RiskCheck` contracts are **referenced from** `libs/aqros_strategy_core`, not redefined here (Decision 1).

```python
class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

class AssetClass(StrEnum):
    EQUITY = "equity"
    # reserved future implementations — added behind the abstraction, no core change (Req 11):
    FUTURE = "future"
    OPTION = "option"
    FOREX = "forex"
    CRYPTO = "crypto"

class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"

class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"

class OrderStatus(StrEnum):
    PENDING = "pending"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    REJECTED = "rejected"
    UNFILLED = "unfilled"

class CorporateActionType(StrEnum):
    SPLIT = "split"
    REVERSE_SPLIT = "reverse_split"
    CASH_DIVIDEND = "cash_dividend"
    STOCK_DIVIDEND = "stock_dividend"
    SYMBOL_CHANGE = "symbol_change"
    MERGER = "merger"
    DELISTING = "delisting"

class EventKind(StrEnum):
    CORPORATE_ACTION = "corporate_action"   # applied before market data at the same instant
    MARKET_BAR = "market_bar"
    ORDER_ELIGIBLE = "order_eligible"        # a latency-delayed order becomes fill-eligible
    EQUITY_SAMPLE = "equity_sample"

@dataclass(frozen=True, slots=True)
class BacktestConfiguration:
    strategy_id: str
    strategy_params: dict[str, object]
    model_name: str
    model_version: int | None            # None => resolve production champion (Req 9.2)
    universe: tuple[str, ...]
    exchange: str
    start: datetime                      # timezone-aware, exchange TZ
    end: datetime
    starting_cash: Decimal
    bar_interval: str                    # e.g. "daily"
    slippage_model: str
    slippage_params: dict[str, object]
    commission_model: str
    commission_params: dict[str, object]
    fill_model: str
    fill_params: dict[str, object]
    latency_model: str                   # default "zero"
    latency_params: dict[str, object]
    leverage_enabled: bool               # MVP default False (Req 22.2)
    max_leverage: Decimal
    equity_sample_interval: str
    benchmark_symbol: str | None
    seed: int                            # assigned + recorded if omitted (Req 29.4)
    asset_class: AssetClass = AssetClass.EQUITY

@dataclass(frozen=True, slots=True)
class ResolvedModel:
    model_name: str
    version: int
    checksum: str
    checksum_algorithm: str
    lineage: dict[str, object]           # local decoupled copy from the Registry
    resolved_as: str                     # "production" | "pinned"

@dataclass(frozen=True, slots=True)
class RunManifest:
    run_uuid: UUID
    engine_git_commit: str
    strategy_core_git_commit: str
    configuration: BacktestConfiguration
    resolved_models: tuple[ResolvedModel, ...]
    feature_versions: dict[str, int]
    universe: tuple[str, ...]
    period_start: datetime
    period_end: datetime
    knowledge_time_boundary: datetime    # no fact after this is ever visible
    calendar_source: str                 # calendar source/version (Req 7.6)
    price_adjustment_convention: str     # convention carried by the bars (Decision 6)
    corporate_actions_applied: tuple[str, ...]
    corporate_actions_unavailable: tuple[str, ...]   # instruments with no feed (Decision 6)
    library_versions: dict[str, str]
    seed: int

@dataclass(frozen=True, slots=True)
class Instrument:
    symbol: str
    asset_class: AssetClass
    exchange: str

@dataclass(frozen=True, slots=True)
class Bar:                               # local decoupled copy of Market Data OHLCV
    symbol: str
    event_time: datetime                 # bar timestamp as provided by Market Data
    knowledge_time: datetime             # derived = session close (Decision 3)
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

@dataclass(frozen=True, slots=True)
class CorporateAction:
    symbol: str
    action_type: CorporateActionType
    event_time: datetime
    knowledge_time: datetime
    ratio: Decimal | None                # split / reverse-split factor
    cash_amount: Decimal | None          # cash dividend per share
    successor_symbol: str | None         # symbol change / merger target
    source: str                          # always "market-data" (Req 8.7)

@dataclass(frozen=True, slots=True)
class Event:
    event_time: datetime
    knowledge_time: datetime
    kind: EventKind
    sequence: int                        # stable ingest sequence for total ordering
    payload: object                      # Bar | CorporateAction | order id | sample marker

    @property
    def ordering_key(self) -> tuple[datetime, int, int]:
        """Total, documented tie-break: (event_time, kind priority, sequence)."""
        return (self.event_time, _KIND_PRIORITY[self.kind], self.sequence)

@dataclass(frozen=True, slots=True)
class OrderIntent:                       # referenced shape from aqros_strategy_core
    client_order_id: str                 # deterministic; re-processing never duplicates (Req 14.2)
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    limit_price: Decimal | None
    emitted_at: datetime

@dataclass(frozen=True, slots=True)
class SimulatedOrder:
    client_order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    limit_price: Decimal | None
    emitted_at: datetime
    eligible_at: datetime                # emitted_at + modeled latency (Req 15.1)
    status: OrderStatus
    reject_reason: str | None

@dataclass(frozen=True, slots=True)
class Fill:
    client_order_id: str
    symbol: str
    side: OrderSide
    quantity: Decimal                    # filled quantity (may be partial, Req 14.5)
    price: Decimal                       # slippage-adjusted execution price
    commission: Decimal
    filled_at: datetime

@dataclass(frozen=True, slots=True)
class Position:
    symbol: str
    quantity: Decimal                    # signed; 0 == flat (Req 19.3)
    average_cost: Decimal
    realized_pnl: Decimal

@dataclass(frozen=True, slots=True)
class CashLedger:
    balance: Decimal                     # Decimal arithmetic only (Req 21.3)
    starting_cash: Decimal

@dataclass(frozen=True, slots=True)
class Portfolio:
    cash: CashLedger
    positions: tuple[Position, ...]      # ordered by symbol for determinism
    as_of: datetime

@dataclass(frozen=True, slots=True)
class TradeLogEntry:
    sequence: int
    client_order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    price: Decimal | None
    commission: Decimal
    clock_time: datetime
    outcome: OrderStatus                 # emitted | filled | partially_filled | rejected | unfilled
    reason: str | None

@dataclass(frozen=True, slots=True)
class EquityPoint:
    clock_time: datetime
    total_value: Decimal

@dataclass(frozen=True, slots=True)
class DrawdownSummary:
    max_drawdown: Decimal
    max_drawdown_start: datetime
    max_drawdown_trough: datetime
    max_drawdown_duration: timedelta

@dataclass(frozen=True, slots=True)
class PerformanceMetrics:
    total_return: Decimal
    annualized_return: Decimal
    sharpe_ratio: Decimal | None         # None => explicitly undefined (Req 23.4)
    sortino_ratio: Decimal | None
    win_rate: Decimal

@dataclass(frozen=True, slots=True)
class RiskMetrics:
    volatility: Decimal
    max_drawdown: Decimal
    value_at_risk: Decimal
    gross_exposure: Decimal
    net_exposure: Decimal

@dataclass(frozen=True, slots=True)
class BenchmarkComparison:
    benchmark_symbol: str
    benchmark_return: Decimal
    excess_return: Decimal

@dataclass(frozen=True, slots=True)
class BacktestResult:
    run_uuid: UUID
    manifest: RunManifest
    status: RunStatus
    trade_log: tuple[TradeLogEntry, ...]
    equity_curve: tuple[EquityPoint, ...]
    drawdown: DrawdownSummary
    performance: PerformanceMetrics
    risk: RiskMetrics
    benchmark: BenchmarkComparison | None
    final_portfolio: Portfolio
    failure_reason: str | None
```

**Departure note:** bar, model-record, and feature shapes are local decoupled dataclasses rather than imports from the upstream service packages, exactly as the Model Registry duplicates the Training Pipeline's shapes; the strategy/risk/sizing contracts are imported from `libs/aqros_strategy_core` so backtest, paper, and live share one definition (CLAUDE.md §7.1, §7.9).

## 5. Repository Structure

```
backend/backtesting-engine/
├── README.md
├── pyproject.toml
├── alembic.ini
├── migrations/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── 0001_initial_schema.py
├── src/aqros_backtesting_engine/
│   ├── __init__.py
│   ├── py.typed
│   ├── config.py                # Settings(BaseServiceSettings): port 8010, DB url, upstream URLs, artifact dir
│   ├── app.py                   # wraps aqros_core.create_app lifespan; attaches clients + store; readiness checks
│   ├── main.py
│   ├── domain/
│   │   ├── __init__.py
│   │   ├── models.py            # Section 4
│   │   ├── ports.py             # MarketDataClient, ModelRegistryClient, FeatureStoreClient, ResultArtifactStore, BacktestRunRepository, Clock, CalendarProvider
│   │   ├── calendar.py          # Trading_Calendar (pure, deterministic sessions, DST-safe)
│   │   ├── events.py            # Event, EventKind, total ordering key + priorities
│   │   ├── replay.py            # Historical_Replay: build ordered Event_Stream, assign bar knowledge_time
│   │   ├── latency.py           # LatencyModel ABC + ZeroLatency / FixedLatency / ConfigurableLatency
│   │   ├── slippage.py          # SlippageModel ABC + ZeroSlippage / FixedBpsSlippage
│   │   ├── commission.py        # CommissionModel ABC + ZeroCommission / PerShareCommission / PctNotionalCommission
│   │   ├── fills.py             # FillModel ABC + ImmediateFillModel / LiquidityCappedFillModel
│   │   ├── corporate_actions.py # point-in-time application of splits/dividends/etc.
│   │   ├── portfolio.py         # Position, CashLedger, Portfolio, margin/leverage, valuation
│   │   ├── metrics.py           # performance, risk, drawdown, benchmark (pure, deterministic)
│   │   ├── lookahead.py         # look_ahead_guard (pure)
│   │   ├── simulation.py        # Simulation_Engine core loop (pure)
│   │   └── services.py          # BacktestService, BacktestQueryService
│   ├── adapters/
│   │   ├── __init__.py
│   │   ├── db.py                # engine/session_factory/session_scope/ping — identical pattern
│   │   ├── orm.py               # BacktestRunORM, TradeLogEntryORM, EquityPointORM, BacktestResultORM
│   │   ├── repository.py        # SqlAlchemyBacktestRunRepository
│   │   ├── market_data_client.py    # HttpMarketDataClient (+ bars, corporate actions where present)
│   │   ├── model_registry_client.py # HttpModelRegistryClient (production + version pin + checksum/lineage)
│   │   ├── feature_store_client.py  # HttpFeatureStoreClient (as_of point-in-time reads)
│   │   ├── local_result_artifact_store.py  # LocalResultArtifactStore (bytes, versioned, checksummed — swappable)
│   │   ├── calendar_provider.py     # StaticCalendarProvider (exchange calendars, versioned)
│   │   └── clock.py             # SystemClock (adapters) + SimulationClock (injected into the domain)
│   └── api/
│       ├── __init__.py
│       ├── schemas.py           # Pydantic request/response + ErrorResponse
│       ├── deps.py              # DI wiring off app.state / request-scoped session
│       └── routes/
│           ├── __init__.py
│           ├── runs.py          # POST /v1/backtests, GET status, GET list, GET manifest
│           └── results.py       # GET /v1/backtests/{run_uuid}/result
└── tests/
    ├── __init__.py
    ├── test_health.py
    ├── unit/
    │   ├── __init__.py
    │   ├── test_calendar.py
    │   ├── test_replay_ordering.py
    │   ├── test_lookahead.py
    │   ├── test_latency.py
    │   ├── test_slippage_commission.py
    │   ├── test_fills.py
    │   ├── test_corporate_actions.py
    │   ├── test_portfolio_cash.py
    │   ├── test_metrics.py
    │   ├── test_simulation.py
    │   └── test_determinism.py         # golden-replay + property tests
    └── integration/
        ├── __init__.py
        ├── conftest.py          # testcontainers Postgres + real LocalResultArtifactStore + faked upstream clients
        ├── test_api.py
        ├── test_repository.py
        └── test_migrations.py
```

### 5.1 Repository-pattern persistence design

`BacktestRunRepository` (port in `domain/ports.py`, adapter `SqlAlchemyBacktestRunRepository`):
- `create_run(config, manifest_stub, run_uuid) -> None` — insert a `PENDING` run (write-once identity).
- `set_status(run_uuid, status, failure_reason=None)` — advance the lifecycle only (`PENDING→RUNNING→COMPLETED|FAILED`).
- `append_trade_log(run_uuid, entries)` — append-only; no update/delete path (Requirement 38.3).
- `append_equity_points(run_uuid, points)` — append-only.
- `write_result(run_uuid, result, manifest)` — one-per-run, written once; refused if a result already exists (Requirements 38.2, 40.4).
- `get_run(run_uuid)`, `get_result(run_uuid)`, `get_manifest(run_uuid)`, `list_runs(strategy_id=None, model_name=None, status=None)`.

Repositories translate ORM rows to/from frozen domain dataclasses via private `_to_domain_*` helpers, take an `AsyncSession` via the constructor, and never `commit()` (the request-scoped session owns the transaction), exactly as the Model Registry and Training Pipeline repositories do. Large artifacts (long trade logs, equity curves, reports, manifests) live behind the `ResultArtifactStore`, not as Postgres blobs (Requirement 38.5).

### 5.2 The new shared library `libs/aqros-strategy-core`

Introduced by this design (Decision 1). Module `aqros_strategy_core` defines the contracts shared unmodified across backtest, paper, and live so no consumer forks the money-path logic (CLAUDE.md §7.1):

```
libs/aqros-strategy-core/
├── pyproject.toml
└── src/aqros_strategy_core/
    ├── __init__.py
    ├── py.typed
    ├── contracts.py     # OrderIntent, Order, Fill value types (shared shapes)
    ├── strategy.py      # Strategy protocol: on_event(context) -> list[OrderIntent]
    ├── sizing.py        # PositionSizer hook (confidence-aware sizing seam)
    └── risk.py          # RiskCheck port: check(order_intent, context) -> RiskDecision
```

- `Strategy` is a `typing.Protocol` with `on_event(context: StrategyContext) -> list[OrderIntent]`; `StrategyContext` exposes only point-in-time-correct market data, features, and resolved model outputs (never wall-clock, never future data).
- `RiskCheck` is the seam the Risk Kernel implements; the engine supplies the same `RiskCheck` path paper/live will use (Requirement 34).
- The Backtesting Engine is the **first consumer**; the shared library owns these contracts and the engine references them (never redefines or forks them). Root `pyproject.toml` `known-first-party` gains both `aqros_strategy_core` and `aqros_backtesting_engine` (implementation-phase edit).

## 6. Upstream Integration (REST only)

All upstream reads go through ports with `Http*` adapters and in-memory fakes; the engine never opens a service database and never contacts the Training Pipeline or Dataset Builder (Requirements 1.4, 1.5, 45.1; CLAUDE.md §7.9). Every external call carries a timeout and an idempotent retry policy (CLAUDE.md §5); any error or unreachability on data essential to a run fails the run (Requirements 2.4, 10.4, 37.1).

| Port | Adapter | Upstream endpoints (published REST) | Requirements |
|---|---|---|---|
| `MarketDataClient` | `HttpMarketDataClient` (Market Data, 8002) | `GET /v1/instruments/{symbol}/bars?start&end&interval&limit&offset` (paginated OHLCV; interval enum, daily default); `GET /v1/instruments/{symbol}`; `GET /v1/instruments`; `get_corporate_actions(...)` calls the corporate-actions endpoint **where it exists** | 2, 3, 8, 28.1 |
| `ModelRegistryClient` | `HttpModelRegistryClient` (Model Registry, 8004) | `GET /v1/models/{model_name}/production`; `GET /v1/models/{model_name}/versions/{version}`; `.../metrics`; `.../lineage`; `.../artifact` | 9 |
| `FeatureStoreClient` | `HttpFeatureStoreClient` (Feature Store, 8003) | `GET /v1/instruments/{symbol}/features/{feature_name}?feature_version&start&end&as_of&limit&offset` (real point-in-time `knowledge_time <= as_of`); `GET /v1/definitions`; `GET /v1/definitions/{name}` | 10 |

**Recorded assumptions / dependencies (do not redesign the upstream services here):**
- The Market Data bars endpoint has **no** knowledge-time / `as_of` parameter, so the engine derives each bar's knowledge time from the exchange session close (Decision 3) rather than requesting a point-in-time cutoff from Market Data.
- Market Data exposes **no** corporate-actions endpoint today. The `get_corporate_actions` port is defined and the adapter calls the endpoint where it exists; where it does not, the engine records "corporate actions unavailable" for that instrument, consumes the price-adjustment convention already carried by the bars (recording that convention in the manifest), and proceeds without synthesizing or inferring corporate-action data (Decision 6; Requirements 8.7, 45.3).
- Feature Store `as_of` is a **real** point-in-time cutoff; the engine always passes `as_of = current Simulation_Clock time` so only values with `knowledge_time <= as_of` are returned (Requirements 4.3, 10.2).
- The engine never queries the Training Pipeline for any purpose (Requirements 9.5, 45.1).

### 6.1 Model resolution

```mermaid
flowchart TD
    A[Config references model_name] --> B{explicit version pinned?}
    B -->|no| C[GET /v1/models/{name}/production]
    C -->|found| E[ResolvedModel resolved_as=production]
    C -->|none| XF[FAIL run: no approved model available]
    B -->|yes| D[GET /v1/models/{name}/versions/{version}]
    D -->|found| F[ResolvedModel resolved_as=pinned]
    D -->|missing| XF2[FAIL run: pinned version not found]
    E --> G[Fetch checksum + lineage; pin into RunManifest]
    F --> G
```

The engine obtains the model only from the Registry — never from the Training Pipeline, a filesystem path, or retraining — and records the resolved version's identity, checksum, and lineage in the manifest (Requirements 9.4, 9.7, 45.4).

## 7. Simulation Loop

```mermaid
sequenceDiagram
    participant Svc as BacktestService
    participant Rep as Historical_Replay
    participant Clk as Simulation_Clock
    participant Guard as look_ahead_guard
    participant Str as Strategy (shared core)
    participant FS as FeatureStoreClient
    participant Risk as RiskCheck / Risk_Kernel
    participant Lat as Latency_Model
    participant Fill as Fill_Model (+slippage+commission)
    participant Pf as Portfolio / Cash_Ledger

    Svc->>Rep: build Event_Stream (bars + corp actions + samples), totally ordered
    loop for each Event in ordering-key order
        Rep->>Clk: advance to Event.event_time (never ahead)
        alt Event is corporate action (knowledge_time <= clock)
            Rep->>Pf: apply split/dividend/symbol-change point-in-time
        else Event is market bar (knowledge_time <= clock)
            Rep->>Str: on_event(context: PIT data + features + model)
            Str->>FS: get features as_of = clock
            FS-->>Guard: values (knowledge_time <= clock enforced)
            Str-->>Svc: list[OrderIntent] (deterministic client_order_ids)
            loop for each OrderIntent (ordered)
                Svc->>Risk: check(intent, context)
                alt rejected
                    Svc->>Pf: record rejection in Trade_Log; continue
                else approved
                    Svc->>Lat: eligible_at = emitted_at + latency
                    Note over Fill: fill only against data at/after eligible_at
                    Svc->>Fill: fill(order, PIT data, slippage, commission)
                    Fill-->>Pf: Fill(qty, price, commission)
                    Pf->>Pf: update Position, Cash_Ledger, Portfolio
                end
            end
        else Event is equity sample
            Rep->>Pf: sample total portfolio value -> Equity_Curve
        end
    end
    Svc->>Svc: compute metrics/drawdown/benchmark; assemble Result + Manifest
```

Per Event, the engine applies effects fully — decisions, orders, risk checks, fills, portfolio updates, and any equity sample — before advancing the clock to the next Event; it never reorders, drops, or coalesces Events based on wall-clock time or unordered iteration (Requirements 6.3, 6.4, 13.1, 20.3).

## 8. Point-in-Time Correctness and Look-Ahead Prevention

- **Bar knowledge-time convention (Decision 3):** each bar's knowledge time is its session close on the exchange calendar; a bar dated `D` is visible only once the clock reaches `D`'s close. Orders decided at a bar close become fill-eligible no earlier than the next session/bar plus modeled latency, so no decision fills at a price it could not have transacted at (Requirements 5.2, 5.3).
- **Feature reads:** every feature request passes `as_of = current clock`, and the Feature Store returns only values with `knowledge_time <= as_of` (Requirements 4.3, 10.2).
- **The guard (`domain/lookahead.py`):** a pure function `assert_knowable(knowledge_time, clock)` that any component funnels reads through; if `knowledge_time > clock` it raises, and the service fails the run with a diagnostic naming the offending access rather than silently proceeding (Requirements 4.4, 5.4, 37.3).
- **No wall-clock:** the `Simulation_Engine` and shared core read "now" only from the injected `Simulation_Clock`; the domain never calls `datetime.now()` (Requirements 4.2, 41.4).

## 9. Event Ordering and Determinism

- **Total ordering key** (`domain/events.py`): `(event_time, kind_priority, sequence)`, where `kind_priority` fixes intra-instant order (corporate action → market bar → order-eligible → equity sample) and `sequence` is a stable ingest counter breaking any remaining tie. This total, documented rule makes relative order identical on every run (Requirements 6.1, 6.2, 31.4).
- **Single seeded RNG:** every stochastic value (slippage jitter, stochastic latency) is drawn from one seeded `random.Random(seed)`; the seed is recorded in the manifest and assigned explicitly if the config omits it (Requirements 15.4, 16.4, 25.2, 29.4).
- **Decimal arithmetic:** cash, notional, commissions, and position cost use `Decimal` so identical inputs yield identical balances with no floating-point ordering nondeterminism (Requirement 21.3).
- **Ordered traversal:** positions are keyed by symbol and always traversed in sorted order; order intents are processed in emission order; no unordered set/dict iteration influences a result (Requirements 31.3, 31.4).

## 10. Order Execution, Latency, Slippage, Commission, and Fills

- **Execution only via the Fill_Model** — never a live venue or broker (Requirements 14.1, 45.6). Each `OrderIntent` carries a deterministic `client_order_id`, so re-processing the same Event never produces a duplicate order (Requirement 14.2).
- **Latency (`domain/latency.py`):** `LatencyModel.eligible_time(emitted_at, rng) -> datetime`; `ZeroLatency` (MVP default) returns `emitted_at`; `FixedLatency` adds a constant; `ConfigurableLatency` may draw a stochastic delay from the seeded RNG. The fill model evaluates an order only against data whose event time is at or after `eligible_at`, preserving point-in-time correctness (Requirements 15.1–15.4).
- **Slippage (`domain/slippage.py`):** `SlippageModel.adjust(reference_price, order, rng) -> Decimal`; `ZeroSlippage` returns the reference; `FixedBpsSlippage` moves the price adversely by a basis-points parameter; any stochastic component draws from the seeded RNG (Requirements 16.1–16.4).
- **Commission (`domain/commission.py`):** `CommissionModel.cost(fill) -> Decimal`; `ZeroCommission`; `PerShareCommission`; `PctNotionalCommission`. Commission is debited from the `Cash_Ledger` and recorded in the trade log and all net metrics (Requirements 17.1–17.4).
- **Fills (`domain/fills.py`):** `FillModel.fill(order, pit_data, slippage, commission, rng) -> Fill | None` composes slippage and commission, fills a limit order only when point-in-time data satisfies its limit price, and caps the filled quantity at the point-in-time liquidity the model supports — recording an unfilled or partially-filled outcome rather than assuming a complete fill (Requirements 14.4, 14.5, 18.1, 18.5). All models are deterministic: identical inputs, parameters, and seed produce identical fills (Requirements 16.3, 17.3, 18.3).

## 11. Position, Portfolio, Cash, Margin, and Leverage

- **Position (`domain/portfolio.py`):** each fill updates the signed quantity and average cost basis; realized P&L is computed on closing/reducing trades and unrealized P&L from the current point-in-time price. Long and short are supported; a flat position is zero quantity. Position state derives solely from the ordered fill sequence, so replaying the same fills reconstructs identical positions (Requirement 19).
- **Portfolio:** all positions plus the cash ledger; total value = cash balance + point-in-time market value of all positions; updated after every fill and cash movement before the clock advances; the final portfolio is part of the result (Requirement 20).
- **Cash:** initialized to `starting_cash`; each fill debits/credits the notional and debits the commission using `Decimal`; ending cash equals starting cash adjusted by every recorded fill notional and commission, keeping the ledger consistent with the trade log (Requirement 21).
- **Margin/leverage (Decision 11):** buying power is derived from cash, position values, and the leverage parameters. With leverage disabled (MVP default), buying power is constrained to available cash and any fill that would breach it (or drive cash below the configured minimum) is blocked and the blocking constraint recorded. With leverage enabled, the engine tracks margin used and maintenance margin and performs deterministic forced liquidation on a maintenance breach, recording each resulting order and fill in the trade log. Both modes run through the same config and core, so enabling leverage later needs no core-loop change (Requirements 21.4, 22).

## 12. Corporate Actions

- Corporate actions are sourced **only** from Market Data via `get_corporate_actions(symbol, start, end, as_of)` and are never modified or synthesized (Requirements 8.1, 8.7, 45.3).
- Each action carries a knowledge time; the replay applies it point-in-time (using only actions with `knowledge_time <= clock`) and never before it was knowable (Requirement 8.6).
- Splits/reverse splits adjust the affected position's quantity and cost basis and subsequent price references consistently, preserving total portfolio value except for explicitly recorded rounding; cash dividends credit the cash ledger and stock dividends adjust the position; symbol changes and mergers map the position to its successor deterministically; delistings resolve the position per the reported terms and record the resolution in the trade log (Requirements 8.2–8.5).
- The corporate actions applied — and the instruments for which no feed was available — are recorded in the manifest (Requirements 8.8; Decision 6).

## 13. Trading Calendar

- `Trading_Calendar` (`domain/calendar.py`) is a pure component that yields deterministic exchange sessions for a given exchange and period, excluding weekends and holidays and applying half-days (Requirements 7.1, 7.2, 7.5).
- Every clock and event timestamp is timezone-aware in the exchange time zone; daylight-saving transitions resolve deterministically from the calendar's time-zone rules, with no naive or ambiguous local timestamps (Requirements 7.3, 7.4).
- The calendar source/version is provided by the `CalendarProvider` adapter and pinned in the manifest (Requirement 7.6).

## 14. Metrics, Drawdown, and Benchmark

- **Performance (`domain/metrics.py`):** total return, annualized return, Sharpe ratio, Sortino ratio, and win rate, computed net of commissions and slippage from the equity curve and trade log; a metric undefined for a run (e.g. Sharpe when volatility is zero) is reported as explicitly undefined (`None`) rather than a misleading value (Requirement 23).
- **Risk:** return volatility, maximum drawdown, a value-at-risk estimate, and gross/net exposure, computed from the equity curve and position history (Requirement 24).
- **Drawdown:** the decline of the equity curve from its running peak, with the maximum magnitude and its duration (Requirement 27).
- **Benchmark:** where configured, the benchmark's return series over the same period is computed from Market Data only, and excess return over the period is reported; where no benchmark is configured, the result is produced without a comparison rather than failing (Requirement 28).
- All metric computations are pure and deterministic: identical inputs yield identical values (Requirements 23.3, 24.3, 27.3, 28.3).

## Database Schema

Postgres via SQLAlchemy 2.0 (`DeclarativeBase` + `Mapped`/`mapped_column`), snake_case plural tables — identical style to `aqros_model_registry.adapters.orm`. Only run metadata and structured results are persisted here; large artifacts live behind the `ResultArtifactStore` (Section "Result Artifact Storage").

```mermaid
erDiagram
    BACKTEST_RUNS {
        bigint id PK
        uuid run_uuid UK
        string strategy_id
        string model_name
        integer model_version
        string status
        text config_json
        text manifest_json
        datetime created_at
        datetime completed_at
        text failure_reason
    }
    TRADE_LOG_ENTRIES {
        bigint id PK
        uuid run_uuid FK
        integer sequence
        string client_order_id
        string symbol
        string side
        string order_type
        numeric quantity
        numeric price
        numeric commission
        datetime clock_time
        string outcome
        text reason
    }
    EQUITY_POINTS {
        bigint id PK
        uuid run_uuid FK
        datetime clock_time
        numeric total_value
    }
    BACKTEST_RESULTS {
        bigint id PK
        uuid run_uuid FK UK
        text performance_json
        text risk_json
        text drawdown_json
        text benchmark_json
        text final_portfolio_json
        datetime written_at
    }
    BACKTEST_RUNS ||--o{ TRADE_LOG_ENTRIES : logs
    BACKTEST_RUNS ||--o{ EQUITY_POINTS : samples
    BACKTEST_RUNS ||--|| BACKTEST_RESULTS : produces
```

Key constraints and indexes:
- `UNIQUE (run_uuid)` on `backtest_runs` — the stable, immutable run identity referenced by every artifact (Requirements 30.1, 30.3).
- `UNIQUE (run_uuid)` on `backtest_results` — exactly one result per run, written once and never updated after `COMPLETED` (Requirements 38.2, 40.4).
- `trade_log_entries` and `equity_points` are append-only with no update or delete code path, ordered by `(run_uuid, sequence)` / `(run_uuid, clock_time, sequence)` using the same total tie-break rule as events (Requirements 25.2, 38.3).
- Indexes `ix_backtest_runs_run_uuid`, `ix_backtest_runs_status`, `ix_backtest_runs_strategy_id`, `ix_backtest_runs_model_name`, `ix_trade_log_entries_run_uuid`, `ix_equity_points_run_uuid`.
- The application refuses any write to a result already present and never issues UPDATE/DELETE against results, trade-log, or equity rows; immutability of `COMPLETED` runs is enforced at the persistence layer (Requirements 40.2, 40.4).
- Migration `0001_initial_schema.py` creates all tables, unique constraints, and indexes, with a symmetric `downgrade()`, in the style of the Model Registry's `0001_initial_schema.py`.

## Result Artifact Storage

`ResultArtifactStore` port (mirrors the Model Registry's `ArtifactStore` — bytes in/bytes out, no filesystem-specific parameter):

```python
class ResultArtifactStore(ABC):
    @abstractmethod
    async def write_artifact(self, run_uuid: UUID, name: str, data: bytes) -> str: ...
    @abstractmethod
    async def read_artifact(self, run_uuid: UUID, name: str) -> bytes: ...

class ArtifactAlreadyExistsError(RuntimeError):
    """Raised when (run_uuid, name) is already persisted — artifacts are never overwritten."""

class ArtifactIntegrityError(RuntimeError):
    """Raised when retrieved bytes do not match the recorded checksum."""
```

`LocalResultArtifactStore` (MVP adapter):
- Path `{base_dir}/{run_uuid}/{name}` — deterministically encodes the run UUID and artifact name, so artifacts from different runs are never conflated or overwritten (Requirements 39.4, 39.5).
- On write, computes and stores a checksum alongside the artifact; a second write to an existing `(run_uuid, name)` raises `ArtifactAlreadyExistsError` (Requirement 39.2).
- On read, recomputes the checksum and compares it to the recorded value, raising `ArtifactIntegrityError` and recording an integrity-failure reason on mismatch rather than serving corrupted bytes (Requirement 39.3).
- Persists reports, the trade-log export, the equity curve, the drawdown series, metric sets, and the run manifest as artifacts, each traceable to the `Run_Uuid` and manifest (Requirements 39.1, 39.4).
- Object-store swap (S3/MinIO/R2) is a drop-in adapter change in `api/deps.py`, zero change to domain or API (Requirements 38.5, 39.1). **Object storage is not a mandatory MVP dependency.**

## REST API Design

All endpoints are Pydantic-typed, documented via FastAPI OpenAPI (Requirement 36.7), return an `ErrorResponse(error, detail)` with `404` for any missing resource (Requirement 36.8), and require a client `Idempotency-Key` header on every mutating call so a retried submission never starts a duplicate run (Requirement 36.6).

| Method | Path | Purpose | Requirement |
|---|---|---|---|
| `POST` | `/v1/backtests` | Submit a `Backtest_Configuration`, validate it, assign a `Run_Uuid`, persist `PENDING`, and start background execution; returns the `Run_Uuid` | 29, 36.1, 36.6 |
| `GET` | `/v1/backtests/{run_uuid}` | Report the run's `Run_Status` (pending / running / completed / failed) and, when failed, its reason | 36.2, 37.2 |
| `GET` | `/v1/backtests/{run_uuid}/result` | Full `Backtest_Result`: trade log, equity curve, drawdown, performance + risk metrics, benchmark comparison, final portfolio | 36.3 |
| `GET` | `/v1/backtests` | List `Backtest_Runs`; optional `strategy_id` / `model_name` / `status` filters | 36.4 |
| `GET` | `/v1/backtests/{run_uuid}/manifest` | Return the immutable `Run_Manifest` of the run | 32.4, 36.5 |
| `GET` | `/health`, `/health/live`, `/health/ready` | Liveness/readiness (readiness verifies own DB + `ResultArtifactStore`) | 41.1 |

Request body for `POST /v1/backtests` (illustrative):

```json
{
  "strategy_id": "momentum_v1",
  "strategy_params": { "lookback": 20 },
  "model_name": "aapl_5d_direction__random_forest",
  "model_version": null,
  "universe": ["AAPL", "MSFT"],
  "exchange": "XNAS",
  "start": "2020-01-02T00:00:00-05:00",
  "end": "2021-12-31T00:00:00-05:00",
  "starting_cash": "100000.00",
  "bar_interval": "daily",
  "slippage_model": "fixed_bps",
  "slippage_params": { "bps": 5 },
  "commission_model": "per_share",
  "commission_params": { "per_share": "0.005" },
  "fill_model": "liquidity_capped",
  "fill_params": {},
  "latency_model": "zero",
  "latency_params": {},
  "leverage_enabled": false,
  "max_leverage": "1.0",
  "equity_sample_interval": "daily",
  "benchmark_symbol": "SPY",
  "seed": 12345
}
```

A run that references a model without a version resolves the production champion; `model_version` may pin an explicit version (Requirement 9). A submission that omits `seed` has one assigned and recorded so the run stays reproducible (Requirement 29.4).

## Error Handling

| Failure | Detected by | Outcome | HTTP surface |
|---|---|---|---|
| Invalid or incomplete configuration | `BacktestService` (validation) | run not started; typed error naming the offending field (Req 29.2) | `422` |
| Market Data / Model Registry / Feature Store unreachable or error on essential data | `Http*Client` | run marked `FAILED`; failure reason recorded; no partial result presented as complete (Req 2.4, 10.4, 37.1, 37.2) | `202` accepted then `FAILED` status; `502` on synchronous resolution |
| No production model and none pinned; pinned version missing | `ModelRegistryClient` / `BacktestService` | run `FAILED`; "no approved model available" recorded (Req 9.6) | `FAILED` status |
| Look-ahead guard trips (knowledge_time > clock) | `look_ahead_guard` | run `FAILED` with a diagnostic naming the offending access (Req 5.4, 37.3) | `FAILED` status |
| Order rejected by shared risk / Risk_Kernel | `RiskCheck` | rejection recorded in the Trade_Log; run continues (Req 34.5) | n/a (in-result) |
| Stored artifact bytes ≠ recorded checksum on retrieval | `ResultArtifactStore` | refuse to serve; integrity-failure reason recorded (Req 39.3) | `500` typed |
| Second write to an existing `(run_uuid, name)` | `ResultArtifactStore` | rejected; original bytes unchanged (Req 39.2, 40) | `409`/internal |
| Attempt to mutate a `COMPLETED` run's results | repository | refused; no update/delete path (Req 40.2, 40.4) | `409`/internal |
| Unknown run / result / manifest | query routes | typed `404` naming the missing resource (Req 36.8) | `404` |
| Run interrupted before `COMPLETED` | `BacktestService` | marked `FAILED`; restart only via a new run (Req 33.1, 33.2) | `FAILED` status |

Every failed or rejected run records a human-readable reason (Requirement 37.5); a run that fails partway leaves no persisted result claiming completeness (Requirements 37.4, 40).

## Reproducibility, Determinism, and Recovery

- **Byte-identical replay (Requirement 25):** two runs sharing the same `Run_Manifest` — same model version(s), historical data selection, configuration, parameters, code commit, and seed — produce byte-for-byte identical trade logs, fills, positions, cash, portfolio snapshots, equity curves, drawdown, and metrics, because "now", randomness, ordering, and arithmetic are all deterministic (Sections 8–9).
- **Manifest reproduction (Requirement 32):** the manifest pins everything needed to reproduce a result without recourse to mutable external state; re-executing from a persisted manifest against the same upstream data versions reproduces the original result identically; the manifest is retrievable via the API.
- **Recovery (Requirement 33):** a run interrupted before `COMPLETED` is marked `FAILED` and never exposes a partial result as complete. A failed or interrupted run is restarted only by creating a **new** run with a new `Run_Uuid` — the engine never resumes a partially-completed run in place unless explicit checkpointing is added later, in which case resuming from a checkpoint must yield results identical to an uninterrupted run of the same manifest.
- **Batch/parameter-sweep extensibility (Requirement 35):** runs are independent and deterministic, so future orchestration of many configurations as independent runs (each with its own `Run_Uuid`, manifest, and result) adds no change to the simulation core, the determinism guarantees, or the single-run result contract.

## Docker Deployment

- Image: shared `docker/Dockerfile.service` with `SERVICE=backtesting-engine`, `MODULE=aqros_backtesting_engine`, `PORT=8010` — no Dockerfile change (Requirement 42.1). This is a NEW service slot (no reserved entry exists yet).
- Ports continue the sequence: **service 8010**, **dedicated Postgres 5437** (`backtesting-engine-db`), following 5432/5433/5434/5435/5436 (Requirements 42.2, 42.3).
- New `docker-compose.yml` entries (implementation-phase edit, shown here for the design record):

```yaml
  backtesting-engine-db:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: aqros
      POSTGRES_PASSWORD: aqros
      POSTGRES_DB: aqros_backtesting_engine
    ports: ["5437:5432"]
    volumes:
      - backtesting-engine-db-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U aqros -d aqros_backtesting_engine"]
      interval: 5s
      timeout: 5s
      retries: 10
    networks: [aqros]

  backtesting-engine:
    <<: *service-defaults
    build:
      context: .
      dockerfile: docker/Dockerfile.service
      args: { SERVICE: backtesting-engine, MODULE: aqros_backtesting_engine, PORT: "8010" }
    ports: ["8010:8010"]
    environment:
      AQROS_ENVIRONMENT: dev
      AQROS_LOG_JSON: "false"
      AQROS_DATABASE_URL: postgresql+asyncpg://aqros:aqros@backtesting-engine-db:5432/aqros_backtesting_engine
      # Reaches Market Data, Model Registry, and Feature Store only through their published REST APIs.
      AQROS_MARKET_DATA_BASE_URL: http://market-data:8002
      AQROS_MODEL_REGISTRY_BASE_URL: http://model-registry:8004
      AQROS_FEATURE_STORE_BASE_URL: http://feature-store:8003
      AQROS_RESULT_ARTIFACT_DIR: /data/backtesting-engine/artifacts
    volumes:
      - backtesting-engine-artifacts:/data/backtesting-engine/artifacts
    depends_on:
      backtesting-engine-db:
        condition: service_healthy
      market-data:
        condition: service_started
      model-registry:
        condition: service_started
      feature-store:
        condition: service_started
```

  plus `backtesting-engine-db-data` and `backtesting-engine-artifacts` named volumes. Result-artifact persistence is a **local volume behind the `ResultArtifactStore` interface** (no object-store service required in the MVP; swappable later — Requirements 38.5, 39.1).

- `Settings` (`config.py`) extends `aqros_core.config.BaseServiceSettings`: `service_name="backtesting-engine"`, `port=8010`, `database_url` defaulting to `postgresql+asyncpg://aqros:aqros@localhost:5437/aqros_backtesting_engine`, `market_data_base_url` / `model_registry_base_url` / `feature_store_base_url` (`AnyHttpUrl`), an optional `risk_kernel_base_url`, `result_artifact_dir`, and `upstream_request_timeout_seconds` — all overridable via `AQROS_*` (Requirement 42.5).
- Health: `/health/live` (always healthy), `/health/ready` (checks `database` via `db.ping` and the `ResultArtifactStore`), `/health` alias — identical pattern to the existing services (Requirement 41.1).
- Root `pyproject.toml` `[tool.ruff.lint.isort].known-first-party` gains `"aqros_backtesting_engine"` and `"aqros_strategy_core"` (implementation-phase edit).

## Security

- **Risk-kernel sovereignty:** every order routes through the shared `RiskCheck` path and, where configured, the Risk Kernel; there is no bypass/disable/relax path and the engine never modifies a kernel limit (Requirement 34; CLAUDE.md §7.3).
- **Read-only upstreams:** all historical market data, models, and features are treated as read-only inputs; the engine never writes to or mutates any upstream service's data (Requirements 2.2, 45.3; CLAUDE.md §7.9).
- **Artifact integrity:** result artifacts are checksum-verified on write and re-verified on read; corrupted bytes are refused (Requirement 39).
- **Secrets:** any credential is fetched at runtime from the platform secrets mechanism and never read from source code, an image layer, or a version-controlled file (CLAUDE.md §7.5).
- **Observability:** structured logs carry a correlation identifier for every request and every run (Requirement 41.2); no secrets appear in logs.

## Correctness Properties

### Property 1: Ingestion is REST-only and the Training Pipeline is never queried
For any run, historical market data, models, and features are obtained solely through the Market Data, Model Registry, and Feature Store REST APIs, and no code path opens a service database or contacts the Training Pipeline.
**Validates: Requirements 1.4, 1.5, 2.1, 9.5, 45.1**

### Property 2: Historical market data is never modified
For any run, the engine issues no write/update/delete against market data or any upstream store and treats all retrieved data as read-only.
**Validates: Requirements 2.2, 2.3, 45.3**

### Property 3: Deterministic byte-identical replay
For any two runs with the same Run_Manifest, the full Backtest_Result — trade log, fills, positions, cash, portfolio snapshots, equity curve, drawdown, performance and risk metrics — is byte-for-byte identical.
**Validates: Requirements 25.1, 25.2, 25.3, 31**

### Property 4: Events are processed in one total, documented order
For any Event_Stream, events are processed in non-decreasing event time with a fixed total tie-break `(event_time, kind_priority, sequence)`, identical on every run, with each event's effects applied fully before the clock advances.
**Validates: Requirements 6.1, 6.2, 6.3, 6.4**

### Property 5: No look-ahead bias; the guard fails the run on any violation
For any decision at clock time `t`, no market data, feature, or model output whose knowledge time exceeds `t` is used; any component read past the clock trips the look-ahead guard and fails the run with a diagnostic.
**Validates: Requirements 4.1, 4.4, 5.1, 5.4, 37.3**

### Property 6: Bar knowledge-time convention
For any bar dated `D`, it becomes visible to the strategy only at or after `D`'s session close, and an order decided at a bar close never fills at a price knowable only before it could have transacted.
**Validates: Requirements 3.5, 5.2, 5.3**

### Property 7: Models are resolved only via the Model Registry (production default or explicit pin)
For any run, the model is resolved via the Registry — the production champion when unpinned, the exact version when pinned — recorded with identity, checksum, and lineage; a run with no approved model and no pin fails.
**Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.6, 9.7, 45.4**

### Property 8: Feature values are point-in-time via `as_of`
For any feature request, the engine passes `as_of = current clock` and uses only values with knowledge time at or before the clock.
**Validates: Requirements 4.3, 10.1, 10.2**

### Property 9: The simulation core is asset-class agnostic
For any run, the simulation loop, event ordering, determinism, and point-in-time logic contain no equity-specific assumption that would block a future asset-class implementation behind the abstraction.
**Validates: Requirements 11.1, 11.3, 11.4**

### Property 10: The Run_Manifest is complete and reproducible
For any run, the manifest pins model version(s) + checksums, feature versions, the market-data selection with its knowledge-time boundary, code commit, calendar source, corporate actions applied, library versions, and seed — sufficient to reproduce the result without mutable external state — and is immutable once the run begins.
**Validates: Requirements 12.1, 12.2, 12.3, 12.4, 32.1, 32.2, 32.3**

### Property 11: The simulation drives the shared core, never a fork
For any run, strategy, risk, sizing, and order-management decisions come from `libs/aqros_strategy_core`; the engine reimplements none of them and differs from paper/live only in data source and fill mechanism.
**Validates: Requirements 1.2, 1.3, 13.1, 45.8**

### Property 12: Orders execute only through the Fill_Model with stable identifiers
For any run, every simulated order is executed by the configured Fill_Model (never a real/paper venue) and carries a deterministic client order id such that re-processing the same event never produces a duplicate order.
**Validates: Requirements 14.1, 14.2, 45.6**

### Property 13: Latency participates in replay without introducing look-ahead
For any order, the earliest fill-eligible time is emission plus the modeled latency, the fill evaluates only data at or after it, any stochastic latency comes from the seeded RNG, and identical inputs and seed give identical timing.
**Validates: Requirements 15.1, 15.3, 15.4**

### Property 14: Slippage and commission are deterministic and reflected net
For any fill, slippage and commission are computed deterministically from inputs, parameters, and seed; commission is debited from cash and included in the trade log and all net performance metrics.
**Validates: Requirements 16.1, 16.3, 16.4, 17.1, 17.3, 17.4**

### Property 15: Fills are deterministic and partial fills are capped at point-in-time liquidity
For any order, the Fill_Model fills a limit order only when point-in-time data satisfies its limit price and caps the filled quantity at the point-in-time liquidity, recording an unfilled/partially-filled outcome rather than assuming a complete fill.
**Validates: Requirements 14.4, 14.5, 18.1, 18.3, 18.4, 18.5**

### Property 16: Positions reconstruct exactly from the ordered fills
For any run, position quantity, average cost, and realized/unrealized P&L derive solely from the ordered fill sequence, so replaying the same fills reconstructs identical positions.
**Validates: Requirements 19.1, 19.2, 19.3, 19.4**

### Property 17: Portfolio value is the point-in-time sum of cash and positions
For any valuation, total portfolio value equals the cash balance plus the point-in-time market value of all positions, updated after every fill and cash movement before the clock advances.
**Validates: Requirements 20.1, 20.2, 20.3, 20.4**

### Property 18: The cash ledger stays consistent with the trade log
For any run, ending cash equals starting cash adjusted by every recorded fill notional and commission, computed with exact Decimal arithmetic.
**Validates: Requirements 17.1, 21.1, 21.2, 21.3, 21.5**

### Property 19: Margin, leverage, and forced liquidation are deterministic
For any run, buying power is derived from cash, positions, and leverage parameters; with leverage disabled a breaching fill is blocked and recorded; with leverage enabled a maintenance-margin breach triggers a deterministic forced liquidation recorded in the trade log.
**Validates: Requirements 21.4, 22.1, 22.2, 22.3, 22.4, 22.5**

### Property 20: Corporate actions are point-in-time correct and sourced only from Market Data
For any corporate action, it is obtained only from Market Data, applied only when its knowledge time is at or before the clock, and never synthesized or applied before it was knowable; splits/dividends/symbol-changes/mergers/delistings adjust positions and cash consistently.
**Validates: Requirements 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8**

### Property 21: The trading calendar is deterministic and timestamps are exchange-aware
For any run, sessions (excluding weekends/holidays, applying half-days) are derived deterministically for the exchange and period, all timestamps are timezone-aware in the exchange time zone, and DST transitions resolve deterministically.
**Validates: Requirements 7.1, 7.2, 7.3, 7.4, 7.5, 7.6**

### Property 22: Performance, risk, and drawdown metrics are deterministic
For any run, performance, risk, and drawdown are computed deterministically from the equity curve and trade log net of costs, with undefined metrics reported explicitly as undefined.
**Validates: Requirements 23.1, 23.3, 23.4, 24.1, 24.3, 27.1, 27.2, 27.3**

### Property 23: Benchmark comparison is deterministic or absent
For any run with a benchmark, the benchmark series and excess return are computed deterministically from Market Data only; with no benchmark configured, the result is produced without a comparison rather than failing.
**Validates: Requirements 28.1, 28.2, 28.3, 28.4**

### Property 24: Every run has a stable, immutable UUID referenced by all artifacts
For any run, a globally unique immutable Run_Uuid is assigned at creation, never changed or reused, and referenced by the manifest, result, trade log, equity curve, and every persisted artifact.
**Validates: Requirements 30.1, 30.2, 30.3, 30.4**

### Property 25: Interrupted runs fail cleanly and re-runs reproduce identically
For any interrupted run, it is marked FAILED with no partial-as-complete result and is restartable only as a new run; re-executing the same manifest against the same upstream versions reproduces the original result identically.
**Validates: Requirements 33.1, 33.2, 33.3, 33.4**

### Property 26: The Risk Kernel is never bypassed
For any order, it is routed through the shared risk check (and the Risk Kernel where configured) before any fill; there is no path that disables or relaxes the check, no kernel limit is modified, and rejections are recorded while the run continues.
**Validates: Requirements 34.1, 34.2, 34.3, 34.4, 34.5, 45.5**

### Property 27: Result artifacts are checksum-verified and version-traceable
For any persisted artifact, a checksum is stored on write and re-verified on read (refusing corrupted bytes), each artifact is traceable to its Run_Uuid and manifest, and artifacts from different runs are never conflated or overwritten.
**Validates: Requirements 39.1, 39.2, 39.3, 39.4, 39.5**

### Property 28: COMPLETED results are immutable
For any run in COMPLETED status, its result, metrics, reports, trade log, equity curve, drawdown, and manifest are immutable with no update or delete path; a required change demands a new run.
**Validates: Requirements 38.2, 38.3, 40.1, 40.2, 40.3, 40.4**

### Property 29: Invalid configurations are rejected before a run begins
For any invalid or incomplete Backtest_Configuration, submission is rejected with a typed error identifying the offending field and no run is started.
**Validates: Requirements 29.1, 29.2, 29.3, 29.4**

### Property 30: Any nonexistent resource yields a typed 404
For any identifier that does not correspond to an existing Backtest_Run, Backtest_Result, or Run_Manifest, the API responds with 404 and a typed error body naming the missing resource.
**Validates: Requirements 36.8**

## Testing Strategy

### Dual testing approach
- **Unit tests** (`tests/unit/`) exercise every domain module (`calendar.py`, `events.py`/`replay.py`, `lookahead.py`, `latency.py`, `slippage.py`, `commission.py`, `fills.py`, `corporate_actions.py`, `portfolio.py`, `metrics.py`, `simulation.py`, `services.py`) against fakes for every port (`FakeMarketDataClient`, `FakeModelRegistryClient`, `FakeFeatureStoreClient`, `FakeResultArtifactStore`, `FakeBacktestRunRepository`, `FakeClock`, `FakeCalendarProvider`) — no real HTTP, filesystem, or database access (Requirement 43.1).
- **Property-based tests** using `hypothesis` (minimum `@settings(max_examples=100)`), one test per correctness property above, tagged `# Feature: backtesting-engine, Property N: <text>` directly above each test, placed in the unit file for the module the property most exercises (e.g. Property 4 in `test_replay_ordering.py`, Property 5/6 in `test_lookahead.py`, Property 3/16/18 in `test_determinism.py`). Covers the invariants required by Requirement 43.2: deterministic replay, event-ordering totality, cash-ledger consistency, position reconstruction from fills, and the absence of look-ahead bias.
- **Golden-replay test** (`tests/unit/test_determinism.py`): a fixed `Backtest_Configuration` over fixed historical data and a fixed model asserts the `Backtest_Result` matches a stored golden result exactly (Requirement 43.3).
- **Integration tests** (`tests/integration/`) run the full FastAPI app via `httpx.AsyncClient` + `ASGITransport` against a real Postgres from `testcontainers.postgres.PostgresContainer` and a real `LocalResultArtifactStore`, with all three upstream clients swapped for in-memory fakes (no live Market Data, Model Registry, or Feature Store required) — Requirement 43.4. Mirrors the Model Registry's `conftest.py`/`test_api.py` fixtures (`postgres_container`, `engine`, `session_factory`, `db_session`, `client` overriding `get_session`/`get_market_data_client`/`get_model_registry_client`/`get_feature_store_client`/`get_result_artifact_store`).
- `tests/integration/test_migrations.py` runs the Alembic `0001` upgrade/downgrade against the testcontainers Postgres.
- `tests/test_health.py` covers readiness composition (all healthy → 200; one failing → 503) — Requirement 41.1.

### Quality gates
`ruff check`, `black --check`, `mypy --strict`, and `pytest` (non-zero exit on any failure or setup error) run against `backend/backtesting-engine` in the same CI pattern as the five existing services (Requirements 43.5, 44).
