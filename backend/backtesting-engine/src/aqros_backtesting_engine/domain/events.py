"""Event ordering utilities for the Backtesting Engine.

This module does **not** redefine ``Event``, ``EventKind``, or the
intra-instant priority mapping — those live in ``domain/models.py``
(``Event.ordering_key`` and the private ``_KIND_PRIORITY`` table), which
already establishes the total, documented tie-break rule
``(event_time, kind_priority, sequence)`` (Requirements 6.1, 6.2, 6.4,
31.4).

Its job is narrower: expose a thin, pure sorting utility layered on top of
that ordering key so every caller that needs to totally order a collection
of ``Event`` objects — the ``Historical_Replay`` building the
``Event_Stream``, the ``Simulation_Engine`` consuming it, and any test
asserting event-ordering totality — uses exactly the same, one, documented
comparator rather than each re-deriving its own sort key.

Pure, deterministic, no I/O, no wall-clock reads: sorting depends only on
each ``Event``'s own ``ordering_key`` (Requirement 31.3, 31.4; design.md
Section 9 "Event Ordering and Determinism").
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

from aqros_backtesting_engine.domain.models import Event

__all__ = ["event_ordering_key", "sort_events"]


def event_ordering_key(event: Event) -> tuple[datetime, int, int]:
    """Key function usable directly with ``sorted(events, key=event_ordering_key)``.

    Delegates to ``Event.ordering_key`` — the single, total, documented
    tie-break rule ``(event_time, kind_priority, sequence)`` defined on
    ``Event`` itself (``domain/models.py``) — so this module never
    maintains a second, potentially-diverging copy of the ordering logic.
    Exposed as a stable, importable key function so callers pass it
    directly as the ``key=`` argument to ``sorted``/``list.sort``.
    """
    return event.ordering_key


_KEY: Final = event_ordering_key  # local alias for use inside sort_events


def sort_events(events: list[Event]) -> list[Event]:
    """Return a new list of ``events`` sorted by ``Event.ordering_key``.

    Stable and deterministic: Python's ``sorted`` is a stable sort, and
    ``Event.ordering_key`` is itself a total order (event time, then the
    fixed intra-instant kind priority, then the stable ingest ``sequence``),
    so two calls given the same input list always produce the same output
    list, and events that compare equal under the key retain their relative
    input order (which — because ``sequence`` is already part of the key —
    only occurs for genuinely identical keys) (Requirements 6.1, 6.2, 6.4,
    31.3, 31.4).

    Does not mutate ``events``; returns a new list.
    """
    return sorted(events, key=_KEY)
