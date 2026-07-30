"""Pure domain model for the Backtesting Engine service.

Frozen/slots dataclasses and ``StrEnum``s only — no I/O, no framework
dependencies, no wall-clock reads. This mirrors
``aqros_model_registry.domain.models`` exactly in spirit: everything here is
data, and every side effect (HTTP calls, database access, filesystem access)
is pushed behind the ports defined in ``domain/ports.py``.

All upstream payloads (bars, resolved-model records, features) are **local,
decoupled copies**, populated only from the Market Data Service's, Model
Registry's, and Feature Store's published REST APIs — never imported from
``aqros_market_data``, ``aqros_model_registry``, ``aqros_feature_store``, or
``aqros_training_pipeline`` (CLAUDE.md §7.9), exactly as
``aqros_model_registry`` duplicates the Training Pipeline's shapes rather
than importing them.

The ``Strategy``, ``OrderIntent``, ``Order``, and ``RiskCheck`` contracts
are **referenced from** ``libs/aqros_strategy_core``, not redefined here
(Design Decision 1; CLAUDE.md §7.1) — this module imports ``OrderIntent``
and ``Order`` from that shared library so backtest, paper, and live share
one definition of the money-path shapes. ``OrderSide`` and ``OrderType``
are re-declared here as local ``StrEnum``s (matching design.md Section 4's
literal domain model, and identical in value to
``aqros_strategy_core.contracts.OrderSide``/``OrderType``) since the local
``SimulatedOrder``/``Fill``/``TradeLogEntry`` shapes are this service's own
decoupled copies rather than direct re-exports.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from aqros_strategy_core.contracts import Order, OrderIntent

__all__ = ["Order", "OrderIntent"]  # re-exported from aqros_strategy_core; see module docstring


class OrderSide(StrEnum):
    """The direction of a ``SimulatedOrder`` or ``Fill`` (Requirement 14, 19)."""

    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    """The order types supported by the simulation (Requirement 14)."""

    MARKET = "market"
    LIMIT = "limit"


class RunStatus(StrEnum):
    """The lifecycle status of a ``Backtest_Run`` (Requirement 33)."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AssetClass(StrEnum):
    """The category of a tradable instrument, behind an abstraction so the
    ``Simulation_Engine`` core stays asset-class agnostic (Requirement 11).
    """

    EQUITY = "equity"
    # reserved future implementations — added behind the abstraction, no core
    # change (Requirement 11):
    FUTURE = "future"
    OPTION = "option"
    FOREX = "forex"
    CRYPTO = "crypto"


class OrderStatus(StrEnum):
    """The outcome of a ``SimulatedOrder`` at a point in time (Requirement 14, 25)."""

    PENDING = "pending"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    REJECTED = "rejected"
    UNFILLED = "unfilled"


class CorporateActionType(StrEnum):
    """The kinds of ``CorporateAction`` the engine applies point-in-time (Requirement 8)."""

    SPLIT = "split"
    REVERSE_SPLIT = "reverse_split"
    CASH_DIVIDEND = "cash_dividend"
    STOCK_DIVIDEND = "stock_dividend"
    SYMBOL_CHANGE = "symbol_change"
    MERGER = "merger"
    DELISTING = "delisting"


class EventKind(StrEnum):
    """The kind of a totally-ordered ``Event`` (Requirement 6).

    Fixes the intra-instant tie-break priority used by ``Event.ordering_key``:
    a corporate action is applied before market data at the same instant, a
    market bar precedes an order becoming fill-eligible, and an equity
    sample is taken last.
    """

    CORPORATE_ACTION = "corporate_action"  # applied before market data at the same instant
    MARKET_BAR = "market_bar"
    ORDER_ELIGIBLE = "order_eligible"  # a latency-delayed order becomes fill-eligible
    EQUITY_SAMPLE = "equity_sample"


_KIND_PRIORITY: dict[EventKind, int] = {
    EventKind.CORPORATE_ACTION: 0,
    EventKind.MARKET_BAR: 1,
    EventKind.ORDER_ELIGIBLE: 2,
    EventKind.EQUITY_SAMPLE: 3,
}


@dataclass(frozen=True, slots=True)
class BacktestConfiguration:
    """The complete, typed, immutable specification of a ``Backtest_Run`` (Requirement 29)."""

    strategy_id: str
    strategy_params: dict[str, object]
    model_name: str
    model_version: int | None  # None => resolve production champion (Req 9.2)
    universe: tuple[str, ...]
    exchange: str
    start: datetime  # timezone-aware, exchange TZ
    end: datetime
    starting_cash: Decimal
    bar_interval: str  # e.g. "daily"
    slippage_model: str
    slippage_params: dict[str, object]
    commission_model: str
    commission_params: dict[str, object]
    fill_model: str
    fill_params: dict[str, object]
    latency_model: str  # default "zero"
    latency_params: dict[str, object]
    leverage_enabled: bool  # MVP default False (Req 22.2)
    max_leverage: Decimal
    equity_sample_interval: str
    benchmark_symbol: str | None
    seed: int  # assigned + recorded if omitted (Req 29.4)
    asset_class: AssetClass = AssetClass.EQUITY


