# Requirements Document

## Introduction

The Backtesting Engine is a new AQROS backend microservice (`backend/backtesting-engine`, module `aqros_backtesting_engine`) that constitutes the **simulation plane** of the platform: it replays historical market data through the platform's shared strategy, risk, and order-management core to measure how a strategy driven by an approved production model would have behaved, without ever touching real capital. It is the second rung of the trust ladder (research → **backtest** → paper → supervised live → bounded autonomous) and the primary defense against overfitting and data leakage before any strategy is allowed near money (CLAUDE.md §1, §6, §10).

The Backtesting Engine consumes historical market data **exclusively** through the Market Data Service's published REST API, resolves the models it evaluates **exclusively** through the Model Registry's published REST API (production/approved models only), and — when a strategy requires engineered features — resolves feature definitions **exclusively** through the Feature Store's published REST API. It never queries the Training Pipeline, never modifies historical market data, never retrains or fine-tunes a model, never bypasses the Model Registry to obtain a model, and never bypasses the Risk Kernel once that integration exists (CLAUDE.md Hard Rules §7.2, §7.3, §7.4, §7.9).

A cardinal, non-negotiable property of the Backtesting Engine is **determinism**: running the same model version(s), the same historical market data, the same configuration, and the same parameters MUST always produce byte-for-byte identical results. Every simulated event, order, fill, position, cash movement, portfolio snapshot, and computed metric MUST be reproducible from an immutable run manifest (CLAUDE.md §5 "no wall-clock time in domain logic", §6.5 "everything is versioned and reproducible"). The engine also enforces **point-in-time correctness** structurally: no decision at simulation time `t` may use any information whose knowledge time is after `t` — look-ahead bias is forbidden by construction, not merely discouraged (CLAUDE.md Hard Rule §7.2).

To honor the "one codebase for backtest, paper, and live" rule (CLAUDE.md Hard Rule §7.1), the Backtesting Engine does not fork strategy, risk, sizing, or order logic. That logic lives once in the shared `libs/` core and is invoked by the engine; only the **data source** (historical replay instead of a live feed) and the **fill simulator** (a cost/slippage/fill model instead of a real venue) differ from paper and live execution.

This document covers the MVP-through-V1 scope of the Backtesting Engine: service purpose and boundaries, historical replay, point-in-time correctness and look-ahead prevention, event ordering, exchange trading calendars and time zones, corporate actions, model resolution from the Model Registry, feature resolution from the Feature Store, multi-asset extensibility, dataset/version reproducibility, the simulation engine, order-execution simulation, execution latency, slippage/commission/fill models, position/portfolio/cash tracking, margin and leverage, performance and risk metrics, the trade log, equity curve, drawdown, benchmark comparison, strategy configuration, stable run identity, deterministic execution, reproducibility, recovery and replay, the Risk Kernel integration boundary, batch/parameter-sweep extensibility, the REST API, repository/persistence, artifact versioning and integrity, result immutability, Docker deployment, and the testing and quality gates shared by every existing AQROS backend service.

For the MVP, equities are the only required asset class, leverage is disabled by default, and execution latency defaults to zero; multi-asset support, margin and leverage, execution-latency models, and batch/parameter-sweep orchestration are specified as extensibility requirements that the architecture must support without changing the simulation core.

## Glossary

