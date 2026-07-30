"""Pure, deterministic performance/risk/drawdown/benchmark metrics.

This module computes everything design.md Section 14 assigns to
``domain/metrics.py``: :class:`~aqros_backtesting_engine.domain.models.PerformanceMetrics`
(total/annualized return, Sharpe, Sortino, win rate — net of costs),
:class:`~aqros_backtesting_engine.domain.models.RiskMetrics` (volatility, max
drawdown, value-at-risk, gross/net exposure),
:class:`~aqros_backtesting_engine.domain.models.DrawdownSummary` (the
running-peak-to-trough decline series' maximum magnitude and duration), and
:class:`~aqros_backtesting_engine.domain.models.BenchmarkComparison` (excess
return over a benchmark computed from Market Data only) (Requirements 23,
24, 27, 28).

Every function in this module is pure: no I/O, no wall-clock reads, and no
mutation of its inputs — it operates only on the already-assembled
``Equity_Curve`` and ``Trade_Log`` (and, for risk metrics, the current
``Position`` map and point-in-time prices), all of which the caller (the
future ``BacktestService`` / ``Simulation_Engine``) has already obtained
through point-in-time-correct, look-ahead-guarded means. This module itself
never contacts the Market Data Service, the Feature Store, or the Model
Registry, and never reads wall-clock time (Requirement 41.4).

**Departure from pure-``Decimal`` arithmetic (documented, per Requirement
21.3 and CLAUDE.md §5).** Requirement 21.3 requires ``Decimal`` (or
otherwise exactly-reproducible) arithmetic for *cash, notional, and
commission* bookkeeping — the money path — so that identical inputs yield
identical balances with no floating-point ordering nondeterminism. That
requirement does not extend to *statistical metric computation*: Python's
``statistics`` module and ``math`` functions (``stdev``, ``mean``,
``sqrt``, floating-point exponentiation for annualization) operate on
``float``, not ``Decimal``, and there is no practical way to compute a
standard deviation, a square root, or a fractional power using ``Decimal``
without reimplementing those primitives from scratch. This module therefore:

1. Converts the ``Decimal`` equity-curve values to ``float`` **only** for
   the specific statistical operations that require it (period-return
   standard deviation, downside deviation, square roots, and fractional
   exponentiation for annualization).
2. Converts every such ``float`` result back to ``Decimal`` before
   returning it, via :func:`_float_to_decimal`, which uses ``Decimal(str(value))``
   — the shortest round-trip decimal string Python's float ``repr``
   produces for that exact IEEE-754 bit pattern.
3. Never lets a ``float`` value leak into cash, notional, or commission
   arithmetic (those remain ``Decimal``-only throughout
   ``domain/portfolio.py``, ``domain/fills.py``, ``domain/commission.py``,
   and this module's own exposure/return calculations, which use ``Decimal``
   division wherever a plain ratio suffices).

This does **not** violate Requirement 31 (determinism): IEEE-754
floating-point arithmetic is itself fully deterministic given the same
inputs and the same sequence of operations — the same ``float`` operands
processed by the same operations always produce the same bit-for-bit
result, with no dependency on wall-clock time, thread scheduling, or
hash-seed randomization. Determinism only requires that identical inputs
produce identical outputs on every run; it does not require that every
intermediate representation be ``Decimal``. What Requirement 21.3
specifically calls out — "no floating-point ordering nondeterminism" — is
about summation-order-dependent rounding drift in *repeated additions of
cash amounts across a long run*, a concern that does not apply to a single,
one-shot statistical reduction computed once at the end of a run from a
already-fixed, already-ordered sequence of equity-curve values.

**Win-rate simplification (documented, per the task).** ``TradeLogEntry``
does not itself carry a realized P&L — that requires position-tracking
context (an evolving cost basis per symbol) that this pure module does not
otherwise hold. Rather than ignore per-trade P&L entirely (e.g. reporting
"fraction of FILLED outcomes"), :func:`compute_performance_metrics`
reconstructs a *local* per-symbol position timeline purely from the
``Trade_Log`` itself, by replaying every ``FILLED``/``PARTIALLY_FILLED``
entry (in ``sequence`` order) through
:func:`aqros_backtesting_engine.domain.portfolio.apply_fill` — the same
pure position-reconstruction function the ``Simulation_Engine`` itself
uses (Requirement 19.4: position state derives solely from the ordered
fill sequence). Whenever a replayed fill causes ``Position.realized_pnl``
to change (i.e. it closes or reduces an existing position), that
fill counts as one closed "trade"; the trade is a *win* if the realized
P&L recognized on that fill, net of its commission, is positive. Slippage
is already embedded in the recorded fill ``price`` (per
``domain.models.Fill``'s own docstring: "slippage-adjusted execution
price"), so no separate slippage adjustment is needed to be net of costs
(Requirement 23.1). ``win_rate`` is the fraction of such closing fills that
were wins; a run with no closing fills at all (e.g. a strategy that only
ever opens positions) reports a win rate of zero rather than an undefined
value, since ``PerformanceMetrics.win_rate`` is a non-optional ``Decimal``
field (only ``sharpe_ratio``/``sortino_ratio`` are optional per Requirement
23.4).

**Undefined metrics (Requirement 23.4).** ``sharpe_ratio`` and
``sortino_ratio`` are reported as ``None`` — explicitly undefined — rather
than a misleading value whenever there is insufficient data to compute
them (fewer than two usable period returns) or their denominator
(standard deviation, or downside deviation) is exactly zero. ``total_return``,
``annualized_return``, ``volatility``, ``max_drawdown``, ``value_at_risk``,
``gross_exposure``, and ``net_exposure`` are always well-defined (they
degrade to ``Decimal(0)`` for empty/degenerate inputs, documented at each
call site below) since their dataclass fields are non-optional.
"""

