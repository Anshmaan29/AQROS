"""Historical replay for the Backtesting Engine.

``build_event_stream`` is the pure domain function behind design.md's
``Historical_Replay`` component: given already-fetched bars and corporate
actions (local, decoupled domain objects — never raw HTTP responses) for a
configured instrument universe and period, plus deterministic exchange
calendar data, it produces the totally-ordered ``Event_Stream`` the
``Simulation_Engine`` (``domain/simulation.py``, task 4.1) consumes.

This module is **pure**: no I/O, no ``async``, no port dependencies. The
adapters that fetch bars/corporate actions from the Market Data Service
(task 5.4, not yet implemented) call ``MarketDataClient`` and pass their
results into this module as plain ``Bar``/``CorporateAction`` lists —
``replay.py`` never imports ``domain/ports.py`` or reaches out to any
upstream service itself (Requirements 1.4, 1.5, 2.1, 45.1).

Design Decision 3 (bar knowledge-time convention)
--------------------------------------------------
Market Data bars carry no real knowledge time, so the engine assigns each
bar a knowledge time equal to its own exchange session close (a bar dated
``D`` is knowable only at or after ``D``'s close). ``Bar.knowledge_time`` is
a required field on the ``Bar`` dataclass (``domain/models.py``), and a
caller (for example a future adapter, or a test fixture) may already have
populated it with some placeholder value before handing bars to this
module. This module is nonetheless **authoritative** for deriving
``knowledge_time``: ``assign_bar_knowledge_time`` always recomputes it from
``calendar_data`` via ``domain.calendar.session_close`` and overwrites
whatever value the incoming ``Bar`` carried, rather than trusting or
merely defaulting to it. This keeps the "one true derivation" of the bar
knowledge-time convention in exactly one place (Decision 3; Requirements
3.5, 5.2, 5.3) regardless of what any upstream adapter happens to set.

Event ordering and sequence assignment
---------------------------------------
Each ``Bar``/``CorporateAction``/equity-sample day becomes an ``Event`` with
a stable, deterministic ``sequence`` assigned at construction time — never
from random or hash-based iteration. Construction order is fixed as:
corporate actions (symbols sorted, then each symbol's actions in their
natural ``event_time`` order), then market bars (symbols sorted, then each
symbol's bars in their natural ``event_time`` order), then equity-sample
markers (sample days in ascending order). The final ``sequence`` values are
therefore reproducible from the same inputs on every run. The true total
order of the returned stream — including intra-instant tie-breaks between
corporate actions, bars, and samples — is resolved by
``domain.events.sort_events``, which sorts by ``Event.ordering_key``
(``(event_time, kind_priority, sequence)``); the construction-time sequence
only need be deterministic, not itself express the final order (Requirement
6.1, 6.2, 31.4).

Advancing the clock only to the current event, never ahead (Requirement 3.3)
------------------------------------------------------------------------------
This module does not itself own or advance a ``Simulation_Clock`` — that is
the ``Simulation_Engine``'s responsibility (``domain/simulation.py``, task
4.1), which walks the ``Event_Stream`` this module returns and sets the
clock to each event's ``event_time`` in turn. ``build_event_stream``
satisfies its half of Requirement 3.3 structurally: the returned stream is
always sorted in non-decreasing ``event_time`` order (via
``sort_events``), so any consumer that walks it in order and advances the
clock to each event's own time, one event at a time, can never move the
clock ahead of the event currently being processed.

Instruments with no data (Requirement 3.4)
--------------------------------------------
``build_event_stream`` never fails the whole run merely because one
instrument in the universe has no bar data for the period. Instead, it
records a human-readable reason per such symbol in the returned
``no_data`` mapping and continues building the ``Event_Stream`` from
whatever data the other instruments do have.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date

from aqros_backtesting_engine.domain.calendar import session_close
from aqros_backtesting_engine.domain.events import sort_events
from aqros_backtesting_engine.domain.models import (
    Bar,
    CorporateAction,
    Event,
    EventKind,
    ExchangeCalendarData,
)

__all__ = [
    "EquitySampleMarker",
    "assign_bar_knowledge_time",
    "build_event_stream",
]


@dataclass(frozen=True, slots=True)
class EquitySampleMarker:
    """The ``Event.payload`` for an ``EventKind.EQUITY_SAMPLE`` event.

    Carries only the session ``day`` the sample corresponds to; the
    ``Simulation_Engine`` reacts to this event by sampling the current
    total ``Portfolio`` value into the ``Equity_Curve`` at that
    ``Event``'s ``event_time`` (design.md Section 7 "Simulation Loop").
    """

    day: date


def assign_bar_knowledge_time(bar: Bar, calendar_data: ExchangeCalendarData) -> Bar:
    """Return a copy of ``bar`` with ``knowledge_time`` re-derived as its session close.

    Authoritative derivation of Design Decision 3: a bar dated ``D`` (taken
    from ``bar.event_time``'s date) is knowable only at or after ``D``'s
    close on ``calendar_data``'s exchange. This always overwrites
    ``bar.knowledge_time`` with the freshly-computed session close — it
    never trusts or falls back to whatever value the incoming ``Bar``
    already carried (see module docstring), so this is the single source of
    truth for the bar knowledge-time convention (Requirements 3.5, 5.2,
    5.3).

    Raises:
        ValueError: if ``bar.event_time``'s date is not a trading session
            day on ``calendar_data`` (propagated from
            ``domain.calendar.session_close``) — this signals a data
            integrity mismatch between the supplied bars and the supplied
            calendar rather than something this module should silently
            paper over.
    """
    knowledge_time = session_close(calendar_data, bar.event_time.date())
    return replace(bar, knowledge_time=knowledge_time)


def _corporate_action_events(
    corporate_actions_by_symbol: Mapping[str, Sequence[CorporateAction]],
    next_sequence: list[int],
) -> list[Event]:
    """Build ``CORPORATE_ACTION`` events, symbols sorted then actions in event-time order."""
    events: list[Event] = []
    for symbol in sorted(corporate_actions_by_symbol):
        actions = sorted(corporate_actions_by_symbol[symbol], key=lambda a: a.event_time)
        for action in actions:
            events.append(
                Event(
                    event_time=action.event_time,
                    knowledge_time=action.knowledge_time,
                    kind=EventKind.CORPORATE_ACTION,
                    sequence=next_sequence[0],
                    payload=action,
                )
            )
            next_sequence[0] += 1
    return events


def _market_bar_events(
    bars_by_symbol: Mapping[str, Sequence[Bar]],
    calendar_data: ExchangeCalendarData,
    next_sequence: list[int],
) -> list[Event]:
    """Build ``MARKET_BAR`` events, symbols sorted then bars in event-time order.

    Each bar's ``knowledge_time`` is re-derived via
    ``assign_bar_knowledge_time`` before being wrapped in an ``Event``
    (Decision 3).
    """
    events: list[Event] = []
    for symbol in sorted(bars_by_symbol):
        bars = sorted(bars_by_symbol[symbol], key=lambda b: b.event_time)
        for bar in bars:
            dated_bar = assign_bar_knowledge_time(bar, calendar_data)
            events.append(
                Event(
                    event_time=dated_bar.event_time,
                    knowledge_time=dated_bar.knowledge_time,
                    kind=EventKind.MARKET_BAR,
                    sequence=next_sequence[0],
                    payload=dated_bar,
                )
            )
            next_sequence[0] += 1
    return events


def _equity_sample_events(
    equity_sample_days: Sequence[date],
    calendar_data: ExchangeCalendarData,
    next_sequence: list[int],
) -> list[Event]:
    """Build ``EQUITY_SAMPLE`` events, one per sample day in ascending order.

    A sample's ``event_time``/``knowledge_time`` is the session close of its
    day: the equity curve is sampled once all of that day's bars and
    corporate actions have been applied (``kind_priority`` also orders
    ``EQUITY_SAMPLE`` after ``MARKET_BAR`` at the same instant). The sample
    is not a fact learned from an external source, so its knowledge time is
    simply its own event time.
    """
    events: list[Event] = []
    for day in sorted(set(equity_sample_days)):
        sample_time = session_close(calendar_data, day)
        events.append(
            Event(
                event_time=sample_time,
                knowledge_time=sample_time,
                kind=EventKind.EQUITY_SAMPLE,
                sequence=next_sequence[0],
                payload=EquitySampleMarker(day=day),
            )
        )
        next_sequence[0] += 1
    return events


def build_event_stream(
    bars_by_symbol: Mapping[str, Sequence[Bar]],
    corporate_actions_by_symbol: Mapping[str, Sequence[CorporateAction]],
    calendar_data: ExchangeCalendarData,
    equity_sample_days: Sequence[date],
    universe: Sequence[str],
) -> tuple[list[Event], dict[str, str]]:
    """Build the totally-ordered ``Event_Stream`` for a configured universe and period.

    Pure and deterministic: given the same arguments, always returns the
    same ordered event list and the same ``no_data`` mapping. Performs no
    I/O — ``bars_by_symbol`` and ``corporate_actions_by_symbol`` must
    already be fetched (by a future adapter, task 5.4) and handed in as
    plain domain objects.

    Args:
        bars_by_symbol: already-fetched bars for each symbol in ``universe``
            that has any. A symbol may be entirely absent from this mapping,
            or present with an empty sequence — both are treated as "no
            data" for that symbol.
        corporate_actions_by_symbol: already-fetched corporate actions for
            each symbol that has any (may omit symbols with none, including
            symbols for which no corporate-actions feed exists at all —
            Decision 6; this module does not distinguish "no feed" from "no
            actions in period", since both mean "apply nothing" here).
        calendar_data: the deterministic, versioned exchange calendar used
            to derive each bar's knowledge time (Decision 3) and each
            equity-sample event's timestamp.
        equity_sample_days: the session days at which to sample the equity
            curve (for example every session day in the period for a daily
            ``equity_sample_interval``); duplicates are ignored.
        universe: the full configured instrument universe for the
            ``Backtest_Run``, used to determine which symbols have no bar
            data at all (Requirement 3.4).

    Returns:
        A tuple of:
            - the ``Event_Stream``: every corporate-action, market-bar, and
              equity-sample event, totally ordered via
              ``domain.events.sort_events`` (``(event_time, kind_priority,
              sequence)``).
            - ``no_data``: a mapping from symbol to a human-readable reason,
              for every symbol in ``universe`` that has no bars in
              ``bars_by_symbol`` (Requirement 3.4) — the caller is expected
              to record this in the run's diagnostics/manifest and continue
              the run for the remaining instruments rather than failing the
              whole run silently.
    """
    no_data: dict[str, str] = {}
    for symbol in universe:
        if not bars_by_symbol.get(symbol):
            no_data[symbol] = "no bar data available for the configured universe and period"

    next_sequence = [0]
    events: list[Event] = []
    events.extend(_corporate_action_events(corporate_actions_by_symbol, next_sequence))
    events.extend(_market_bar_events(bars_by_symbol, calendar_data, next_sequence))
    events.extend(_equity_sample_events(equity_sample_days, calendar_data, next_sequence))

    return sort_events(events), no_data