- **Backtesting_Engine**: The `backtesting-engine` backend microservice as a whole, exposing a REST API and owning the execution and results of historical simulations. Also referred to as "the engine."
- **Backtest_Run**: A single, uniquely-identified execution of a simulation over a defined historical period, with a fixed configuration, parameter set, model resolution, and data selection.
- **Backtest_Configuration**: The complete, typed specification of a Backtest_Run — the strategy identity and parameters, the instrument universe, the start and end of the historical period, the starting cash, the slippage/commission/fill model selections and their parameters, the benchmark selection, and the random seed.
- **Historical_Replay**: The process of reading historical market data for the configured universe and period from the Market Data Service and presenting it to the Simulation_Engine as a time-ordered sequence of Events.
- **Market_Data_Client**: The adapter component within the Backtesting_Engine that communicates exclusively with the Market Data Service's published REST API to read historical market data.
- **Model_Registry_Client**: The adapter component within the Backtesting_Engine that communicates exclusively with the Model Registry's published REST API to resolve and download approved production models and their metadata, lineage, and artifacts.
- **Feature_Store_Client**: The adapter component within the Backtesting_Engine that communicates exclusively with the Feature Store's published REST API to resolve feature definitions and point-in-time feature values when a strategy requires them.
- **Production_Model**: A Model_Version that the Model Registry reports as being in the `PRODUCTION` Lifecycle_State (the approved champion), the only model class the engine is permitted to evaluate unless a specific, explicitly-pinned Model_Version is configured.
- **Model_Version**: An immutable, versioned trained model as recorded and served by the Model Registry, identified by its model name and version.
- **Simulation_Clock**: The injected, monotonic logical clock that advances only as the engine processes Events; it is the sole source of "now" inside the simulation, and no domain component reads wall-clock time.
- **Knowledge_Time**: The time at which a fact could first have been known to the platform; a fact with knowledge time after the Simulation_Clock's current value is treated as not yet known.
- **Look_Ahead_Bias**: Any use, at Simulation_Clock time `t`, of information whose Knowledge_Time is after `t`; structurally forbidden.
- **Event**: A single time-stamped occurrence in the simulation — for example a market-data bar or tick becoming available, a scheduled decision point, or a simulated order lifecycle transition — carrying an event time and a knowledge time.
- **Event_Stream**: The fully-ordered sequence of Events consumed by the Simulation_Engine for a Backtest_Run.
- **Simulation_Engine**: The pure, deterministic core loop that advances the Simulation_Clock, dispatches Events to the Shared_Strategy_Core and risk/OMS logic, applies the Fill_Model, and updates Portfolio state.
- **Shared_Strategy_Core**: The strategy, risk, position-sizing, and order-management logic living in `libs/` that is shared unmodified across backtest, paper, and live; the Backtesting_Engine invokes it rather than reimplementing it.
- **Strategy**: A configured decision policy that, given point-in-time-correct market data, features, and a resolved model, emits target orders; supplied by the Shared_Strategy_Core.
- **Simulated_Order**: An order emitted by the Strategy/OMS logic during a Backtest_Run, carrying a client-generated identifier, that is executed by the Fill_Model rather than a real venue.
- **Fill**: The simulated execution (in whole or in part) of a Simulated_Order, producing an executed quantity and price at a specific Simulation_Clock time.
- **Fill_Model**: The pluggable component that decides whether, when, at what quantity, and at what price a Simulated_Order fills, given the point-in-time market data and the configured Slippage_Model and Commission_Model.
- **Slippage_Model**: The pluggable, deterministic model that adjusts a Fill's price away from the reference price to represent market impact and spread cost.
- **Commission_Model**: The pluggable, deterministic model that computes the transaction cost (fees) charged against cash for a Fill.
- **Position**: The engine's tracked holding in a single instrument — signed quantity, average cost basis, and derived realized and unrealized profit and loss.
- **Portfolio**: The complete tracked state of a Backtest_Run at a point in Simulation_Clock time — all Positions, the Cash_Ledger balance, and the total portfolio value (equity).
- **Cash_Ledger**: The engine's tracked cash balance, debited and credited by Fills, commissions, and other configured cash flows.
- **Trade_Log**: The append-only, ordered record of every Simulated_Order and Fill produced during a Backtest_Run, with quantities, prices, costs, timestamps, and identifiers.
- **Equity_Curve**: The ordered time series of total Portfolio value sampled at each configured interval over the Backtest_Run.
- **Drawdown**: The decline in Portfolio value from a running peak, expressed as a series and summarized by its maximum magnitude and duration.
- **Performance_Metrics**: The computed return-and-risk-adjusted summary of a Backtest_Run — for example total return, annualized return, Sharpe ratio, Sortino ratio, and win rate.
- **Risk_Metrics**: The computed risk summary of a Backtest_Run — for example volatility, maximum drawdown, value-at-risk, and exposure.
- **Benchmark**: A reference return series (for example a buy-and-hold of an index or instrument) against which a Backtest_Run's performance is compared.
- **Run_Manifest**: The immutable, content-addressable record that fully specifies a Backtest_Run — the code commit SHA, the Backtest_Configuration, the resolved Model_Version(s) and their checksums, the dataset/feature versions and Market Data selection with its knowledge-time boundary, the trading-calendar source, the corporate actions applied, the model/library versions, and the random seed — sufficient to reproduce the Backtest_Result bit-for-bit.
- **Backtest_Result**: The complete, immutable output of a Backtest_Run — the Trade_Log, Equity_Curve, Drawdown series, Performance_Metrics, Risk_Metrics, benchmark comparison, and final Portfolio state, keyed by the Run_Manifest.
- **Risk_Kernel**: The platform's sovereign, human-owned hard risk limits (a future integration); the Backtesting_Engine routes orders through the same shared risk logic and, once available, the Risk_Kernel, and never bypasses it.
- **Backtest_API**: The FastAPI REST interface exposed by the Backtesting_Engine for configuring, running, and retrieving Backtest_Runs and their results.
- **Downstream_Consumer**: Any human or service that reads Backtest_Results — including the research workflow, the validation gauntlet that gates model promotion, and the control surface.
- **Run_Uuid**: The globally unique, immutable UUID assigned to a Backtest_Run at creation and referenced by every result, report, and artifact of that run.
- **Run_Status**: The lifecycle status of a Backtest_Run — one of `PENDING`, `RUNNING`, `COMPLETED`, or `FAILED`.
- **Trading_Calendar**: The exchange-specific schedule of trading sessions — regular sessions, half-days, weekends, and holidays — used to determine when each instrument's market was open, expressed in the exchange's own time zone.
- **Corporate_Action**: An event that changes an instrument's shares, identity, or cash entitlement — a stock split, reverse split, cash or stock dividend, symbol change, merger, or delisting — as reported by the Market Data Service.
- **Asset_Class**: The category of a tradable instrument — equities for the MVP, with futures, options, forex, crypto, and others as future extensions — modeled behind an abstraction so the Simulation_Engine core is asset-class agnostic.
- **Latency_Model**: The pluggable, deterministic model that determines the delay between a Simulated_Order's emission and the earliest Simulation_Clock time at which the Fill_Model may execute it.
- **Margin**: The collateral framework governing leveraged Positions — including buying power, margin used, and the maintenance-margin requirement — that constrains how large the Portfolio's Positions may be.
- **Buying_Power**: The maximum notional the Portfolio may deploy at a point in Simulation_Clock time, derived from cash, Position values, and the configured leverage/margin parameters.
- **Maintenance_Margin**: The minimum Portfolio equity that must be maintained to hold leveraged Positions; a breach triggers Forced_Liquidation.
- **Forced_Liquidation**: The deterministic, simulated closing of Positions when the Portfolio's equity falls below the Maintenance_Margin requirement.
- **Result_Artifact**: A persisted output of a Backtest_Run — a report, Trade_Log export, Equity_Curve, Drawdown series, metric set, or Run_Manifest — stored behind the artifact storage interface, checksum-verified, and traceable to the Run_Uuid.

## Requirements

### Requirement 1: Service Purpose and Boundaries

**User Story:** As a platform architect, I want the Backtesting Engine to have one bounded responsibility — deterministically simulating a strategy over historical data using the shared execution core — so that it never duplicates upstream services' responsibilities or forks the money-path logic.

#### Acceptance Criteria

1. THE Backtesting_Engine SHALL simulate the behavior of a Strategy driven by a resolved Model_Version over a defined historical period and SHALL produce a Backtest_Result.
2. THE Backtesting_Engine SHALL invoke the Shared_Strategy_Core in `libs/` for all strategy, risk, position-sizing, and order-management logic, and SHALL NOT reimplement or fork that logic.
3. THE Backtesting_Engine SHALL differ from paper and live execution only in its data source (Historical_Replay) and its execution mechanism (the Fill_Model), and SHALL NOT alter the shared decision logic to obtain a result.
4. THE Backtesting_Engine SHALL consume historical market data only via the Market_Data_Client, resolve models only via the Model_Registry_Client, and resolve feature definitions only via the Feature_Store_Client.
5. THE Backtesting_Engine SHALL NOT establish a direct database connection to the Market Data Service's, Feature Store's, Model Registry's, Training Pipeline's, or Dataset Builder's databases.

### Requirement 2: Historical Market Data via the Market Data Service Only

**User Story:** As a platform architect, I want the engine to read historical market data solely from the Market Data Service and never mutate it, so that market history has exactly one owner and can never be corrupted by a simulation.

#### Acceptance Criteria

1. WHEN the Backtesting_Engine requires historical market data for a Backtest_Run, THE Market_Data_Client SHALL retrieve it only via HTTP requests to the Market Data Service's published REST API.
2. THE Backtesting_Engine SHALL NOT write, update, delete, or otherwise modify any historical market data in the Market Data Service or any other store.
3. THE Backtesting_Engine SHALL treat all retrieved historical market data as read-only inputs to the simulation.
4. IF the Market Data Service returns an error response or is unreachable while data required for a Backtest_Run is being retrieved, THEN THE Backtesting_Engine SHALL fail the Backtest_Run, record the failure reason, and SHALL NOT produce a partial Backtest_Result presented as complete.
5. THE Backtesting_Engine SHALL request historical market data scoped to the configured instrument universe and historical period of the Backtest_Run.

### Requirement 3: Historical Replay

**User Story:** As a quant researcher, I want the engine to replay historical market data as a time-ordered event stream over a chosen period and universe, so that a strategy experiences history as it actually unfolded.

#### Acceptance Criteria

1. WHEN a Backtest_Run begins, THE Historical_Replay SHALL produce an Event_Stream covering the configured start and end of the historical period for the configured instrument universe.
2. THE Historical_Replay SHALL present market-data Events to the Simulation_Engine in non-decreasing order of event time.
3. THE Historical_Replay SHALL advance the Simulation_Clock only to the event time of the Event currently being processed, and never ahead of it.
4. IF the configured historical period contains no market data for a configured instrument, THEN THE Backtesting_Engine SHALL record that the instrument had no data for the period and SHALL continue the Backtest_Run for the remaining instruments rather than failing silently.
5. THE Historical_Replay SHALL replay only data whose Knowledge_Time is at or before the Event's event time as presented by the Market Data Service, and SHALL NOT synthesize, interpolate, or infer market data that the Market Data Service did not provide.