@dataclass(frozen=True, slots=True)
class ResolvedModel:
    """A single model version resolved via the Model Registry, pinned into the manifest (Requirement 9)."""

    model_name: str
    version: int
    checksum: str
    checksum_algorithm: str
    lineage: dict[str, object]  # local decoupled copy from the Registry
    resolved_as: str  # "production" | "pinned"


@dataclass(frozen=True, slots=True)
class RunManifest:
    """The immutable, content-addressable record fully specifying a ``Backtest_Run`` (Requirement 12)."""

    run_uuid: UUID
    engine_git_commit: str
    strategy_core_git_commit: str
    configuration: BacktestConfiguration
    resolved_models: tuple[ResolvedModel, ...]
    feature_versions: dict[str, int]
    universe: tuple[str, ...]
    period_start: datetime
    period_end: datetime
    knowledge_time_boundary: datetime  # no fact after this is ever visible
    calendar_source: str  # calendar source/version (Req 7.6)
    price_adjustment_convention: str  # convention carried by the bars (Decision 6)
    corporate_actions_applied: tuple[str, ...]
    corporate_actions_unavailable: tuple[str, ...]  # instruments with no feed (Decision 6)
    library_versions: dict[str, str]
    seed: int


@dataclass(frozen=True, slots=True)
class Instrument:
    """A local, decoupled representation of a tradable instrument (Requirement 11)."""

    symbol: str
    asset_class: AssetClass
    exchange: str


@dataclass(frozen=True, slots=True)
class Bar:
    """A local decoupled copy of a Market Data OHLCV bar (Requirement 3)."""

    symbol: str
    event_time: datetime  # bar timestamp as provided by Market Data
    knowledge_time: datetime  # derived = session close (Decision 3)
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True, slots=True)
class CorporateAction:
    """A local decoupled copy of a Market Data corporate action (Requirement 8)."""

    symbol: str
    action_type: CorporateActionType
    event_time: datetime
    knowledge_time: datetime
    ratio: Decimal | None  # split / reverse-split factor
    cash_amount: Decimal | None  # cash dividend per share
    successor_symbol: str | None  # symbol change / merger target
    source: str  # always "market-data" (Req 8.7)


@dataclass(frozen=True, slots=True)
class Event:
    """A single time-stamped occurrence in the simulation (Requirement 6)."""

    event_time: datetime
    knowledge_time: datetime
    kind: EventKind
    sequence: int  # stable ingest sequence for total ordering
    payload: object  # Bar | CorporateAction | order id | sample marker

    @property
    def ordering_key(self) -> tuple[datetime, int, int]:
        """Total, documented tie-break: ``(event_time, kind priority, sequence)``."""
        return (self.event_time, _KIND_PRIORITY[self.kind], self.sequence)


@dataclass(frozen=True, slots=True)
class SimulatedOrder:
    """An order emitted by the shared strategy/OMS logic and tracked through the simulation (Requirement 14)."""

    client_order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    limit_price: Decimal | None
    emitted_at: datetime
    eligible_at: datetime  # emitted_at + modeled latency (Req 15.1)
    status: OrderStatus
    reject_reason: str | None


@dataclass(frozen=True, slots=True)
class Fill:
    """A local, decoupled record of a single simulated execution (Requirement 14, 18).

    Shares its shape with ``aqros_strategy_core.contracts.Fill`` by
    convention (both are the money-path execution record) but is declared
    locally so the engine's persistence and trade-log translation layers
    depend only on this service's own domain model.
    """

    client_order_id: str
    symbol: str
    side: OrderSide
    quantity: Decimal  # filled quantity (may be partial, Req 14.5)
    price: Decimal  # slippage-adjusted execution price
    commission: Decimal
    filled_at: datetime


@dataclass(frozen=True, slots=True)
class Position:
    """The engine's tracked holding in a single instrument (Requirement 19)."""

    symbol: str
    quantity: Decimal  # signed; 0 == flat (Req 19.3)
    average_cost: Decimal
    realized_pnl: Decimal


@dataclass(frozen=True, slots=True)
class CashLedger:
    """The engine's tracked cash balance (Requirement 21)."""

    balance: Decimal  # Decimal arithmetic only (Req 21.3)
    starting_cash: Decimal


