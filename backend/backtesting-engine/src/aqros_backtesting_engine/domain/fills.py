"""Fill domain models for the Backtesting Engine.

A ``FillModel`` (design.md Decision 10, Section 10 "Order Execution,
Latency, Slippage, Commission, and Fills") decides whether, when, at what
quantity, and at what price a ``SimulatedOrder`` fills against a single
point-in-time ``Bar``, composing the configured ``SlippageModel`` and
``CommissionModel``. Execution happens only through a ``FillModel`` — never
against a live venue or broker (Requirements 14.1, 45.6).

Two implementations are provided, both selectable via the
``Backtest_Configuration``'s ``fill_model``/``fill_params``:

- ``ImmediateFillModel`` fills the full ``order.quantity`` whenever the
  order is fillable at all — the simplest model, with no partial fills.
- ``LiquidityCappedFillModel`` caps the filled quantity at a configurable
  fraction (``max_participation_rate``, default ``Decimal("0.1")`` = 10%)
  of the bar's volume, returning a partially-filled ``Fill`` when
  ``order.quantity`` exceeds that cap. Tracking the remaining unfilled
  quantity across subsequent bars is the caller's (the future
  ``Simulation_Engine``'s) responsibility — this model only ever reports
  what filled against the single bar it was given (Requirement 18.5).

**Order types.** ``MARKET`` orders use the bar's ``open`` price as their
execution reference: the convention throughout this engine is that a
decision made at a bar's *close* (its knowledge time; Decision 3) becomes
fill-eligible no earlier than the *next* bar, and it executes at that next
bar's ``open`` — the earliest price actually tradable once the order is
known and eligible, never the same bar's own close (which would presume
foresight). ``LIMIT`` orders fill only when the point-in-time bar's range
satisfies the limit: a ``BUY`` limit fills when the market traded at or
below the limit at some point during the bar (``bar.low <= limit_price``),
and a ``SELL`` limit fills when it traded at or above the limit
(``bar.high >= limit_price``) (Requirement 14.4). A satisfied limit order's
reference price for slippage purposes is the limit price itself — the
worst price the order is contractually willing to accept — rather than a
bar-derived price, since the bar's range only establishes that the limit
*could* have traded, not at what exact price.

**Look-ahead and eligibility.** Every ``fill()`` call first funnels the
bar's knowledge time through ``assert_knowable`` (Requirement 5.2; letting
``LookAheadViolationError`` propagate rather than swallowing it), and then
refuses to fill an order that is not yet eligible at the current clock
(``clock < order.eligible_at``), returning ``None`` rather than filling
early (Requirements 15.3, 18.4). A ``None`` return means the order cannot
fill *against this bar* for any reason — not yet eligible, the bar is not
knowable, or a limit price is not yet satisfied — leaving the caller to
retry against a later bar or record the order as unfilled.

**Determinism.** Every implementation here is pure and deterministic:
given the same ``order``, ``bar``, ``clock``, model parameters, and the
same state of the run's single seeded ``random.Random``, ``fill()`` always
returns the same result (or ``None``). Any stochastic component comes only
from the composed ``slippage_model``/``commission_model`` and the ``rng``
passed through to them — this module draws nothing from its own random
source (Requirement 18.3).

This module performs no I/O and reads no wall-clock time.
"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from aqros_backtesting_engine.domain.commission import CommissionModel
from aqros_backtesting_engine.domain.lookahead import assert_knowable
from aqros_backtesting_engine.domain.models import (
    Bar,
    Fill,
    OrderSide,
    OrderType,
    SimulatedOrder,
)
from aqros_backtesting_engine.domain.slippage import SlippageModel

__all__ = [
    "FillModel",
    "ImmediateFillModel",
    "LiquidityCappedFillModel",
]


class FillModel(ABC):
    """Port/ABC for deciding whether, when, at what quantity, and at what price a
    ``SimulatedOrder`` fills against a single point-in-time ``Bar``.

    Selected and parameterized via the ``Backtest_Configuration``'s
    ``fill_model``/``fill_params`` and pinned into the ``Run_Manifest`` for
    reproducibility — recording the manifest entry is the caller's
    (``BacktestService``'s) responsibility, not this module's.

    ``fill()`` itself is a concrete template method shared by every
    implementation: it enforces the look-ahead guard and order eligibility,
    resolves the reference price for market/limit orders, and composes the
    configured ``SlippageModel``/``CommissionModel``. Subclasses customize
    only how much of ``order.quantity`` is fillable against the bar's
    point-in-time liquidity, via ``_max_fillable_quantity``.
    """

    def fill(
        self,
        order: SimulatedOrder,
        bar: Bar,
        clock: datetime,
        slippage_model: SlippageModel,
        commission_model: CommissionModel,
        rng: random.Random,
    ) -> Fill | None:
        """Attempt to fill ``order`` against ``bar`` at the current ``clock`` time.

        Returns ``None`` if the order cannot fill at all against this bar
        — the bar is not yet knowable at ``clock``, the order is not yet
        eligible, or (for a ``LIMIT`` order) the limit price is not
        satisfied by the bar's range — rather than a ``Fill``. Otherwise
        returns a ``Fill`` whose ``quantity`` may be less than
        ``order.quantity`` when the model caps the fill at point-in-time
        liquidity; the caller distinguishes a full fill from a partial fill
        by comparing ``Fill.quantity`` to ``order.quantity`` (Requirement
        14.5).

        Args:
            order: the ``SimulatedOrder`` being evaluated.
            bar: the point-in-time ``Bar`` to fill against.
            clock: the current ``Simulation_Clock`` time.
            slippage_model: the configured ``SlippageModel`` used to adjust
                the execution price away from the reference price.
            commission_model: the configured ``CommissionModel`` used to
                compute the transaction cost of the fill.
            rng: the ``Backtest_Run``'s single seeded random source, passed
                through to ``slippage_model.adjust`` unchanged.

        Raises:
            LookAheadViolationError: if ``bar.knowledge_time`` is after
                ``clock`` — propagated rather than swallowed, so a
                look-ahead violation fails the run loudly (Requirement
                37.3).
        """
        assert_knowable(
            bar.knowledge_time,
            clock,
            context=f"fill {order.client_order_id} against {order.symbol} bar {bar.event_time.isoformat()}",
        )

        if clock < order.eligible_at:
            # Not yet eligible at this event — not a failure, just not fillable yet.
            return None

        reference_price = self._reference_price(order, bar)
        if reference_price is None:
            # MARKET orders always resolve a reference price; only a LIMIT
            # order whose limit is not yet satisfied by the bar's range
            # reaches this branch.
            return None

        max_quantity = self._max_fillable_quantity(order, bar)
        if max_quantity <= Decimal(0):
            return None
        fill_quantity = min(order.quantity, max_quantity)

        adjusted_price = slippage_model.adjust(reference_price, order.side, rng)
        commission = commission_model.cost(fill_quantity, adjusted_price)

        return Fill(
            client_order_id=order.client_order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=fill_quantity,
            price=adjusted_price,
            commission=commission,
            filled_at=clock,
        )

    @staticmethod
    def _reference_price(order: SimulatedOrder, bar: Bar) -> Decimal | None:
        """Resolve the pre-slippage reference price for ``order`` against ``bar``.

        For a ``MARKET`` order, always returns ``bar.open`` — see the
        module docstring for the "decide at close, execute at next open"
        convention. For a ``LIMIT`` order, returns the limit price if the
        bar's range satisfies it (``BUY``: ``bar.low <= limit_price``;
        ``SELL``: ``bar.high >= limit_price``), otherwise returns ``None``
        to signal the limit is not yet satisfied.
        """
        if order.order_type is OrderType.MARKET:
            return bar.open

        # LIMIT
        limit_price = order.limit_price
        if limit_price is None:
            raise ValueError(f"LIMIT order {order.client_order_id!r} has no limit_price set")
        if order.side is OrderSide.BUY:
            return limit_price if bar.low <= limit_price else None
        return limit_price if bar.high >= limit_price else None

    @abstractmethod
    def _max_fillable_quantity(self, order: SimulatedOrder, bar: Bar) -> Decimal:
        """Return the maximum quantity of ``order`` fillable against ``bar``'s liquidity.

        Never negative. The template method ``fill()`` fills
        ``min(order.quantity, this value)``; a return of ``0`` (or less)
        means the order cannot fill against this bar at all.
        """


@dataclass(frozen=True, slots=True)
class ImmediateFillModel(FillModel):
    """Fills the full order quantity whenever it is fillable at all.

    The simplest fill model: it imposes no liquidity cap, so it never
    produces a partial fill. Useful as a baseline or for instruments/tests
    where liquidity constraints are not the concern under test.
    """

    def _max_fillable_quantity(self, order: SimulatedOrder, bar: Bar) -> Decimal:
        return order.quantity


@dataclass(frozen=True, slots=True)
class LiquidityCappedFillModel(FillModel):
    """Caps the filled quantity at a configurable fraction of the bar's volume.

    ``max_participation_rate`` (default ``Decimal("0.1")`` = 10% of the
    bar's volume) must be non-negative — a negative rate is not a
    meaningful liquidity cap. WHEN ``order.quantity`` exceeds
    ``bar.volume * max_participation_rate``, ``fill()`` returns a ``Fill``
    for only the capped quantity (Requirement 18.5); the caller is
    responsible for tracking the remaining unfilled quantity across
    subsequent bars.
    """

    max_participation_rate: Decimal = Decimal("0.1")

    def __post_init__(self) -> None:
        if self.max_participation_rate < Decimal(0):
            raise ValueError(
                "LiquidityCappedFillModel.max_participation_rate must be non-negative, "
                f"got {self.max_participation_rate!r}"
            )

    def _max_fillable_quantity(self, order: SimulatedOrder, bar: Bar) -> Decimal:
        return bar.volume * self.max_participation_rate