### Requirement 4: Point-in-Time Correctness

**User Story:** As a risk-conscious platform owner, I want every simulated decision to see only what was knowable at that moment, so that backtest results reflect reality and not hindsight.

#### Acceptance Criteria

1. WHEN the Simulation_Engine dispatches an Event at Simulation_Clock time `t`, THE Backtesting_Engine SHALL make available to the Strategy only market data, features, and model outputs whose Knowledge_Time is at or before `t`.
2. THE Backtesting_Engine SHALL derive every timestamp used in a decision from the Simulation_Clock and SHALL NOT read wall-clock time within the Simulation_Engine or the Shared_Strategy_Core during a Backtest_Run.
3. WHEN the Backtesting_Engine requests feature values from the Feature_Store_Client, THE Backtesting_Engine SHALL request them as of the current Simulation_Clock time so that only point-in-time-correct values are returned.
4. IF a data source would return a value whose Knowledge_Time is after the current Simulation_Clock time, THEN THE Backtesting_Engine SHALL treat that value as not yet known and SHALL exclude it from the decision.

### Requirement 5: No Look-Ahead Bias

**User Story:** As a quant researcher, I want look-ahead bias to be structurally impossible, so that I can trust that an impressive backtest is not the result of leaked future information.

#### Acceptance Criteria

1. THE Backtesting_Engine SHALL NOT allow a Strategy's decision at Simulation_Clock time `t` to depend on any Event, market-data value, feature value, or label whose Knowledge_Time is after `t`.
2. WHEN a Simulated_Order is emitted in response to an Event at time `t`, THE Fill_Model SHALL fill it only against market data whose event time and Knowledge_Time are at or after `t`, and SHALL NOT fill it at a price drawn from data available only before `t` in a way that presumes foresight.
3. THE Backtesting_Engine SHALL NOT use the closing or future value of a bar to make or fill a decision that is timestamped at or before that bar's own event time when doing so would reveal information not yet knowable.
4. THE Backtesting_Engine SHALL provide an internal guard that rejects any attempt by a component to read data with a Knowledge_Time after the current Simulation_Clock time, and SHALL fail the Backtest_Run with a diagnostic identifying the offending access rather than silently proceeding.

### Requirement 6: Event Ordering

**User Story:** As a platform architect, I want a single, deterministic ordering of all simulation events, so that identical inputs always process in identical order.

#### Acceptance Criteria

1. THE Simulation_Engine SHALL process all Events in non-decreasing order of event time.
2. WHEN two or more Events share the same event time, THE Simulation_Engine SHALL order them by a fixed, documented, total tie-breaking rule so that their relative order is identical on every run.
3. THE Simulation_Engine SHALL apply the effects of an Event — decisions, orders, fills, and portfolio updates — fully before advancing the Simulation_Clock to the next Event.
4. THE Simulation_Engine SHALL NOT reorder, drop, or coalesce Events in a way that depends on wall-clock time, iteration order of an unordered collection, or any non-deterministic source.

### Requirement 7: Trading Calendar and Time Zones

**User Story:** As a quant researcher, I want the engine to use exchange trading calendars and exchange-aware timestamps, so that replay and decisions align with when each market was actually open, deterministically across holidays and daylight-saving transitions.

#### Acceptance Criteria

1. THE Backtesting_Engine SHALL determine trading sessions for each instrument using the Trading_Calendar of the exchange on which that instrument trades.
2. THE Backtesting_Engine SHALL exclude weekends, exchange holidays, and non-session periods from the trading sessions it simulates, and SHALL apply shortened sessions on exchange half-days.
3. THE Backtesting_Engine SHALL represent every Simulation_Clock time and Event time as an exchange-aware timestamp carrying an explicit time zone, and SHALL NOT use a naive or ambiguous local timestamp in the simulation.
4. WHEN a daylight-saving transition occurs within a Backtest_Run's period, THE Backtesting_Engine SHALL resolve session open and close times deterministically according to the exchange's Trading_Calendar and time-zone rules.
5. THE Backtesting_Engine SHALL derive the Trading_Calendar deterministically for a given exchange and period so that identical inputs yield identical session boundaries on every run.
6. THE Backtesting_Engine SHALL record the Trading_Calendar source or version used by a Backtest_Run in the Run_Manifest.

### Requirement 8: Corporate Actions

**User Story:** As a quant researcher, I want the engine to handle corporate actions correctly and point-in-time, so that prices, positions, and cash stay accurate across splits, dividends, symbol changes, mergers, and delistings.

#### Acceptance Criteria

1. THE Backtesting_Engine SHALL correctly handle stock splits, reverse splits, cash and stock dividends, symbol changes, mergers, and delistings that occur within a Backtest_Run's period, as reported by the Market Data Service.
2. WHEN a split or reverse split takes effect at Simulation_Clock time `t`, THE Backtesting_Engine SHALL adjust the affected Position's quantity and cost basis and the subsequent price references consistently, preserving the Portfolio's total value across the adjustment except for any rounding that is recorded explicitly.
3. WHEN a dividend is reached at Simulation_Clock time `t`, THE Backtesting_Engine SHALL credit the Cash_Ledger for a cash dividend or adjust the Position for a stock dividend, according to the Corporate_Action as reported by the Market Data Service.
4. WHEN a symbol change or merger takes effect, THE Backtesting_Engine SHALL map the affected Position to its successor instrument deterministically as reported by the Market Data Service.
5. WHEN an instrument is delisted, THE Backtesting_Engine SHALL resolve the affected Position according to the delisting terms reported by the Market Data Service and SHALL record the resolution in the Trade_Log.
6. THE Backtesting_Engine SHALL apply every Corporate_Action point-in-time correctly, using only Corporate_Action information whose Knowledge_Time is at or before the current Simulation_Clock time, and SHALL NOT apply a Corporate_Action before it was knowable.
7. THE Backtesting_Engine SHALL obtain all Corporate_Action data only from the Market Data Service and SHALL NOT modify it.
8. THE Backtesting_Engine SHALL record the Corporate_Actions applied during a Backtest_Run in the Run_Manifest for reproducibility.

### Requirement 9: Model Resolution from the Model Registry Only

**User Story:** As a risk-conscious platform owner, I want the engine to obtain the models it evaluates only from the Model Registry — approved production models by default — so that backtests always reflect governed models and never bypass promotion.

#### Acceptance Criteria

1. WHEN a Backtest_Run requires a model, THE Model_Registry_Client SHALL resolve it only via HTTP requests to the Model Registry's published REST API.
2. WHEN a Backtest_Configuration references a model by model name without an explicit version, THE Backtesting_Engine SHALL resolve the current Production_Model for that model name via the Model Registry's production-resolution endpoint.
3. WHERE a Backtest_Configuration pins an explicit Model_Version, THE Backtesting_Engine SHALL resolve exactly that Model_Version from the Model Registry and SHALL record which version was used.
4. THE Backtesting_Engine SHALL obtain the model artifact it evaluates only from the Model Registry, and SHALL NOT obtain a model artifact from the Training Pipeline, from any filesystem path outside the Model Registry client, or by retraining.
5. THE Backtesting_Engine SHALL NOT query the Training Pipeline for any purpose.
6. IF the Model Registry reports that no Production_Model exists for a referenced model name and no explicit version is pinned, THEN THE Backtesting_Engine SHALL fail the Backtest_Run and record that no approved model was available.
7. WHEN the Backtesting_Engine resolves a Model_Version, THE Backtesting_Engine SHALL record that version's identity, checksum, and lineage in the Run_Manifest.