from __future__ import annotations

import itertools
import math
import statistics
from decimal import Decimal

from aqros_backtesting_engine.domain.models import (
    BenchmarkComparison,
    DrawdownSummary,
    EquityPoint,
    Fill,
    OrderStatus,
    PerformanceMetrics,
    Position,
    RiskMetrics,
    TradeLogEntry,
)
from aqros_backtesting_engine.domain.portfolio import apply_fill

__all__ = [
    "compute_benchmark_comparison",
    "compute_drawdown",
    "compute_performance_metrics",
    "compute_risk_metrics",
]

# Calendar days per year used for annualization, accounting for leap years
# (365.25 = average Gregorian-calendar year length) — the conventional
# constant used throughout finance for return annualization.
_DAYS_PER_YEAR = 365.25
_SECONDS_PER_DAY = 86400.0

# The historical-VaR confidence level: the reported value-at-risk is the
# loss magnitude at the 5th percentile of the period-return distribution,
# i.e. a loss of this size or worse occurred in (up to) 5% of observed
# periods (Requirement 24.1; documented method below).
_VAR_TAIL_PROBABILITY = 0.05


def _float_to_decimal(value: float) -> Decimal:
    """Convert a ``float`` statistic to ``Decimal`` via its shortest round-trip repr.

    ``Decimal(str(value))`` — see the module docstring's "Departure from
    pure-Decimal arithmetic" section for why this conversion is necessary
    and why it does not compromise determinism: ``str()`` on a Python
    ``float`` always produces the same digits for the same IEEE-754 bit
    pattern, so this conversion is itself deterministic.
    """
    return Decimal(str(value))


def _period_returns(equity_curve: list[EquityPoint]) -> list[float]:
    """Return the period-over-period fractional returns of ``equity_curve``.

    ``(current.total_value - previous.total_value) / previous.total_value``
    for each consecutive pair, assuming ``equity_curve`` is already ordered
    by ``clock_time`` (the ``Equity_Curve`` contract in ``domain/models.py``).
    A period whose starting value is exactly zero is skipped — a return
    relative to a zero base is undefined, and this is a genuinely
    degenerate input this module does not attempt to paper over.
    Converted to ``float`` here (see module docstring) since every
    consumer of this helper (Sharpe, Sortino, volatility, VaR) needs
    ``statistics``/``math`` operations.
    """
    returns: list[float] = []
    for previous, current in itertools.pairwise(equity_curve):
        if previous.total_value == 0:
            continue
        returns.append(float((current.total_value - previous.total_value) / previous.total_value))
    return returns


