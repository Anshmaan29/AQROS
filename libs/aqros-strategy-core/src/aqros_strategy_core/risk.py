"""The shared risk-check seam — the Risk Kernel's future integration point.

``RiskCheck`` is the seam a future Risk Kernel implements. Every consumer
(the Backtesting Engine today; paper and live execution in the future) MUST
route every ``OrderIntent`` through the same ``RiskCheck`` path before it may
be filled or routed to a venue — there is no bypass, disable, or relax path
anywhere in this library, and none may be added to a consumer without
violating CLAUDE.md §7.3 / §7.4.

``RiskCheck`` is a ``typing.Protocol`` for the same reason ``Strategy`` is:
consumers (a permissive pass-through check for early development, the real
Risk Kernel once it exists) satisfy it structurally without forced
inheritance, and the contract stays a pure, dependency-free seam.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from aqros_strategy_core.contracts import OrderIntent
from aqros_strategy_core.strategy import StrategyContext


@dataclass(frozen=True, slots=True)
class RiskDecision:
    """The outcome of a single ``RiskCheck.check`` call.

    ``approved`` is the sole authority on whether an ``OrderIntent`` may
    proceed to the fill/execution path. When ``approved`` is ``False``,
    ``reason`` SHOULD be populated with a human-readable explanation so
    callers can record it (e.g. in a trade log) rather than silently
    dropping the order.
    """

    approved: bool
    reason: str | None


@runtime_checkable
class RiskCheck(Protocol):
    """The shared risk-check contract — the non-bypassable seam the Risk
    Kernel implements.

    Every ``OrderIntent`` a ``Strategy`` emits MUST be routed through
    ``check`` before it may be filled (in a backtest) or routed to a venue
    (in paper/live); no consumer may provide a configuration, flag, or code
    path that skips, disables, or relaxes this check, and no consumer may
    modify, raise, or override a limit enforced by an implementation of this
    contract (CLAUDE.md §7.3, §7.4).
    """

    def check(self, order_intent: OrderIntent, context: StrategyContext) -> RiskDecision:
        """Return the ``RiskDecision`` for ``order_intent`` given ``context``.

        MUST NOT read wall-clock time or any data whose knowledge time
        exceeds ``context.as_of``. Implementations decide approval
        deterministically from ``order_intent`` and ``context`` alone.
        """
        ...
