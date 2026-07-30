"""Slippage domain models for the Backtesting Engine.

A ``SlippageModel`` (design.md Decision 10, Section 10 "Order Execution,
Latency, Slippage, Commission, and Fills") adjusts a ``Fill``'s execution
price away from the reference price to represent market impact and spread
cost. The adjustment is always **adverse to the trader's side**: a buy
fills at a price at or above the reference price, and a sell fills at a
price at or below it (Requirement 16.1). The MVP provides ``ZeroSlippage``
(no adjustment) and ``FixedBpsSlippage`` (a fixed-basis-points adjustment),
both selectable via the ``Backtest_Configuration``'s
``slippage_model``/``slippage_params`` (Requirement 16.2).

Every implementation is pure and deterministic: given the same
``reference_price``, ``side``, and the same state of the run's single
seeded ``random.Random`` instance, ``adjust`` always returns the same
result (Requirement 16.3). Any stochastic component a ``SlippageModel``
implementation might use is drawn **only** from the ``rng`` argument passed
in by the caller — never from this module's own unseeded random source,
from ``random`` module-level functions, or from wall-clock time — so
slippage participates in deterministic replay exactly like every other
stochastic component in the engine (Requirement 16.4; design.md Decision 2
"Determinism is engineered, not hoped for"). ``ZeroSlippage`` and
``FixedBpsSlippage`` are themselves both fully deterministic and never
consult ``rng``; the parameter is accepted only to satisfy the shared
``SlippageModel`` ABC signature, so future stochastic implementations can
be swapped in without changing any caller.

This module performs no I/O and reads no wall-clock time.
"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal

from aqros_backtesting_engine.domain.models import OrderSide

__all__ = [
    "FixedBpsSlippage",
    "SlippageModel",
    "ZeroSlippage",
]


class SlippageModel(ABC):
    """Port/ABC for the deterministic, adverse price adjustment applied to a fill.

    Selected and parameterized via the ``Backtest_Configuration``'s
    ``slippage_model``/``slippage_params`` and pinned into the
    ``Run_Manifest`` for reproducibility — recording the manifest entry is
    the caller's (``BacktestService``'s) responsibility, not this module's.
    """

    @abstractmethod
    def adjust(self, reference_price: Decimal, side: OrderSide, rng: random.Random) -> Decimal:
        """Return the slippage-adjusted execution price for a fill.

        ``reference_price`` is the point-in-time market price the fill
        would otherwise execute at. ``side`` determines the direction of
        the adverse adjustment: a ``BUY`` returns a price at or above
        ``reference_price`` (the trader pays more), and a ``SELL`` returns
        a price at or below it (the trader receives less) — representing
        market impact and spread cost (Requirement 16.1). ``rng`` is the
        ``Backtest_Run``'s single seeded random source; any stochastic
        component of the adjustment is drawn from it and from no other
        source (Requirement 16.4).
        """


@dataclass(frozen=True, slots=True)
class ZeroSlippage(SlippageModel):
    """No adjustment: the fill executes at exactly the reference price.

    Deterministic and never consults ``rng``.
    """

    def adjust(self, reference_price: Decimal, side: OrderSide, rng: random.Random) -> Decimal:
        return reference_price


@dataclass(frozen=True, slots=True)
class FixedBpsSlippage(SlippageModel):
    """A fixed basis-points adjustment applied adversely to the trader's side.

    ``basis_points`` must be non-negative — a negative value would move the
    price in the trader's favor, which is not slippage (Requirement 16.1).
    The adjustment is ``reference_price * basis_points / 10_000``, added to
    the reference price for a ``BUY`` and subtracted for a ``SELL``.
    Deterministic and never consults ``rng`` (Requirement 16.3).
    """

    basis_points: Decimal

    def __post_init__(self) -> None:
        if self.basis_points < Decimal(0):
            raise ValueError(
                f"FixedBpsSlippage.basis_points must be non-negative, got {self.basis_points!r}"
            )

    def adjust(self, reference_price: Decimal, side: OrderSide, rng: random.Random) -> Decimal:
        adjustment = reference_price * self.basis_points / Decimal(10_000)
        if side is OrderSide.BUY:
            return reference_price + adjustment
        return reference_price - adjustment