### Requirement 10: Feature Definitions via the Feature Store When Required

**User Story:** As a quant researcher, I want strategies that need engineered features to obtain them only from the Feature Store, point-in-time correct, so that backtest features match exactly what training and live would use.

#### Acceptance Criteria

1. WHERE a Strategy requires engineered features, THE Feature_Store_Client SHALL resolve their definitions and values only via HTTP requests to the Feature Store's published REST API.
2. WHEN the Backtesting_Engine requests feature values, THE Feature_Store_Client SHALL request them as of the current Simulation_Clock time so that only point-in-time-correct values are used.
3. THE Backtesting_Engine SHALL record the feature versions used by a Backtest_Run in the Run_Manifest.
4. IF the Feature Store returns an error or is unreachable while features required by a Backtest_Run are being resolved, THEN THE Backtesting_Engine SHALL fail the Backtest_Run and record the failure reason.
5. WHERE a Strategy requires no engineered features, THE Backtesting_Engine SHALL run without contacting the Feature Store.

### Requirement 11: Multi-Asset Extensibility

**User Story:** As a platform architect, I want the simulation core to be asset-class agnostic, so that futures, options, forex, crypto, and other asset classes can be added later without changing the core.

#### Acceptance Criteria

1. THE Backtesting_Engine SHALL model instruments, Positions, Fills, and cash effects behind an Asset_Class abstraction so that additional asset classes can be introduced without changing the Simulation_Engine core loop.
2. THE Backtesting_Engine SHALL implement equities as the only required Asset_Class for the MVP.
3. WHERE a future Asset_Class such as futures, options, forex, or crypto is added, THE Backtesting_Engine SHALL support it by adding an Asset_Class implementation behind the existing abstraction rather than modifying the Simulation_Engine, event-ordering, determinism, or point-in-time-correctness logic.
4. THE Backtesting_Engine SHALL NOT hard-code equity-specific assumptions into the Simulation_Engine core in a way that would prevent a future Asset_Class implementation.

### Requirement 12: Dataset and Version Reproducibility

**User Story:** As a platform architect, I want every backtest pinned to exact versions of model, data, features, and code, so that any result can be reconstructed exactly.

#### Acceptance Criteria

1. WHEN a Backtest_Run executes, THE Backtesting_Engine SHALL pin and record the resolved Model_Version(s) and their checksums, the feature versions used, the Market Data selection with its instrument universe, period, and knowledge-time boundary, the code commit SHA of the engine and the Shared_Strategy_Core, the versions of the model/library dependencies used to evaluate the model, and the random seed.
2. THE Backtesting_Engine SHALL assemble the pinned information into an immutable Run_Manifest for the Backtest_Run.
3. FOR ALL Backtest_Runs, THE Backtesting_Engine SHALL keep the Run_Manifest immutable after the run begins.
4. THE Backtesting_Engine SHALL retain sufficient information in the Run_Manifest to reproduce the Backtest_Result without recourse to any mutable external state.

### Requirement 13: Simulation Engine and the Shared Strategy/Risk/OMS Core

**User Story:** As a platform architect, I want the simulation engine to drive the shared strategy, risk, and order-management core rather than its own copy, so that a backtested strategy is the exact same code that will run in paper and live.

#### Acceptance Criteria

1. THE Simulation_Engine SHALL advance the Simulation_Clock event by event, dispatch each Event to the Shared_Strategy_Core, route emitted orders through the shared risk and order-management logic, apply the Fill_Model, and update the Portfolio, in that fixed order per Event.
2. THE Simulation_Engine SHALL be a pure domain component that performs no direct I/O and reads no wall-clock time, receiving all external data through injected adapters and the injected Simulation_Clock.
3. THE Simulation_Engine SHALL route every Simulated_Order through the same shared risk-check path used by paper and live execution before the order can be filled.
4. WHEN the shared risk logic rejects a Simulated_Order, THE Simulation_Engine SHALL NOT fill that order and SHALL record the rejection with its reason in the Trade_Log.
5. THE Simulation_Engine SHALL produce identical intermediate state — orders, fills, positions, cash, and portfolio snapshots — for identical inputs on every run.

### Requirement 14: Order Execution Simulation

**User Story:** As a quant researcher, I want orders simulated realistically through a fill model, so that backtest fills approximate what a real venue would have done without ever contacting one.

#### Acceptance Criteria

1. WHEN the Shared_Strategy_Core and OMS logic emit a Simulated_Order during a Backtest_Run, THE Backtesting_Engine SHALL execute it through the configured Fill_Model rather than any live venue or broker.
2. THE Backtesting_Engine SHALL assign every Simulated_Order a client-generated identifier and SHALL ensure that re-processing the same Event never produces a duplicate Simulated_Order for that identifier.
3. WHEN a Simulated_Order is filled in whole or in part, THE Backtesting_Engine SHALL record the executed quantity, execution price, commission, and Simulation_Clock time of the Fill.
4. THE Backtesting_Engine SHALL support at least market and limit order types, filling a limit order only when the point-in-time market data satisfies its limit price.
5. WHERE a Simulated_Order cannot be fully filled given the point-in-time market data and the Fill_Model, THE Backtesting_Engine SHALL record the unfilled or partially-filled outcome rather than assuming a complete fill.

### Requirement 15: Execution Latency

**User Story:** As a quant researcher, I want configurable, deterministic execution latency between order emission and fill eligibility, so that backtests can model the delay a real order would experience without breaking determinism.

#### Acceptance Criteria

1. THE Backtesting_Engine SHALL apply a configured Latency_Model that determines the delay between a Simulated_Order's emission time and the earliest Simulation_Clock time at which the Fill_Model may execute it.
2. THE Backtesting_Engine SHALL support at least a zero-latency model as the MVP default, a fixed-latency model, and a configurable latency model, selectable via the Backtest_Configuration.
3. WHEN the Latency_Model imposes a delay, THE Fill_Model SHALL evaluate the Simulated_Order only against market data whose event time is at or after the order's emission time plus the modeled latency, preserving point-in-time correctness and never introducing Look_Ahead_Bias.
4. THE Backtesting_Engine SHALL compute latency deterministically, drawing any stochastic component only from the run's seeded random source, so that latency participates in deterministic replay.
5. THE Backtesting_Engine SHALL record the Latency_Model selection and its parameters in the Run_Manifest.

### Requirement 16: Slippage Models

**User Story:** As a quant researcher, I want configurable, deterministic slippage applied to fills, so that backtests account for market impact and spread instead of assuming perfect prices.

#### Acceptance Criteria

