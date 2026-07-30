"""Position, cash, portfolio valuation, and margin/leverage primitives.

``Position``, ``CashLedger``, and ``Portfolio`` are declared as pure data
containers in ``domain/models.py`` (Requirements 19, 20, 21). This module
adds the pure, deterministic *behavior* that operates on them — updating a
``Position`` and a ``CashLedger`` from a ``Fill``, valuing a portfolio at a
point in time, and computing margin/leverage/buying-power/maintenance-margin
primitives — mirroring how ``domain/corporate_actions.py`` adds pure
functions over the same ``Position``/``CashLedger`` dataclasses, and how
``domain/fills.py`` adds the ``FillModel`` behavior. Every function here is
pure: no I/O, no wall-clock reads, ``Decimal`` arithmetic only (Requirement
21.3, 31.2).

**Position reconstruction (Requirement 19).** ``apply_fill`` derives a
``Position``'s signed ``quantity`` and ``average_cost`` solely from an
ordered sequence of ``Fill``s (Requirement 19.4): starting from ``None``
(flat: quantity 0, average_cost 0, realized_pnl 0), each ``BUY`` fill adds a
positive signed delta and each ``SELL`` fill adds a negative signed delta to
``quantity``, so a short position is represented as negative ``quantity``
(Requirement 19.3). Three cases arise:

1. **Opening or adding** (the position is flat, or the fill's signed delta
   has the same sign as the existing ``quantity``): ``average_cost``
   becomes the fill price (when opening from flat) or the quantity-weighted
   average of the existing cost and the fill price (when adding); no
   profit or loss is realized.
2. **Reducing, without crossing zero** (the fill's signed delta has the
   opposite sign of the existing ``quantity``, and does not fully offset
   it): the closed portion (``min(|existing quantity|, |delta|)``) realizes
   profit or loss into ``realized_pnl``; ``average_cost`` of the remaining
   shares is unchanged, since reducing a position never changes the cost
   basis of what remains.
3. **Closing exactly, or crossing through zero**: the entire existing
   position closes (realizing P&L on all of it), and — if the fill's
   quantity exceeds what was needed to close it — the leftover opens a
   *new* position in the opposite direction at the fill's price. This is
   handled by the same arithmetic as case 2 with ``new_quantity`` simply
   ``existing_quantity + delta``; when ``new_quantity`` lands on the same
   side as the delta (i.e. the opposite side from the pre-fill position),
   the position has crossed through zero and its cost basis resets to the
   fill price, since none of the pre-fill position's cost basis is
   meaningful to whatever was freshly opened.

Realized P&L on a close/reduce is computed as
``closing_quantity * direction * (fill.price - average_cost)`` where
``direction`` is ``+1`` for a position that was long and ``-1`` for a
position that was short before the fill — this is sign-correct for both:
a long realizes a gain when it sells above its cost basis
(``direction=+1``, so a higher fill price increases realized P&L); a short
realizes a gain when it buys back (covers) below its cost basis
(``direction=-1``, so a *lower* fill price increases realized P&L).

**Unrealized P&L (Requirement 19.2).** ``unrealized_pnl`` uses
``quantity * (current_price - average_cost)``. Because ``quantity`` is
signed, this single formula is correct for both sides without a branch: for
a long (``quantity > 0``), a ``current_price`` above ``average_cost`` yields
a positive result (a gain); for a short (``quantity < 0``), the same
``current_price`` above ``average_cost`` makes ``(current_price -
average_cost)`` positive but ``quantity`` negative, yielding a *negative*
result (a loss for the short) — exactly the economically correct sign in
both cases.

**Cash (Requirement 21).** ``apply_fill_to_cash`` debits the ``Cash_Ledger``
by the fill notional (``quantity * price``) plus commission for a ``BUY``,
and credits it by the notional minus commission for a ``SELL`` (Requirement
21.2); all arithmetic is ``Decimal`` (Requirement 21.3).

**Portfolio valuation (Requirement 20.2).** ``portfolio_value`` is
``cash.balance`` plus the point-in-time market value
(``quantity * current_price``) of every held position. ``build_portfolio``
assembles a ``Portfolio`` with positions ordered by symbol (a sorted tuple)
so every traversal of the position map is over a fixed, documented total
order (Requirement 31.4), matching the ``positions`` field's own
"ordered by symbol for determinism" contract in ``domain/models.py``.

**Margin and leverage (Requirement 22).** For the MVP default
(``leverage_enabled=False``), ``buying_power`` returns exactly
``cash.balance`` (Requirement 22.2: buying power is constrained to
available cash, and no ``Fill`` may exceed it). When leverage is enabled,
this module adopts the following documented formula:
``buying_power = (cash.balance + market value of positions) * max_leverage``
— i.e. buying power scales with total portfolio *equity* (cash plus the
signed market value of every position), not with cash alone. This is the
standard "equity times a leverage multiplier" definition (e.g.
``max_leverage = Decimal("2")`` means buying power is twice total equity),
consistent with typical margin-account conventions; it was chosen over
"cash times leverage" because equity — not cash alone — is what backs a
leveraged account's exposure. ``maintenance_margin_requirement`` sums
``abs(quantity * current_price) * maintenance_margin_rate`` across every
held position (absolute notional exposure is what a maintenance-margin
call is against, regardless of long or short direction).
``is_maintenance_breach`` reports whether current portfolio equity has
fallen below that requirement — the deterministic trigger condition for a
forced liquidation (Requirement 22.4). ``would_exceed_buying_power`` is the
companion primitive for Requirement 21.4/22.2: given a prospective ``Fill``,
it reports whether applying it would spend more than the account's current
buying power, so the caller can block the fill and record the constraint
that blocked it in the ``Trade_Log``. Only ``BUY`` fills are checked — a
``SELL`` fill (including one that opens or adds to a short position) never
*spends* buying power in this model; the risk of an unbounded short is
instead bounded by the maintenance-margin machinery above, which the
caller can check after any fill to decide whether a forced liquidation is
warranted.

**Scope boundary (documented per the task).** This module provides only
the *pure detection and calculation primitives* for margin, leverage,
buying power, maintenance margin, and forced-liquidation triggering. It
deliberately does **not** generate the liquidation orders/fills themselves
— sequencing which positions to liquidate, in what order, and by how much
to clear a maintenance breach is a stateful, event-loop-driven decision
that belongs to the ``Simulation_Engine`` (task 4.1), which already owns
the loop that applies fills, updates cash, and advances the clock. Mixing
that orchestration into this module would duplicate the event-ordering and
fill-application responsibilities that ``domain/simulation.py`` owns.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal

from aqros_backtesting_engine.domain.models import (
    CashLedger,
    Fill,
    OrderSide,
    Portfolio,
    Position,
)

__all__ = [
    "apply_fill",
    "apply_fill_to_cash",
    "build_portfolio",
    "buying_power",
    "is_maintenance_breach",
    "maintenance_margin_requirement",
    "portfolio_value",
    "unrealized_pnl",
    "would_exceed_buying_power",
]


def _same_sign(a: Decimal, b: Decimal) -> bool:
    """Return whether ``a`` and ``b`` are both strictly positive or both strictly negative."""
    return (a > 0 and b > 0) or (a < 0 and b < 0)


def apply_fill(position: Position | None, fill: Fill) -> Position:
    """Update a ``Position``'s signed quantity, average cost, and realized P&L from a ``Fill``.

    ``position`` may be ``None``, in which case the fill is applied as
    though starting from flat (``quantity=0``, ``average_cost=0``,
    ``realized_pnl=0``) — see the module docstring for the three cases
    (opening/adding, reducing, closing/crossing zero) and the realized-P&L
    formula. Pure and deterministic: given the same ``position`` and
    ``fill``, always returns the same result.

    Args:
        position: the current ``Position`` in ``fill.symbol``, or ``None``
            if no position is currently held.
        fill: the ``Fill`` to apply. ``fill.quantity`` is the unsigned
            executed quantity; ``fill.side`` determines its sign
            (``BUY`` => positive delta, ``SELL`` => negative delta).

    Returns:
        The new ``Position`` reflecting the fill. Always returns a
        ``Position`` (never ``None``), even when the fill flattens the
        position exactly to zero — a flat position is represented as
        ``quantity=0``, not the absence of a ``Position``.
    """
    current_quantity = position.quantity if position is not None else Decimal(0)
    current_average_cost = position.average_cost if position is not None else Decimal(0)
    current_realized_pnl = position.realized_pnl if position is not None else Decimal(0)

    delta = fill.quantity if fill.side is OrderSide.BUY else -fill.quantity

    if current_quantity == 0 or _same_sign(current_quantity, delta):
        # Opening from flat, or adding to a position in the same direction:
        # no P&L realized; average_cost becomes (or is blended toward) the
        # fill price.
        new_quantity = current_quantity + delta
        if current_quantity == 0:
            new_average_cost = fill.price
        else:
            new_average_cost = (
                abs(current_quantity) * current_average_cost + fill.quantity * fill.price
            ) / abs(new_quantity)
        return Position(
            symbol=fill.symbol,
            quantity=new_quantity,
            average_cost=new_average_cost,
            realized_pnl=current_realized_pnl,
        )

    # Reducing, closing exactly, or crossing through zero: `delta` has the
    # opposite sign of `current_quantity`.
    closing_quantity = min(abs(current_quantity), abs(delta))
    direction = Decimal(1) if current_quantity > 0 else Decimal(-1)
    realized_delta = closing_quantity * direction * (fill.price - current_average_cost)
    new_quantity = current_quantity + delta
    new_realized_pnl = current_realized_pnl + realized_delta

    if new_quantity == 0:
        new_average_cost = Decimal(0)
    elif _same_sign(new_quantity, delta):
        # Crossed through zero: the leftover opens a fresh position in the
        # opposite direction, at this fill's price — the pre-fill cost
        # basis has nothing to do with what was just opened.
        new_average_cost = fill.price
    else:
        # Merely reduced, still on the same side as before the fill: the
        # cost basis of the remaining shares is unchanged.
        new_average_cost = current_average_cost

    return Position(
        symbol=fill.symbol,
        quantity=new_quantity,
        average_cost=new_average_cost,
        realized_pnl=new_realized_pnl,
    )


def apply_fill_to_cash(cash: CashLedger, fill: Fill) -> CashLedger:
    """Debit or credit ``cash`` by a ``Fill``'s notional and debit its commission.

    A ``BUY`` fill debits ``quantity * price`` plus the commission; a
    ``SELL`` fill credits ``quantity * price`` minus the commission
    (Requirement 21.2). ``Decimal`` arithmetic only (Requirement 21.3).
    Pure: returns a new ``CashLedger`` rather than mutating ``cash``
    (``CashLedger`` is frozen).
    """
    notional = fill.quantity * fill.price
    if fill.side is OrderSide.BUY:
        new_balance = cash.balance - notional - fill.commission
    else:
        new_balance = cash.balance + notional - fill.commission
    return replace(cash, balance=new_balance)


def unrealized_pnl(position: Position, current_price: Decimal) -> Decimal:
    """Return a ``Position``'s unrealized P&L at ``current_price``.

    ``quantity * (current_price - average_cost)`` — sign-correct for both
    long (``quantity > 0``) and short (``quantity < 0``) positions without
    branching; see the module docstring for why this single formula holds
    for both sides (Requirement 19.2).
    """
    return position.quantity * (current_price - position.average_cost)


def portfolio_value(
    cash: CashLedger,
    positions: dict[str, Position],
    current_prices: dict[str, Decimal],
) -> Decimal:
    """Return the total point-in-time portfolio value: cash plus position market value.

    ``cash.balance`` plus ``quantity * current_price`` summed over every
    entry in ``positions``, traversed in sorted-symbol order for a fixed,
    documented total order (Requirement 31.4) — though ``Decimal``
    addition is exact, so the summation order does not itself affect the
    result (Requirement 20.2).

    Args:
        cash: the current ``Cash_Ledger``.
        positions: every currently-held ``Position``, keyed by symbol.
        current_prices: the point-in-time price for every symbol held in
            ``positions``.

    Raises:
        ValueError: if ``positions`` holds a symbol with no corresponding
            entry in ``current_prices`` — a missing price must never be
            silently treated as zero.
    """
    total = cash.balance
    for symbol in sorted(positions):
        if symbol not in current_prices:
            raise ValueError(f"no current price supplied for held position {symbol!r}")
        total += positions[symbol].quantity * current_prices[symbol]
    return total


def build_portfolio(
    cash: CashLedger,
    positions: dict[str, Position],
    as_of: datetime,
) -> Portfolio:
    """Assemble a ``Portfolio`` snapshot with positions ordered by symbol.

    Positions are emitted as a tuple sorted by symbol, matching
    ``domain.models.Portfolio.positions``'s documented "ordered by symbol
    for determinism" contract (Requirement 20.1, 31.4).
    """
    ordered_positions = tuple(positions[symbol] for symbol in sorted(positions))
    return Portfolio(cash=cash, positions=ordered_positions, as_of=as_of)


def buying_power(
    cash: CashLedger,
    positions: dict[str, Position],
    current_prices: dict[str, Decimal],
    leverage_enabled: bool,
    max_leverage: Decimal,
) -> Decimal:
    """Return the account's current buying power.

    WHERE leverage is disabled (the MVP default), buying power is exactly
    ``cash.balance`` (Requirement 22.2). WHERE leverage is enabled, buying
    power is ``portfolio_value(...) * max_leverage`` — total portfolio
    equity (cash plus the signed market value of every position) scaled by
    ``max_leverage`` (Requirement 22.1, 22.3); see the module docstring for
    the rationale behind this equity-based formula.
    """
    if not leverage_enabled:
        return cash.balance
    equity = portfolio_value(cash, positions, current_prices)
    return equity * max_leverage


def maintenance_margin_requirement(
    positions: dict[str, Position],
    current_prices: dict[str, Decimal],
    maintenance_margin_rate: Decimal,
) -> Decimal:
    """Return the total maintenance-margin requirement across all held positions.

    ``sum(abs(quantity * current_price) * maintenance_margin_rate)`` over
    every entry in ``positions``, traversed in sorted-symbol order
    (Requirement 22.3, 31.4). Absolute notional exposure is used regardless
    of long/short direction, since a maintenance-margin call is against
    exposure, not direction.

    Raises:
        ValueError: if ``positions`` holds a symbol with no corresponding
            entry in ``current_prices``.
    """
    total = Decimal(0)
    for symbol in sorted(positions):
        if symbol not in current_prices:
            raise ValueError(f"no current price supplied for held position {symbol!r}")
        position = positions[symbol]
        total += abs(position.quantity * current_prices[symbol]) * maintenance_margin_rate
    return total


def is_maintenance_breach(
    cash: CashLedger,
    positions: dict[str, Position],
    current_prices: dict[str, Decimal],
    maintenance_margin_rate: Decimal,
) -> bool:
    """Return whether current portfolio equity has fallen below the maintenance-margin requirement.

    ``True`` exactly when ``portfolio_value(...) < maintenance_margin_requirement(...)``
    — the deterministic trigger condition for a forced liquidation
    (Requirement 22.4, 22.5). Detecting the breach is this module's
    responsibility; sequencing and executing the liquidation itself belongs
    to the ``Simulation_Engine`` (see the module docstring's scope
    boundary).
    """
    equity = portfolio_value(cash, positions, current_prices)
    requirement = maintenance_margin_requirement(positions, current_prices, maintenance_margin_rate)
    return equity < requirement


def would_exceed_buying_power(
    fill: Fill,
    cash: CashLedger,
    positions: dict[str, Position],
    current_prices: dict[str, Decimal],
    leverage_enabled: bool,
    max_leverage: Decimal,
) -> bool:
    """Return whether applying ``fill`` would spend more than the account's buying power.

    Only ``BUY`` fills are checked (always returns ``False`` for a
    ``SELL``) — see the module docstring for why selling, including
    opening or adding to a short, is not constrained by buying power in
    this model. WHERE leverage is disabled, this is the primitive behind
    Requirement 21.4 ("prevent a ``Simulated_Order`` from filling if it
    would drive the ``Cash_Ledger`` below the configured minimum"): the
    minimum is implicitly zero, and buying power equals available cash, so
    a ``BUY`` costing more than cash on hand is blocked. WHERE leverage is
    enabled, the same check is made against the leveraged buying-power
    figure (Requirement 22.2, 22.3).

    This function only *detects* the block; recording the blocked fill and
    its reason in the ``Trade_Log`` is the caller's (the future
    ``Simulation_Engine``'s) responsibility.
    """
    if fill.side is not OrderSide.BUY:
        return False
    available = buying_power(cash, positions, current_prices, leverage_enabled, max_leverage)
    cost = fill.quantity * fill.price + fill.commission
    return cost > available
