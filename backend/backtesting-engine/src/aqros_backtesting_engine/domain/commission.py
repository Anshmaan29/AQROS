"""Commission domain models for the Backtesting Engine.

A ``CommissionModel`` (design.md Decision 10, Section "Pluggable,
deterministic fill / slippage / commission models") computes the
transaction cost (fees) charged against the ``Cash_Ledger`` for a ``Fill``.
The MVP provides ``ZeroCommission`` (no cost), ``PerShareCommission`` (a
fixed cost per share traded), and ``PctNotionalCommission`` (a fixed
percentage of the fill's notional value), all selectable via the
``Backtest_Configuration``'s ``commission_model``/``commission_params``
(Requirement 17.2).

Every implementation is pure and deterministic: given the same
``quantity`` and ``price``, ``cost`` always returns the same result
(Requirement 17.3). Unlike ``LatencyModel`` and ``SlippageModel``,
commission is deterministic given quantity and price alone and needs no
random source (Requirement 17.3) — no implementation here reads wall-clock
time or draws from any random source.

This module performs no I/O and reads no wall-clock time.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal

__all__ = [
    "CommissionModel",
    "PctNotionalCommission",
    "PerShareCommission",
    "ZeroCommission",
]


class CommissionModel(ABC):
    """Port/ABC for the deterministic transaction cost charged on a fill.

    Selected and parameterized via the ``Backtest_Configuration``'s
    ``commission_model``/``commission_params`` and pinned into the
    ``Run_Manifest`` for reproducibility — recording the manifest entry is
    the caller's (``BacktestService``'s) responsibility, not this module's.
    The computed cost is debited from the ``Cash_Ledger`` and recorded in
    the ``Trade_Log`` and all net ``Performance_Metrics`` by the caller
    (Requirements 17.1, 17.4).
    """

    @abstractmethod
    def cost(self, quantity: Decimal, price: Decimal) -> Decimal:
        """Return the commission charged for a fill of ``quantity`` shares at ``price``.

        ``quantity`` is the executed (non-negative) fill quantity and
        ``price`` is the fill's per-share execution price. The result is
        computed deterministically from ``quantity`` and ``price`` alone,
        so identical inputs and parameters always yield identical
        commissions (Requirement 17.3).
        """


@dataclass(frozen=True, slots=True)
class ZeroCommission(CommissionModel):
    """No transaction cost is charged.

    Deterministic and independent of ``quantity`` and ``price``.
    """

    def cost(self, quantity: Decimal, price: Decimal) -> Decimal:
        return Decimal(0)


@dataclass(frozen=True, slots=True)
class PerShareCommission(CommissionModel):
    """A fixed cost charged per share traded.

    ``per_share`` must be non-negative — a negative rate would credit
    rather than charge the trader, which is not a commission
    (Requirement 17.2). The cost is ``quantity * per_share``, deterministic
    given ``quantity`` (Requirement 17.3).
    """

    per_share: Decimal

    def __post_init__(self) -> None:
        if self.per_share < Decimal(0):
            raise ValueError(
                f"PerShareCommission.per_share must be non-negative, got {self.per_share!r}"
            )

    def cost(self, quantity: Decimal, price: Decimal) -> Decimal:
        return quantity * self.per_share


@dataclass(frozen=True, slots=True)
class PctNotionalCommission(CommissionModel):
    """A fixed percentage of the fill's notional value.

    ``pct`` must be non-negative and is expressed as a fraction (e.g.
    ``Decimal("0.001")`` for 0.1%), not as basis points or a percentage
    integer. The cost is ``quantity * price * pct``, deterministic given
    ``quantity`` and ``price`` (Requirement 17.3).
    """

    pct: Decimal

    def __post_init__(self) -> None:
        if self.pct < Decimal(0):
            raise ValueError(f"PctNotionalCommission.pct must be non-negative, got {self.pct!r}")

    def cost(self, quantity: Decimal, price: Decimal) -> Decimal:
        return quantity * price * self.pct