def _periods_per_year(equity_curve: list[EquityPoint]) -> float | None:
    """Return the inferred number of equity-curve sampling periods per year, or ``None``.

    Inferred from the *actual elapsed time* spanned by ``equity_curve``
    divided evenly across its points — ``(365.25 days) / average seconds
    between consecutive points`` — rather than assumed from a configured
    sampling interval, since this pure module is never given that
    configuration directly. Returns ``None`` when fewer than two points are
    available or the span is zero/negative (e.g. duplicate timestamps),
    since a periods-per-year rate cannot be inferred in either case; every
    caller treats ``None`` as "insufficient data to annualize."
    """
    if len(equity_curve) < 2:
        return None
    elapsed_seconds = (equity_curve[-1].clock_time - equity_curve[0].clock_time).total_seconds()
    if elapsed_seconds <= 0:
        return None
    average_period_seconds = elapsed_seconds / (len(equity_curve) - 1)
    return (_DAYS_PER_YEAR * _SECONDS_PER_DAY) / average_period_seconds


def _sharpe_ratio(period_returns: list[float], periods_per_year: float | None) -> Decimal | None:
    """Return the annualized Sharpe ratio, or ``None`` if explicitly undefined.

    ``mean(period_returns) / stdev(period_returns) * sqrt(periods_per_year)``.
    Undefined (``None``) when fewer than two period returns are available
    (``statistics.stdev`` requires at least two data points), when
    ``periods_per_year`` could not be inferred, or when the standard
    deviation is exactly zero — the canonical "Sharpe undefined when
    volatility is zero" case named by Requirement 23.4.
    """
    if periods_per_year is None or len(period_returns) < 2:
        return None
    stdev = statistics.stdev(period_returns)
    if stdev == 0:
        return None
    mean = statistics.mean(period_returns)
    value = (mean / stdev) * math.sqrt(periods_per_year)
    return _float_to_decimal(value)


def _sortino_ratio(period_returns: list[float], periods_per_year: float | None) -> Decimal | None:
    """Return the annualized Sortino ratio, or ``None`` if explicitly undefined.

    ``mean(period_returns) / downside_deviation * sqrt(periods_per_year)``,
    where ``downside_deviation`` is computed against a zero target using
    every period return (not only the negative ones) as the denominator:
    ``sqrt(sum(min(r, 0) ** 2 for r in period_returns) / len(period_returns))``.
    This is the standard target-downside-deviation formulation, chosen
    (documented) over "stdev of negative returns only" because it remains
    well-defined even when there is exactly one negative return (a sample
    standard deviation over a single value is undefined), while still
    reducing to zero — and therefore Sortino explicitly undefined — when
    there are no negative returns at all, exactly mirroring the Sharpe
    "undefined when volatility is zero" case (Requirement 23.4).
    """
    if periods_per_year is None or len(period_returns) < 2:
        return None
    downside_squared_sum = sum(min(r, 0.0) ** 2 for r in period_returns)
    downside_deviation = math.sqrt(downside_squared_sum / len(period_returns))
    if downside_deviation == 0:
        return None
    mean = statistics.mean(period_returns)
    value = (mean / downside_deviation) * math.sqrt(periods_per_year)
    return _float_to_decimal(value)


