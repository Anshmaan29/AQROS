"""The shared ``Strategy`` contract and its point-in-time-correct context.

``Strategy`` is a ``typing.Protocol`` rather than an ABC: any object — a
backtest strategy implementation, a paper-trading strategy, a live-trading
strategy — that provides a matching ``on_event`` method satisfies it
structurally, with no forced inheritance. This is the exact contract shared
unmodified across backtest, paper, and live execution (CLAUDE.md §7.1); no
consumer redefines or forks it.

``StrategyContext`` is the *only* window a ``Strategy`` has onto the outside
world. It is deliberately narrow:

* It exposes only point-in-time-correct market data, features, and
  resolved-model outputs — i.e. only facts whose knowledge time is at or
  before the current decision time.
* It **never** exposes wall-clock time. A ``Strategy`` must derive every
  notion of "now" from ``StrategyContext.as_of``, which the caller (the
  Backtesting Engine's ``Simulation_Engine`` today; a paper/live execution
  loop in the future) sets from its own injected clock — never from
  ``datetime.now()``.
* It **never** exposes future or not-yet-knowable data. The caller is
  responsible for populating ``market_data``, ``features``, and
  ``model_outputs`` with only values whose knowledge time is at or before
  ``as_of`` (the Backtesting Engine enforces this with its look-ahead
  guard); this library does not and cannot verify that on the caller's
  behalf, since it performs no I/O and holds no notion of knowledge time
  itself.

Consumers are free to pass a richer object that duck-types this Protocol
(for example carrying additional read-only accessors) — ``Strategy`` only
requires that ``on_event`` be present and behave as documented.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from aqros_strategy_core.contracts import OrderIntent


@dataclass(frozen=True, slots=True)
class StrategyContext:
    """Point-in-time-correct inputs available to a ``Strategy`` at a decision.

    ``as_of`` is the sole notion of "now" a ``Strategy`` may use; it is
    derived from the caller's injected clock, never wall-clock time.
    ``market_data``, ``features``, and ``model_outputs`` are plain,
    read-only mappings supplied by the caller and MUST contain only values
    whose knowledge time is at or before ``as_of`` — this dataclass carries
    no wall-clock accessor and no mechanism to request future data.
    """

    as_of: datetime
    market_data: dict[str, object] = field(default_factory=dict)
    features: dict[str, object] = field(default_factory=dict)
    model_outputs: dict[str, object] = field(default_factory=dict)


@runtime_checkable
class Strategy(Protocol):
    """The shared decision-policy contract, invoked unmodified across
    backtest, paper, and live execution.

    Implementations MUST derive every decision solely from the supplied
    ``StrategyContext`` — never from wall-clock time, never from any data
    source not passed through the context — so that the same strategy code
    produces the same decisions regardless of which execution mode invokes
    it.
    """

    def on_event(self, context: StrategyContext) -> list[OrderIntent]:
        """Return the list of ``OrderIntent`` this strategy wants to emit.

        Called once per point-in-time decision opportunity (e.g. once per
        bar becoming knowable in a backtest). MUST NOT read wall-clock time
        or any data whose knowledge time exceeds ``context.as_of``. Return
        an empty list to emit no orders for this event.
        """
        ...