1. THE Backtesting_Engine SHALL apply a configured Slippage_Model to every Fill to adjust its execution price away from the reference price.
2. THE Backtesting_Engine SHALL support at least a zero-slippage model and one non-trivial slippage model (for example a fixed-basis-points or volume-proportional model), selectable via the Backtest_Configuration.
3. WHEN a Slippage_Model computes a price adjustment, THE Backtesting_Engine SHALL apply it deterministically so that identical inputs and parameters yield identical adjusted prices.
4. IF a Slippage_Model uses a stochastic component, THEN THE Backtesting_Engine SHALL draw it only from the run's seeded random source so that the result remains reproducible.

### Requirement 17: Commission Models

**User Story:** As a quant researcher, I want configurable, deterministic commissions charged on fills, so that backtest returns are net of realistic transaction costs.

#### Acceptance Criteria

1. THE Backtesting_Engine SHALL apply a configured Commission_Model to every Fill and SHALL debit the computed commission from the Cash_Ledger.
2. THE Backtesting_Engine SHALL support at least a zero-commission model and one non-trivial commission model (for example per-share, per-trade, or percentage-of-notional), selectable via the Backtest_Configuration.
3. WHEN a Commission_Model computes a cost, THE Backtesting_Engine SHALL compute it deterministically so that identical inputs and parameters yield identical commissions.
4. THE Backtesting_Engine SHALL include commissions in the Trade_Log and in all net Performance_Metrics.

### Requirement 18: Fill Models

**User Story:** As a quant researcher, I want a pluggable fill model that decides whether, when, and at what price orders fill, so that I can model execution assumptions explicitly and swap them without changing strategy code.

#### Acceptance Criteria

1. THE Backtesting_Engine SHALL determine every Fill through a Fill_Model that consumes the Simulated_Order, the point-in-time market data, the Slippage_Model, and the Commission_Model.
2. THE Backtesting_Engine SHALL support selecting the Fill_Model and its parameters through the Backtest_Configuration.
3. THE Fill_Model SHALL compute Fills deterministically so that identical inputs, parameters, and seed produce identical Fills.
4. THE Fill_Model SHALL NOT fill a Simulated_Order using market data whose Knowledge_Time is before the order's emission time in a way that would constitute Look_Ahead_Bias.
5. WHERE the Fill_Model models liquidity or partial fills, THE Backtesting_Engine SHALL cap the filled quantity at what the point-in-time market data supports under the model's rules.

### Requirement 19: Position Tracking

**User Story:** As a quant researcher, I want positions tracked exactly through every fill, so that holdings and per-instrument profit and loss are always correct and reproducible.

#### Acceptance Criteria

1. WHEN a Fill occurs, THE Backtesting_Engine SHALL update the affected Position's signed quantity and average cost basis according to the executed quantity and price.
2. THE Backtesting_Engine SHALL compute each Position's realized profit and loss on closing or reducing trades and its unrealized profit and loss from the current point-in-time market price.
3. THE Backtesting_Engine SHALL support long and short Positions and SHALL represent a flat Position as zero quantity.
4. THE Backtesting_Engine SHALL derive Position state solely from the ordered sequence of Fills so that replaying the same Fills reconstructs identical Positions.

### Requirement 20: Portfolio Tracking

**User Story:** As a quant researcher, I want the whole portfolio state tracked at every step, so that I can see total value and composition throughout the backtest.

#### Acceptance Criteria

1. THE Backtesting_Engine SHALL maintain a Portfolio comprising all Positions and the Cash_Ledger balance for the Backtest_Run.
2. WHEN the Portfolio is valued at a Simulation_Clock time, THE Backtesting_Engine SHALL compute total portfolio value as the Cash_Ledger balance plus the point-in-time market value of all Positions.
3. THE Backtesting_Engine SHALL update the Portfolio after every applied Fill and cash movement before advancing the Simulation_Clock to the next Event.
4. THE Backtesting_Engine SHALL expose the final Portfolio state as part of the Backtest_Result.

### Requirement 21: Cash Management

**User Story:** As a quant researcher, I want cash tracked precisely through purchases, sales, costs, and configured cash flows, so that the backtest can enforce affordability and report accurate balances.

#### Acceptance Criteria

1. WHEN a Backtest_Run begins, THE Backtesting_Engine SHALL initialize the Cash_Ledger to the starting cash defined in the Backtest_Configuration.
2. WHEN a Fill occurs, THE Backtesting_Engine SHALL debit or credit the Cash_Ledger by the fill notional and debit the commission.
3. THE Backtesting_Engine SHALL compute cash balances using a fixed decimal or otherwise exactly-reproducible arithmetic so that identical inputs yield identical balances with no floating-point ordering nondeterminism.
4. WHERE the Backtest_Configuration disallows leverage, THE Backtesting_Engine SHALL prevent a Simulated_Order from filling if it would drive the Cash_Ledger below the configured minimum, and SHALL record the constraint that blocked it.
5. THE Backtesting_Engine SHALL keep the Cash_Ledger consistent with the Trade_Log such that the ending cash equals starting cash adjusted by every recorded Fill notional and commission.

### Requirement 22: Margin and Leverage

**User Story:** As a risk officer, I want the engine to model margin, leverage, buying power, maintenance margin, and forced liquidation, so that leveraged strategies can be simulated realistically when enabled — while the MVP runs unleveraged.

#### Acceptance Criteria

1. THE Backtesting_Engine SHALL compute Buying_Power from the Cash_Ledger, Position values, and the configured leverage/margin parameters of the Backtest_Configuration.
2. WHERE leverage is disabled, which is the MVP default, THE Backtesting_Engine SHALL constrain Buying_Power to available cash and SHALL prevent any Fill that would exceed it, consistent with Cash Management.
3. WHERE leverage is enabled, THE Backtesting_Engine SHALL permit Positions up to the configured leverage limit and SHALL track the margin used and the Maintenance_Margin requirement.
4. WHEN the Portfolio's equity falls below the Maintenance_Margin requirement at Simulation_Clock time `t`, THE Backtesting_Engine SHALL simulate a Forced_Liquidation of Positions deterministically according to the configured rules and SHALL record each resulting order and Fill in the Trade_Log.
5. THE Backtesting_Engine SHALL compute margin, Buying_Power, Maintenance_Margin, and Forced_Liquidation outcomes deterministically so that identical inputs yield identical results.
6. THE Backtesting_Engine SHALL provide the margin and leverage behavior behind the same Backtest_Configuration and simulation core so that enabling leverage later requires no change to the Simulation_Engine core loop.

### Requirement 23: Performance Metrics

**User Story:** As a quant researcher, I want standard performance metrics computed from the backtest, so that I can evaluate a strategy without recomputing them by hand.

#### Acceptance Criteria

1. WHEN a Backtest_Run completes, THE Backtesting_Engine SHALL compute Performance_Metrics including at least total return, annualized return, Sharpe ratio, Sortino ratio, and win rate, net of commissions and slippage.
2. THE Backtesting_Engine SHALL compute Performance_Metrics from the Equity_Curve and Trade_Log of the same Backtest_Run.
3. THE Backtesting_Engine SHALL compute all Performance_Metrics deterministically so that identical inputs yield identical metric values.
4. WHERE a metric is undefined for a Backtest_Run (for example a Sharpe ratio when volatility is zero), THE Backtesting_Engine SHALL report the metric as explicitly undefined rather than substituting a misleading value.

### Requirement 24: Risk Metrics

**User Story:** As a risk officer, I want risk metrics computed from the backtest, so that I can judge a strategy's risk profile, not only its return.

