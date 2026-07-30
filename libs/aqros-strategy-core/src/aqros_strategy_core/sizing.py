"""The shared position-sizing seam.

``PositionSizer`` is a ``typing.Protocol`` — a hook consumers implement to
turn a ``Strategy``'s ``OrderIntent`` into a concrete quantity, given the
same point-in-time-correct ``StrategyContext`` a ``Strategy`` sees. It is
intentionally minimal for now: a seam for confidence-aware sizing (e.g.
scaling size by a resolved model's confidence output) to be layered in later
without changing the ``Strategy`` or ``RiskCheck`` contracts.

Like ``Strategy``, implementations MUST derive every decision solely from
the supplied ``StrategyContext`` — never from wall-clock time or any
out-of-band data source — so sizing behaves identically across backtest,
paper, and live.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol, runtime_checkable

from aqros_strategy_core.contracts import OrderIntent
from aqros_strategy_core.strategy import StrategyContext


@runtime_checkable
class PositionSizer(Protocol):
    """The shared position-sizing contract, invoked unmodified across
    backtest, paper, and live execution.
    """

    def size(self, order_intent: OrderIntent, context: StrategyContext) -> Decimal:
        """Return the quantity to use for ``order_intent`` given ``context``.

        MUST NOT read wall-clock time or any data whose knowledge time
        exceeds ``context.as_of``. Implementations may return
        ``order_intent.quantity`` unchanged (a pass-through sizer) or
        compute a different quantity — for example scaled by a
        resolved-model confidence output present in
        ``context.model_outputs``.
        """
        ...