def _win_rate(trade_log: list[TradeLogEntry]) -> Decimal:
    """Return the fraction of closing fills (replayed from ``trade_log``) that were wins.

    See the module docstring's "Win-rate simplification" section for the
    full rationale. Returns ``Decimal(0)`` when there are no closing fills
    to evaluate (rather than ``None``, since ``PerformanceMetrics.win_rate``
    is non-optional).
    """
    positions: dict[str, Position] = {}
    wins = 0
    closes = 0
    for entry in sorted(trade_log, key=lambda e: e.sequence):
        if entry.outcome not in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED):
            continue
        if entry.price is None:
            continue
        fill = Fill(
            client_order_id=entry.client_order_id,
            symbol=entry.symbol,
            side=entry.side,
            quantity=entry.quantity,
            price=entry.price,
            commission=entry.commission,
            filled_at=entry.clock_time,
        )
        prior_position = positions.get(entry.symbol)
        prior_realized_pnl = (
            prior_position.realized_pnl if prior_position is not None else Decimal(0)
        )
        new_position = apply_fill(prior_position, fill)
        positions[entry.symbol] = new_position

        realized_delta = new_position.realized_pnl - prior_realized_pnl
        if realized_delta == 0:
            continue  # this fill opened or added to a position; nothing closed
        closes += 1
        net_of_commission = realized_delta - entry.commission
        if net_of_commission > 0:
            wins += 1

    if closes == 0:
        return Decimal(0)
    return Decimal(wins) / Decimal(closes)


def compute_drawdown(equity_curve: list[EquityPoint]) -> DrawdownSummary:
    """Compute the running-peak-to-trough drawdown summary for ``equity_curve``.

    Walks ``equity_curve`` once, tracking the running peak value and time
    seen so far; at each point, the decline from that running peak
    (``(peak - value) / peak``, or skipped where ``peak == 0``, since a
    fractional decline from a zero peak is undefined) is compared against
    the largest decline seen so far. ``max_drawdown_duration`` is the
    peak-to-trough duration (``max_drawdown_trough - max_drawdown_start``)
    of the single largest decline — this pure function does not attempt to
    detect a subsequent *recovery* back to the pre-drawdown peak, since
    ``EquityPoint``/``DrawdownSummary`` carry no such "recovered" concept
    and the requirement (27.2) asks only for "the duration of the maximum
    Drawdown," which peak-to-trough duration answers directly and
    unambiguously (Requirement 27.1, 27.2).

    Args:
        equity_curve: the ``Equity_Curve`` to analyze, ordered by
            ``clock_time`` (Requirement 26.1).

    Returns:
        A ``DrawdownSummary`` with ``max_drawdown`` expressed as a
        non-negative fractional decline (e.g. ``Decimal("0.25")`` for a 25%
        peak-to-trough decline). A single-point curve has no possible
        decline and reports ``max_drawdown=Decimal(0)`` with both
        ``max_drawdown_start`` and ``max_drawdown_trough`` set to that
        point's ``clock_time`` and a zero duration — "handled sensibly"
        per the task, rather than an arbitrary/undefined timestamp.

    Raises:
        ValueError: if ``equity_curve`` is empty — there is no timestamp
            available at all to report, so fabricating one would be worse
            than failing loudly.
    """
    if not equity_curve:
        raise ValueError("cannot compute a drawdown summary from an empty equity curve")

    peak_value = equity_curve[0].total_value
    peak_time = equity_curve[0].clock_time
    max_drawdown = Decimal(0)
    max_drawdown_start = peak_time
    max_drawdown_trough = peak_time

    for point in equity_curve:
        if point.total_value > peak_value:
            peak_value = point.total_value
            peak_time = point.clock_time
        if peak_value == 0:
            continue  # decline from a zero peak is undefined; skip this point
        decline = (peak_value - point.total_value) / peak_value
        if decline > max_drawdown:
            max_drawdown = decline
            max_drawdown_start = peak_time
            max_drawdown_trough = point.clock_time

    return DrawdownSummary(
        max_drawdown=max_drawdown,
        max_drawdown_start=max_drawdown_start,
        max_drawdown_trough=max_drawdown_trough,
        max_drawdown_duration=max_drawdown_trough - max_drawdown_start,
    )