#### Acceptance Criteria

1. WHEN a Backtest_Run completes, THE Backtesting_Engine SHALL compute Risk_Metrics including at least return volatility, maximum drawdown, and a value-at-risk estimate.
2. THE Backtesting_Engine SHALL compute Risk_Metrics from the Equity_Curve and Position history of the same Backtest_Run.
3. THE Backtesting_Engine SHALL compute all Risk_Metrics deterministically so that identical inputs yield identical metric values.
4. THE Backtesting_Engine SHALL report the exposure of the Portfolio over the Backtest_Run so that gross and net exposure can be inspected.

### Requirement 25: Trade Log

**User Story:** As an auditor, I want a complete, ordered record of every order and fill, so that any reported metric can be traced back to the trades that produced it.

#### Acceptance Criteria

1. WHEN a Simulated_Order is emitted, rejected, or filled, THE Backtesting_Engine SHALL append a corresponding entry to the Trade_Log recording the identifier, instrument, side, quantity, order type, price, commission, Simulation_Clock time, and outcome.
2. THE Backtesting_Engine SHALL keep the Trade_Log append-only within a Backtest_Run and ordered by Simulation_Clock time with the same total tie-breaking rule used for Events.
3. THE Backtesting_Engine SHALL expose the Trade_Log as part of the Backtest_Result.
4. THE Backtesting_Engine SHALL ensure the Trade_Log is sufficient, together with the Run_Manifest, to reconstruct the Portfolio, Cash_Ledger, and metrics of the Backtest_Run.

### Requirement 26: Equity Curve

**User Story:** As a quant researcher, I want the portfolio value sampled over time, so that I can see and chart how the strategy grew or shrank capital.

#### Acceptance Criteria

1. WHEN a Backtest_Run executes, THE Backtesting_Engine SHALL record an Equity_Curve as the ordered time series of total Portfolio value sampled at the configured interval.
2. THE Backtesting_Engine SHALL derive each Equity_Curve point from the point-in-time Portfolio valuation at that Simulation_Clock time.
3. THE Backtesting_Engine SHALL expose the Equity_Curve as part of the Backtest_Result.
4. THE Backtesting_Engine SHALL produce an identical Equity_Curve for identical inputs on every run.

### Requirement 27: Drawdown Calculation

**User Story:** As a risk officer, I want drawdowns computed from the equity curve, so that I can quantify the worst peak-to-trough losses a strategy would have suffered.

#### Acceptance Criteria

1. WHEN a Backtest_Run completes, THE Backtesting_Engine SHALL compute the Drawdown series as the decline of the Equity_Curve from its running peak at each point.
2. THE Backtesting_Engine SHALL compute the maximum Drawdown magnitude and the duration of the maximum Drawdown for the Backtest_Run.
3. THE Backtesting_Engine SHALL compute the Drawdown series and its summaries deterministically from the Equity_Curve.
4. THE Backtesting_Engine SHALL expose the Drawdown series and its summaries as part of the Backtest_Result.

### Requirement 28: Benchmark Comparison

**User Story:** As a quant researcher, I want the backtest compared against a benchmark, so that I can tell whether a strategy added value beyond a passive alternative.

#### Acceptance Criteria

1. WHERE a Backtest_Configuration specifies a Benchmark, THE Backtesting_Engine SHALL compute the Benchmark's return series over the same historical period using historical market data obtained only from the Market Data Service.
2. WHEN a Benchmark is configured, THE Backtesting_Engine SHALL compute relative performance measures against it, including at least excess return over the period.
3. THE Backtesting_Engine SHALL compute the Benchmark series and comparison deterministically so that identical inputs yield identical results.
4. WHERE no Benchmark is configured, THE Backtesting_Engine SHALL produce the Backtest_Result without a benchmark comparison rather than failing.

### Requirement 29: Strategy Configuration

**User Story:** As a quant researcher, I want to fully specify a backtest through typed configuration, so that a run is unambiguous, validated, and reproducible from its inputs.

#### Acceptance Criteria

1. THE Backtesting_Engine SHALL accept a Backtest_Configuration specifying the Strategy identity and parameters, the instrument universe, the start and end of the historical period, the starting cash, the Slippage_Model, Commission_Model, and Fill_Model selections and their parameters, the optional Benchmark, the equity-curve sampling interval, and the random seed.
2. THE Backtesting_Engine SHALL validate a Backtest_Configuration at submission and SHALL reject an invalid or incomplete configuration with a typed error identifying the offending field before any run begins.
3. THE Backtesting_Engine SHALL treat the Backtest_Configuration as an immutable input of the Backtest_Run and SHALL record it in the Run_Manifest.
4. IF a Backtest_Configuration omits the random seed, THEN THE Backtesting_Engine SHALL assign and record an explicit seed so that the run remains reproducible.

### Requirement 30: Stable Run Identity

**User Story:** As a platform maintainer, I want every backtest run to carry a globally unique, immutable identifier assigned at creation, so that all results, reports, and artifacts reference one stable identity.

#### Acceptance Criteria

1. WHEN a Backtest_Run is created, THE Backtesting_Engine SHALL assign it a globally unique, immutable Run_Uuid.
2. THE Backtesting_Engine SHALL NOT change or reuse a Backtest_Run's Run_Uuid for the lifetime of that run.
3. THE Backtesting_Engine SHALL reference the Backtest_Run's Run_Uuid from its Run_Manifest, Backtest_Result, Trade_Log, Equity_Curve, and every persisted Result_Artifact and report.
4. THE Backtest_API SHALL identify a Backtest_Run by its Run_Uuid in every endpoint that accepts or returns a run identifier.

### Requirement 31: Deterministic Execution

**User Story:** As a platform architect, I want the engine to be fully deterministic, so that the same inputs always yield the same results and any nondeterminism is treated as a defect.

#### Acceptance Criteria

1. WHEN two Backtest_Runs execute with the same Run_Manifest — the same model version(s), historical data selection, configuration, parameters, code commit, and seed — THE Backtesting_Engine SHALL produce byte-for-byte identical Backtest_Results, including the Trade_Log, Fills, Positions, Cash_Ledger, Portfolio snapshots, Equity_Curve, Drawdown, Performance_Metrics, and Risk_Metrics.
2. THE Backtesting_Engine SHALL derive every stochastic value used in a Backtest_Run solely from the run's single seeded random source.
3. THE Backtesting_Engine SHALL NOT let wall-clock time, unordered-collection iteration order, concurrency scheduling, hash-seed randomization, or any other nondeterministic source affect a Backtest_Result.
4. THE Backtesting_Engine SHALL order every computation over a collection of Events, orders, or instruments by a fixed, total, documented ordering rule.

### Requirement 32: Reproducibility and Run Manifest

**User Story:** As a platform architect, I want each backtest reproducible from an immutable manifest, so that any past result can be regenerated and audited exactly.

#### Acceptance Criteria

1. WHEN a Backtest_Run completes, THE Backtesting_Engine SHALL persist its Run_Manifest and its Backtest_Result together, keyed such that the result is retrievable by the run's identifier.
2. WHEN a Backtest_Run is re-executed from a persisted Run_Manifest against the same upstream data versions, THE Backtesting_Engine SHALL reproduce the original Backtest_Result identically.
3. THE Backtesting_Engine SHALL keep the persisted Run_Manifest and Backtest_Result immutable after the run completes.
4. THE Backtest_API SHALL expose an endpoint that returns the Run_Manifest of a completed Backtest_Run.

