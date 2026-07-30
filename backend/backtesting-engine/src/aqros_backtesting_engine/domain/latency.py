"""Execution-latency domain models for the Backtesting Engine.

A ``LatencyModel`` (design.md Decision 9, Section 10 "Order Execution,
Latency, Slippage, Commission, and Fills") determines the delay between a
``Simulated_Order``'s emission and the earliest ``Simulation_Clock`` time at
which the ``Fill_Model`` may execute it — its ``eligible_time``. The MVP
default is ``ZeroLatency`` (no delay); ``FixedLatency`` and
``ConfigurableLatency`` are also provided, selectable via the
``Backtest_Configuration``'s ``latency_model``/``latency_params``
(Requirements 15.1, 15.2).

Every implementation is pure and deterministic: given the same
``emitted_at`` and the same state of the run's single seeded
``random.Random`` instance, ``eligible_time`` always returns the same
result. Any stochastic delay is drawn **only** from the ``rng`` argument
passed in by the caller — never from this module's own unseeded random
source, from ``random`` module-level functions, or from wall-clock time —
so latency participates in deterministic replay exactly like every other
stochastic component in the engine (Requirement 15.4; design.md Decision 2
"Determinism is engineered, not hoped for"). ``eligible_time`` never
advances the clock backwards: it always returns a time at or after
``emitted_at``, so the ``Fill_Model``'s point-in-time correctness and
absence of look-ahead bias are preserved (Requirement 15.3).

This module performs no I/O and reads no wall-clock time.
"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta

__all__ = [
    "ConfigurableLatency",
    "FixedLatency",
    "LatencyModel",
    "ZeroLatency",
]


class LatencyModel(ABC):
    """Port/ABC for the delay between an order's emission and its fill eligibility.

    Selected and parameterized via the ``Backtest_Configuration``'s
    ``latency_model``/``latency_params`` and pinned into the ``Run_Manifest``
    for reproducibility (Requirement 15.5) — recording the manifest entry is
    the caller's (``BacktestService``'s) responsibility, not this module's.
    """

    @abstractmethod
    def eligible_time(self, emitted_at: datetime, rng: random.Random) -> datetime:
        """Return the earliest ``Simulation_Clock`` time the order may fill at.

        ``emitted_at`` is the timezone-aware instant the order was emitted.
        ``rng`` is the ``Backtest_Run``'s single seeded random source; any
        stochastic delay is drawn from it and from no other source
        (Requirement 15.4). The returned instant is always at or after
        ``emitted_at`` (Requirement 15.3): the ``Fill_Model`` evaluates the
        order only against market data whose event time is at or after this
        ``eligible_time``, preserving point-in-time correctness and never
        introducing look-ahead bias (Requirements 15.3, 5.2).
        """


@dataclass(frozen=True, slots=True)
class ZeroLatency(LatencyModel):
    """No delay: the order is fill-eligible at the instant it was emitted.

    The MVP default (Requirement 15.2). Deterministic and never consults
    ``rng``.
    """

    def eligible_time(self, emitted_at: datetime, rng: random.Random) -> datetime:
        return emitted_at


@dataclass(frozen=True, slots=True)
class FixedLatency(LatencyModel):
    """A constant delay added to every order's emission time.

    ``delay`` must be non-negative — a negative delay would make an order
    eligible before it was emitted, violating point-in-time correctness
    (Requirement 15.3). Deterministic and never consults ``rng``
    (Requirement 15.2).
    """

    delay: timedelta

    def __post_init__(self) -> None:
        if self.delay < timedelta(0):
            raise ValueError(f"FixedLatency.delay must be non-negative, got {self.delay!r}")

    def eligible_time(self, emitted_at: datetime, rng: random.Random) -> datetime:
        return emitted_at + self.delay


@dataclass(frozen=True, slots=True)
class ConfigurableLatency(LatencyModel):
    """A stochastic delay drawn uniformly from ``[min_delay, max_delay]``.

    ``min_delay`` and ``max_delay`` must both be non-negative, and
    ``min_delay`` must not exceed ``max_delay``, so the drawn delay can
    never be negative (Requirement 15.3). The delay is drawn from the
    ``rng`` argument passed to ``eligible_time`` on every call — the run's
    single seeded random source — and from no other source, so it
    participates in deterministic replay (Requirement 15.4). When
    ``min_delay == max_delay`` the draw is degenerate but still consumes
    ``rng`` state identically to any other draw, keeping replay bit-for-bit
    reproducible regardless of parameterization.
    """

    min_delay: timedelta
    max_delay: timedelta

    def __post_init__(self) -> None:
        if self.min_delay < timedelta(0):
            raise ValueError(
                f"ConfigurableLatency.min_delay must be non-negative, got {self.min_delay!r}"
            )
        if self.max_delay < self.min_delay:
            raise ValueError(
                "ConfigurableLatency.max_delay must be >= min_delay, got "
                f"min_delay={self.min_delay!r}, max_delay={self.max_delay!r}"
            )

    def eligible_time(self, emitted_at: datetime, rng: random.Random) -> datetime:
        span_seconds = (self.max_delay - self.min_delay).total_seconds()
        delay_seconds = self.min_delay.total_seconds() + rng.uniform(0.0, span_seconds)
        return emitted_at + timedelta(seconds=delay_seconds)