@dataclass(frozen=True, slots=True)
class Portfolio:
    """The complete tracked state of a ``Backtest_Run`` at a point in time (Requirement 20)."""

    cash: CashLedger
    positions: tuple[Position, ...]  # ordered by symbol for determinism
    as_of: datetime


@dataclass(frozen=True, slots=True)
class TradeLogEntry:
    """A single append-only ``Trade_Log`` entry (Requirement 25)."""

    sequence: int
    client_order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    price: Decimal | None
    commission: Decimal
    clock_time: datetime
    outcome: OrderStatus  # emitted | filled | partially_filled | rejected | unfilled
    reason: str | None


@dataclass(frozen=True, slots=True)
class EquityPoint:
    """A single sampled point of the ``Equity_Curve`` (Requirement 26)."""

    clock_time: datetime
    total_value: Decimal


@dataclass(frozen=True, slots=True)
class DrawdownSummary:
    """The maximum drawdown magnitude and duration for a ``Backtest_Run`` (Requirement 27)."""

    max_drawdown: Decimal
    max_drawdown_start: datetime
    max_drawdown_trough: datetime
    max_drawdown_duration: timedelta


@dataclass(frozen=True, slots=True)
class PerformanceMetrics:
    """The computed return-and-risk-adjusted summary of a ``Backtest_Run`` (Requirement 23)."""

    total_return: Decimal
    annualized_return: Decimal
    sharpe_ratio: Decimal | None  # None => explicitly undefined (Req 23.4)
    sortino_ratio: Decimal | None
    win_rate: Decimal


@dataclass(frozen=True, slots=True)
class RiskMetrics:
    """The computed risk summary of a ``Backtest_Run`` (Requirement 24)."""

    volatility: Decimal
    max_drawdown: Decimal
    value_at_risk: Decimal
    gross_exposure: Decimal
    net_exposure: Decimal


@dataclass(frozen=True, slots=True)
class BenchmarkComparison:
    """The benchmark return series comparison for a ``Backtest_Run`` (Requirement 28)."""

    benchmark_symbol: str
    benchmark_return: Decimal
    excess_return: Decimal


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """The complete, immutable output of a ``Backtest_Run`` (Requirement 36)."""

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


@dataclass(frozen=True, slots=True)
class FeatureValue:
    """A single, local decoupled copy of a Feature Store feature value (Requirement 10).

    Bitemporal per platform convention: ``event_time`` is the bar this value
    describes; ``knowledge_time`` is when the feature became knowable. The
    ``FeatureStoreClient`` port only ever returns values whose
    ``knowledge_time`` is at or before the ``as_of`` cutoff it was called
    with (Requirement 4.3, 10.2) — never imported from
    ``aqros_feature_store`` (CLAUDE.md §7.9).
    """

    symbol: str
    feature_name: str
    feature_version: int
    event_time: datetime
    value: float
    knowledge_time: datetime


@dataclass(frozen=True, slots=True)
class BacktestRun:
    """The persisted run-metadata row for a ``Backtest_Run`` (Requirement 30, 36, 38).

    Distinct from ``BacktestResult``: this is the lightweight status/identity
    record returned by ``GET /v1/backtests/{run_uuid}`` and
    ``GET /v1/backtests``, available from the moment a run is created
    (``PENDING``) through its terminal status, independent of whether a full
    ``BacktestResult`` has been written yet.
    """

    run_uuid: UUID
    strategy_id: str
    model_name: str
    model_version: int | None
    status: RunStatus
    created_at: datetime
    completed_at: datetime | None
    failure_reason: str | None


@dataclass(frozen=True, slots=True)
class ExchangeCalendarData:
    """Deterministic, versioned exchange trading-calendar data (Requirement 7).

    Supplied by the ``CalendarProvider`` port to the pure domain
    ``Trading_Calendar``, which derives concrete session open/close
    timestamps for any date in range from this data. ``timezone`` is an IANA
    time-zone name (e.g. ``"America/New_York"``) so the pure
    ``Trading_Calendar`` can resolve DST-safe local times without reading
    wall-clock time or system tz state; ``regular_open``/``regular_close``
    are the exchange's normal session times-of-day; ``holidays`` are dates
    with no session at all; ``half_days`` are session dates with a
    shortened close time-of-day, keyed by date; ``source`` is the calendar
    source/version string recorded in the ``Run_Manifest`` (Requirement 7.6).
    """

    exchange: str
    timezone: str
    regular_open: time
    regular_close: time
    holidays: frozenset[date]
    half_days: dict[date, time]  # session date -> shortened close time-of-day
    source: str