### Requirement 33: Recovery and Replay

**User Story:** As a platform operator, I want clearly defined behavior for interrupted runs, so that a failed run never yields an ambiguous result and can be re-executed reproducibly.

#### Acceptance Criteria

1. IF a Backtest_Run is interrupted before reaching the COMPLETED Run_Status, THEN THE Backtesting_Engine SHALL mark it FAILED and SHALL NOT expose a partial result as complete.
2. THE Backtesting_Engine SHALL require that a failed or interrupted Backtest_Run be restarted only by creating a new Backtest_Run with a new Run_Uuid, unless explicit checkpointing is implemented in the future.
3. WHERE checkpointing is not implemented, THE Backtesting_Engine SHALL NOT resume a partially-completed Backtest_Run in place.
4. WHEN a Backtest_Run is re-executed from the same Run_Manifest against the same upstream data versions, THE Backtesting_Engine SHALL reproduce identical results, consistent with Deterministic Execution and Reproducibility.
5. WHERE checkpointing is introduced in the future, THE Backtesting_Engine SHALL implement it such that resuming from a checkpoint yields results identical to an uninterrupted run of the same Run_Manifest.

### Requirement 34: Risk Kernel Sovereignty and Non-Bypass

**User Story:** As a risk-conscious platform owner, I want the backtest to route orders through the same risk path as live and never bypass the risk kernel, so that a strategy validated in backtest was validated under the same guardrails it will face with capital.

#### Acceptance Criteria

1. THE Backtesting_Engine SHALL route every Simulated_Order through the shared risk-check logic before the Fill_Model may execute it.
2. WHERE the platform Risk_Kernel is configured for the Backtesting_Engine, THE Backtesting_Engine SHALL submit every Simulated_Order to the Risk_Kernel and SHALL NOT fill an order the Risk_Kernel rejects.
3. THE Backtesting_Engine SHALL NOT provide any configuration, flag, or code path that bypasses, disables, or relaxes the shared risk checks or the Risk_Kernel for a Backtest_Run.
4. THE Backtesting_Engine SHALL NOT modify, raise, or override any Risk_Kernel limit.
5. WHEN a Simulated_Order is rejected by the shared risk logic or the Risk_Kernel, THE Backtesting_Engine SHALL record the rejection and its reason in the Trade_Log and SHALL continue the Backtest_Run.

### Requirement 35: Batch and Parameter Sweep Extensibility

**User Story:** As a quant researcher, I want the architecture to support running many configurations as independent runs in the future, so that parameter sweeps become possible without redesigning the engine.

#### Acceptance Criteria

1. THE Backtesting_Engine SHALL structure a Backtest_Run so that multiple Backtest_Configurations can, in the future, be executed as independent Backtest_Runs, each with its own Run_Uuid, Run_Manifest, and Backtest_Result.
2. THE Backtesting_Engine SHALL keep each Backtest_Run independent and deterministic such that running several configurations produces the same per-run results as running each configuration alone.
3. THE Backtesting_Engine SHALL treat batch or parameter-sweep orchestration as a future extensibility capability and SHALL NOT be required to implement multi-configuration orchestration in the MVP.
4. WHERE batch or parameter-sweep execution is added later, THE Backtesting_Engine SHALL support it without changing the Simulation_Engine core, the determinism guarantees, or the single-run result contract.

### Requirement 36: Backtest REST API

**User Story:** As a quant researcher, I want a REST API to configure, run, and retrieve backtests, so that I can integrate backtesting into research and validation workflows without direct database or filesystem access.

#### Acceptance Criteria

1. THE Backtest_API SHALL expose an endpoint that submits a Backtest_Configuration and initiates a Backtest_Run, returning the run's identifier.
2. THE Backtest_API SHALL expose an endpoint that reports the status of a Backtest_Run (for example pending, running, completed, or failed).
3. THE Backtest_API SHALL expose an endpoint that returns the Backtest_Result of a completed Backtest_Run, including the Trade_Log, Equity_Curve, Drawdown, Performance_Metrics, Risk_Metrics, benchmark comparison, and final Portfolio state.
4. THE Backtest_API SHALL expose an endpoint that lists Backtest_Runs, optionally filtered by strategy, model name, or status.
5. THE Backtest_API SHALL expose an endpoint that returns the Run_Manifest of a Backtest_Run.
6. THE Backtest_API SHALL require every mutating endpoint to accept a client-supplied idempotency key such that a retried submission never initiates a duplicate Backtest_Run.
7. THE Backtest_API SHALL expose OpenAPI documentation describing every exposed endpoint.
8. IF a request identifies a Backtest_Run, Backtest_Result, or Run_Manifest that does not exist, THEN THE Backtest_API SHALL respond with a 404 response and a typed error body identifying the missing resource.

### Requirement 37: Failure Handling

**User Story:** As a platform operator, I want the engine to fail loudly and cleanly on the money-relevant path, so that a broken run is never mistaken for a valid result.

#### Acceptance Criteria

1. IF any required upstream dependency — the Market Data Service, the Model Registry, or, where required, the Feature Store — is unreachable or returns an error while data essential to a Backtest_Run is being retrieved, THEN THE Backtesting_Engine SHALL fail the Backtest_Run, mark it failed, and record the failure reason.
2. WHEN a Backtest_Run fails, THE Backtesting_Engine SHALL NOT present a partial Backtest_Result as complete and SHALL make the failure and its reason retrievable via the Backtest_API.
3. IF the internal look-ahead guard detects an access to data whose Knowledge_Time is after the current Simulation_Clock time, THEN THE Backtesting_Engine SHALL fail the Backtest_Run with a diagnostic identifying the offending access.
4. WHEN a Backtest_Run fails partway, THE Backtesting_Engine SHALL leave no persisted Backtest_Result that claims completeness for that run.
5. THE Backtesting_Engine SHALL record a human-readable reason for every failed or rejected Backtest_Run.

### Requirement 38: Repository and Persistence Requirements

**User Story:** As a platform maintainer, I want the engine to own its database with immutable result guarantees, so that stored backtest results cannot be silently altered.

#### Acceptance Criteria

1. THE Backtesting_Engine SHALL own its own database and SHALL NOT share a database with any other backend service.
2. THE Backtesting_Engine SHALL persist each Backtest_Run's Run_Manifest and Backtest_Result such that they are written once and never updated after the run completes.
3. THE Backtesting_Engine SHALL persist the Trade_Log and Equity_Curve as append-only records within a Backtest_Run with no update or delete path.
4. THE Backtesting_Engine SHALL provide a repository operation that retrieves a Backtest_Run and its Backtest_Result by the run's identifier.
5. WHERE large result artifacts (for example a long Equity_Curve or Trade_Log) are persisted, THE Backtesting_Engine SHALL store them behind a storage interface that is swappable for an object store without any change to domain or API logic.

### Requirement 39: Backtest Artifact Versioning and Integrity

**User Story:** As a platform maintainer, I want every persisted report and result artifact stored behind a storage interface, checksum-verified, and version-traceable, so that artifacts cannot be silently corrupted or confused across runs.