def compute_performance_metrics(
    equity_curve: list[EquityPoint],
    trade_log: list[TradeLogEntry],
) -> PerformanceMetrics:
    """Compute total/annualized return, Sharpe, Sortino, and win rate — net of costs.

    Args:
        equity_curve: the ``Equity_Curve`` of the ``Backtest_Run``, ordered
            by ``clock_time``. ``total_return`` and ``annualized_return``
            are computed from its first and last points; Sharpe, Sortino,
            and volatility (in ``compute_risk_metrics``) are computed from
            its period-over-period returns.
        trade_log: the ``Trade_Log`` of the same run, used only to compute
            ``win_rate`` (see the module docstring's "Win-rate
            simplification").

    Returns:
        A ``PerformanceMetrics`` with:

        - ``total_return``: ``(final_value - initial_value) / initial_value``,
          or ``Decimal(0)`` if ``equity_curve`` has fewer than two points or
          its initial value is exactly zero (both degenerate; a
          non-optional field cannot report "undefined").
        - ``annualized_return``: ``(1 + total_return) ** (365.25 / elapsed_days) - 1``,
          using the *actual* elapsed wall-span of ``equity_curve`` (never a
          configured/assumed period count) for ``elapsed_days``, computed
          via ``float`` exponentiation and converted back to ``Decimal``
          (see the module docstring). Reports ``total_return`` itself
          (i.e. un-annualized) when the elapsed span is zero/negative
          (fewer than two points, or duplicate timestamps), and reports
          ``Decimal("-1")`` — total capital loss — when
          ``1 + total_return <= 0``, since a fractional power of a
          non-positive base is not a real number and "worse than -100%"
          has no meaningful further annualization.
        - ``sharpe_ratio`` / ``sortino_ratio``: ``None`` when explicitly
          undefined (Requirement 23.4); see ``_sharpe_ratio``/``_sortino_ratio``.
        - ``win_rate``: see ``_win_rate``.
    """
    if len(equity_curve) < 2:
        return PerformanceMetrics(
            total_return=Decimal(0),
            annualized_return=Decimal(0),
            sharpe_ratio=None,
            sortino_ratio=None,
            win_rate=_win_rate(trade_log),
        )

    initial_value = equity_curve[0].total_value
    final_value = equity_curve[-1].total_value
    if initial_value == 0:
        total_return = Decimal(0)
    else:
        total_return = (final_value - initial_value) / initial_value

    elapsed_days = (
        equity_curve[-1].clock_time - equity_curve[0].clock_time
    ).total_seconds() / _SECONDS_PER_DAY
    if elapsed_days <= 0:
        annualized_return = total_return
    else:
        base = float(1 + total_return)
        if base <= 0:
            annualized_return = Decimal("-1")
        else:
            annualized_value = base ** (_DAYS_PER_YEAR / elapsed_days) - 1.0
            annualized_return = _float_to_decimal(annualized_value)

    period_returns = _period_returns(equity_curve)
    periods_per_year = _periods_per_year(equity_curve)

    return PerformanceMetrics(
        total_return=total_return,
        annualized_return=annualized_return,
        sharpe_ratio=_sharpe_ratio(period_returns, periods_per_year),
        sortino_ratio=_sortino_ratio(period_returns, periods_per_year),
        win_rate=_win_rate(trade_log),
    )