#### Acceptance Criteria

1. THE Backtesting_Engine SHALL persist reports, Trade_Logs, Equity_Curves, Drawdown series, metric sets, and Run_Manifests as Result_Artifacts behind a storage interface that is swappable for an object store without any change to domain or API logic.
2. WHEN the Backtesting_Engine persists a Result_Artifact, THE Backtesting_Engine SHALL compute and store a checksum of that artifact.
3. WHEN the Backtesting_Engine retrieves a Result_Artifact, THE Backtesting_Engine SHALL verify the artifact's bytes against its recorded checksum and SHALL refuse to serve an artifact whose bytes do not match, recording an integrity-failure reason.
4. THE Backtesting_Engine SHALL make every persisted Result_Artifact traceable to the Run_Uuid and Run_Manifest that produced it.
5. THE Backtesting_Engine SHALL version Result_Artifacts such that artifacts from different Backtest_Runs are never conflated or overwritten.

### Requirement 40: Immutable Results

**User Story:** As an auditor, I want completed backtest results to be immutable, so that a result, once produced, can be trusted never to change.

#### Acceptance Criteria

1. WHEN a Backtest_Run reaches the COMPLETED Run_Status, THE Backtesting_Engine SHALL treat every persisted result, metric, report, and Result_Artifact of that run as immutable.
2. THE Backtesting_Engine SHALL NOT provide any operation that modifies or deletes a COMPLETED Backtest_Run's Backtest_Result, Performance_Metrics, Risk_Metrics, Trade_Log, Equity_Curve, Drawdown series, reports, or Run_Manifest.
3. IF a change to a COMPLETED Backtest_Run's outputs is required, THEN THE Backtesting_Engine SHALL require a new Backtest_Run rather than mutating the existing one.
4. THE Backtesting_Engine SHALL enforce the immutability of COMPLETED results at the persistence layer with no update or delete path.

### Requirement 41: Non-Functional — Observability and Maintainability

**User Story:** As a platform maintainer, I want the engine observable and consistently structured, so that its behavior can be diagnosed in production and maintained like every other service.

#### Acceptance Criteria

1. THE Backtesting_Engine SHALL expose `/health`, `/health/live`, and `/health/ready` endpoints consistent with the existing backend services, WHERE the readiness check additionally verifies connectivity to its own database and to any configured result-artifact store.
2. THE Backtesting_Engine SHALL emit structured logs carrying a correlation identifier for every request and every Backtest_Run.
3. THE Backtesting_Engine SHALL follow the platform's domain/adapters/api layering so that the Simulation_Engine and metric logic are pure and independent of transport and persistence.
4. THE Backtesting_Engine SHALL keep the Simulation_Engine and all metric computations free of I/O and wall-clock reads so that they are deterministically unit-testable.

### Requirement 42: Docker Deployment

**User Story:** As a platform operator, I want the engine deployable via the same Docker conventions as the existing services, so that it fits the existing local and future cloud workflows without special-casing.

#### Acceptance Criteria

1. THE Backtesting_Engine SHALL build into a container image using the shared parameterized `docker/Dockerfile.service` pattern used by market-data, feature-store, dataset-builder, training-pipeline, and model-registry.
2. THE Backtesting_Engine SHALL register a `backtesting-engine` service entry in the root `docker-compose.yml` that exposes port `8010` and depends on its own dedicated Postgres database service.
3. THE Backtesting_Engine SHALL register a `backtesting-engine-db` Postgres service entry in the root `docker-compose.yml`, exposing port `5437`, that is not shared with any other backend service.
4. THE Backtesting_Engine SHALL reach the Market Data Service, the Model Registry, and the Feature Store only via their published REST base URLs configured through environment variables, and SHALL NOT depend on another service's database container.
5. THE Backtesting_Engine SHALL read all configuration from `AQROS_*` environment variables, consistent with the existing services.

### Requirement 43: Testing Requirements

**User Story:** As a platform maintainer, I want comprehensive automated tests for the engine, so that its determinism, point-in-time correctness, and integrations are verified without manual testing.

#### Acceptance Criteria

1. THE Backtesting_Engine SHALL include unit tests that exercise the Simulation_Engine, Fill_Model, Slippage_Model, Commission_Model, position/portfolio/cash tracking, and metric computations against fakes for every port, including the Market_Data_Client, Model_Registry_Client, and Feature_Store_Client ports.
2. THE Backtesting_Engine SHALL include property-based tests for its core invariants, including deterministic replay (identical inputs yield identical results), event-ordering totality, cash-ledger consistency with the Trade_Log, position reconstruction from Fills, and the absence of Look_Ahead_Bias.
3. THE Backtesting_Engine SHALL include a deterministic golden-replay test that runs a fixed Backtest_Configuration against fixed historical data and a fixed model and asserts the Backtest_Result matches a stored golden result exactly.
4. THE Backtesting_Engine SHALL include integration tests that exercise its REST API through an HTTP client against a real Postgres database provisioned via testcontainers, with the Market Data Service, Model Registry, and Feature Store integrations exercised against faked clients without requiring live upstream instances.
5. WHEN the Backtesting_Engine's automated test suite is executed, THE Backtesting_Engine SHALL report a non-zero exit status IF any test fails or IF a test setup, missing test file, or test configuration error prevents the suite from running to completion.

### Requirement 44: Quality Gates

**User Story:** As a platform maintainer, I want the engine held to the same linting, formatting, and type-checking standards as the rest of the monorepo, so that the codebase stays consistent and maintainable.

#### Acceptance Criteria

1. THE Backtesting_Engine SHALL pass Ruff linting using the rule set configured in the root `pyproject.toml`.
2. THE Backtesting_Engine SHALL pass Black formatting checks using the configuration in the root `pyproject.toml`.
3. THE Backtesting_Engine SHALL pass MyPy strict type checking using the configuration in the root `pyproject.toml`, with every function and public interface fully type-hinted.

### Requirement 45: Explicit Non-Goals

**User Story:** As a platform architect, I want the engine's responsibilities explicitly bounded, so that it never duplicates upstream services' responsibilities or crosses into training, promotion, or live execution.

#### Acceptance Criteria

1. THE Backtesting_Engine SHALL NOT query the Training Pipeline for any purpose.
2. THE Backtesting_Engine SHALL NOT train, retrain, fine-tune, or otherwise modify any model.
3. THE Backtesting_Engine SHALL NOT modify historical market data or any other upstream service's data.
4. THE Backtesting_Engine SHALL NOT obtain a model by any path other than the Model Registry, and SHALL NOT bypass the Model Registry's governance to evaluate an unapproved model unless an explicit Model_Version is pinned for research and recorded as such.
5. THE Backtesting_Engine SHALL NOT bypass, disable, or relax the shared risk logic or the Risk_Kernel.
6. THE Backtesting_Engine SHALL NOT execute, route, or place any order against a real or paper venue, broker, or exchange.
7. THE Backtesting_Engine SHALL NOT compute or serve model training metrics or promotion decisions; it produces Backtest_Results that a separate validation and promotion process may consume.
8. THE Backtesting_Engine SHALL NOT fork or reimplement the shared strategy, risk, sizing, or order-management logic that lives in `libs/`.