def compute_risk_metrics(
    equity_curve: list[EquityPoint],
    positions: dict[str, Position],
    current_prices: dict[str, Decimal],
) -> RiskMetrics:
    """Compute return volatility, max drawdown, value-at-risk, and gross/net exposure.

    Args:
        equity_curve: the ``Equity_Curve`` of the ``Backtest_Run``, used for
            ``volatility``, ``max_drawdown``, and ``value_at_risk``.
        positions: the currently-held ``Position`` map (keyed by symbol),
            used for ``gross_exposure``/``net_exposure``. Traversed in
            sorted-symbol order for a fixed, documented total order
            (Requirement 31.4).
        current_prices: the point-in-time price for every symbol held in
            ``positions``.

    Returns:
        A ``RiskMetrics`` with:

        - ``volatility``: the sample standard deviation of
          ``equity_curve``'s period-over-period returns, or ``Decimal(0)``
          when fewer than two period returns are available (a sample
          standard deviation over fewer than two points is undefined, and
          this field is non-optional).
        - ``max_drawdown``: delegates to :func:`compute_drawdown`
          (``Decimal(0)`` when ``equity_curve`` is empty, rather than
          raising, since risk metrics must always report a value).
        - ``value_at_risk``: a simple **historical VaR** at the 95%
          confidence level (documented method, Requirement 24.1): the
          period returns are sorted ascending and the value at the 5th
          percentile index (``floor(0.05 * n)``, clamped to the last index)
          is taken as the tail return; ``value_at_risk`` is that return's
          magnitude if it represents a loss (``max(-tail_return, 0)``), or
          ``Decimal(0)`` if even the 5th-percentile period was not a loss,
          or if there are no period returns at all.
        - ``gross_exposure`` / ``net_exposure``: ``sum(abs(quantity * price))``
          and the signed ``sum(quantity * price)`` respectively, over every
          held position, using ``Decimal`` arithmetic throughout (this is
          notional/cash-adjacent, not a statistical reduction, so it stays
          ``Decimal``-only per Requirement 21.3).

    Raises:
        ValueError: if ``positions`` holds a symbol with no corresponding
            entry in ``current_prices`` — a missing price must never be
            silently treated as zero (matches ``domain/portfolio.py``'s
            ``portfolio_value``/``maintenance_margin_requirement``
            convention).
    """
    period_returns = _period_returns(equity_curve)

    if len(period_returns) < 2:
        volatility = Decimal(0)
    else:
        volatility = _float_to_decimal(statistics.stdev(period_returns))

    max_drawdown = compute_drawdown(equity_curve).max_drawdown if equity_curve else Decimal(0)

    if not period_returns:
        value_at_risk = Decimal(0)
    else:
        sorted_returns = sorted(period_returns)
        tail_index = min(int(_VAR_TAIL_PROBABILITY * len(sorted_returns)), len(sorted_returns) - 1)
        tail_return = sorted_returns[tail_index]
        value_at_risk = _float_to_decimal(max(-tail_return, 0.0))

    gross_exposure = Decimal(0)
    net_exposure = Decimal(0)
    for symbol in sorted(positions):
        if symbol not in current_prices:
            raise ValueError(f"no current price supplied for held position {symbol!r}")
        notional = positions[symbol].quantity * current_prices[symbol]
        gross_exposure += abs(notional)
        net_exposure += notional

    return RiskMetrics(
        volatility=volatility,
        max_drawdown=max_drawdown,
        value_at_risk=value_at_risk,
        gross_exposure=gross_exposure,
        net_exposure=net_exposure,
    )


def compute_benchmark_comparison(
    benchmark_symbol: str,
    benchmark_equity_curve: list[EquityPoint],
    strategy_total_return: Decimal,
) -> BenchmarkComparison:
    """Compute the benchmark's total return and excess return over the strategy's.

    ``benchmark_return`` is computed identically to a strategy's
    ``total_return`` in :func:`compute_performance_metrics`:
    ``(final_value - initial_value) / initial_value`` over
    ``benchmark_equity_curve`` (which the caller builds from the
    benchmark's own price series obtained from the Market Data Service
    only — Requirement 28.1), or ``Decimal(0)`` if it has fewer than two
    points or its initial value is exactly zero.

    Args:
        benchmark_symbol: the configured benchmark instrument's symbol.
        benchmark_equity_curve: a benchmark price/value series shaped
            exactly like an ``Equity_Curve`` (e.g. a buy-and-hold notional
            trajectory), ordered by ``clock_time``, covering the same
            historical period as the strategy's own ``Equity_Curve``
            (Requirement 28.1).
        strategy_total_return: the strategy's own ``total_return`` (from
            ``PerformanceMetrics.total_return``) for the same period.

    Returns:
        A ``BenchmarkComparison`` with ``excess_return =
        strategy_total_return - benchmark_return`` (Requirement 28.2).
    """
    if len(benchmark_equity_curve) < 2:
        benchmark_return = Decimal(0)
    else:
        initial_value = benchmark_equity_curve[0].total_value
        final_value = benchmark_equity_curve[-1].total_value
        benchmark_return = (
            Decimal(0) if initial_value == 0 else (final_value - initial_value) / initial_value
        )

    return BenchmarkComparison(
        benchmark_symbol=benchmark_symbol,
        benchmark_return=benchmark_return,
        excess_return=strategy_total_return - benchmark_return,
    )
