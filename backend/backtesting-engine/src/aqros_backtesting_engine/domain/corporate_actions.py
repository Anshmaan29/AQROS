"""Point-in-time corporate-action application for the Backtesting Engine.

A ``CorporateAction`` (splits, reverse splits, cash dividends, stock
dividends, symbol changes, mergers, and delistings) changes an instrument's
shares, identity, or cash entitlement (Requirement 8). This module applies
a single ``CorporateAction`` to a ``Position``/``CashLedger`` pair, point-in
time: the caller MUST have already resolved that
``action.knowledge_time <= clock`` before invoking the action (this module
re-asserts it via ``assert_knowable`` so the guard can never be
accidentally skipped, per Requirement 8.6 and design.md Decision 3).

These are pure functions, not a class — mirroring the style of
``domain/latency.py``, ``domain/slippage.py``, and ``domain/commission.py``:
no I/O, no wall-clock reads, fully deterministic given the same
``CorporateAction``, ``Position``, and ``CashLedger`` inputs.

Rounding convention (Requirement 8.2 — "preserving total value except for
any rounding that is recorded explicitly"): splits, reverse splits, and
stock dividends rescale a position's quantity by a factor and then derive a
new average cost so that ``quantity * average_cost`` (total position value)
is preserved as closely as the rescaled quantity allows. Both the rescaled
quantity and the derived average cost are quantized with
``Decimal.quantize`` using ``ROUND_HALF_EVEN`` ("banker's rounding", chosen
because it does not systematically bias results high or low across many
corporate actions). Any residual difference between the pre- and
post-adjustment total value is exactly the "explicitly recorded rounding"
the requirement allows; the caller may choose to log or trade-log-record
this module's returned description string, which names the ratio applied,
to make that rounding auditable.

Stock-dividend ratio convention (Requirement 8.3): ``action.ratio`` for a
``STOCK_DIVIDEND`` is the *dividend rate* — the fraction of additional
shares distributed per share held (e.g. ``Decimal("0.05")`` for a 5% stock
dividend, meaning 5 additional shares per 100 held). This makes a stock
dividend mathematically a rescale by ``(1 + ratio)``, exactly like a split
rescale by ``ratio`` — the two code paths share the same
``_rescale_position`` helper.

Symbol change / merger convention (Requirement 8.4): this module returns a
``Position`` whose ``symbol`` has been changed to ``action.successor_symbol``
with quantity and cost basis otherwise unchanged. Re-keying the caller's
position map (e.g. a ``dict[str, Position]``) by the new symbol is the
**caller's** responsibility — this module has no concept of a position map,
only a single ``Position``.

Delisting convention (Requirement 8.5): a delisting resolves the affected
``Position`` to flat (``quantity = 0``, ``average_cost = 0``). If
``action.cash_amount`` is set, it is treated as a final cash payout per
share held (credited to the ``Cash_Ledger``, exactly like a cash dividend);
if it is ``None``, no compensation is paid. The difference between the
final payout and the position's pre-delisting book value
(``quantity * average_cost``) is realized into ``Position.realized_pnl``
before the position is flattened, so the delisting's economic effect is
never silently dropped. The caller is expected to record the returned
description in the ``Trade_Log`` (Requirement 8.5).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal

from aqros_backtesting_engine.domain.lookahead import assert_knowable
from aqros_backtesting_engine.domain.models import (
    CashLedger,
    CorporateAction,
    CorporateActionType,
    Position,
)

__all__ = ["apply_corporate_action"]

# Rounding precision for rescaled quantities and derived average costs.
# 8 decimal places accommodates fractional shares arising from non-integer
# split/dividend ratios while keeping arithmetic exact and deterministic
# (Requirement 8.2, 31.2 — Decimal arithmetic only, no floating point).
_QUANTITY_QUANTUM = Decimal("0.00000001")
_COST_QUANTUM = Decimal("0.00000001")


def _rescale_position(position: Position, factor: Decimal) -> Position:
    """Rescale ``position.quantity`` by ``factor``, preserving total value up to rounding.

    ``new_quantity = position.quantity * factor``, quantized to
    ``_QUANTITY_QUANTUM``. ``new_average_cost`` is then derived from the
    pre-adjustment total value (``position.quantity * position.average_cost``)
    divided by ``new_quantity``, quantized to ``_COST_QUANTUM``, so that
    ``new_quantity * new_average_cost`` reconstructs the original total
    value as closely as the quantized quantity permits (Requirement 8.2).
    ``position.realized_pnl`` is carried through unchanged — a split,
    reverse split, or stock dividend realizes no profit or loss on its own.
    """
    total_value = position.quantity * position.average_cost
    new_quantity = (position.quantity * factor).quantize(
        _QUANTITY_QUANTUM, rounding=ROUND_HALF_EVEN
    )
    if new_quantity == 0:
        new_average_cost = Decimal(0)
    else:
        new_average_cost = (total_value / new_quantity).quantize(
            _COST_QUANTUM, rounding=ROUND_HALF_EVEN
        )
    return replace(position, quantity=new_quantity, average_cost=new_average_cost)


def _apply_split(
    action: CorporateAction, position: Position | None, cash: CashLedger
) -> tuple[Position | None, CashLedger, str | None]:
    if action.ratio is None:
        raise ValueError(
            f"CorporateAction of type {action.action_type} for {action.symbol} requires a ratio"
        )
    if position is None:
        return None, cash, None
    new_position = _rescale_position(position, action.ratio)
    description = (
        f"{action.action_type.value} applied to {action.symbol}: ratio={action.ratio} "
        f"({position.quantity} -> {new_position.quantity} shares, "
        f"average_cost {position.average_cost} -> {new_position.average_cost})"
    )
    return new_position, cash, description


def _apply_cash_dividend(
    action: CorporateAction, position: Position | None, cash: CashLedger
) -> tuple[Position | None, CashLedger, str | None]:
    if action.cash_amount is None:
        raise ValueError(
            f"CorporateAction of type {action.action_type} for {action.symbol} requires a cash_amount"
        )
    if position is None:
        return None, cash, None
    credit = position.quantity * action.cash_amount
    new_cash = replace(cash, balance=cash.balance + credit)
    description = (
        f"cash dividend credited for {action.symbol}: {credit} "
        f"({position.quantity} shares x {action.cash_amount}/share)"
    )
    return position, new_cash, description


def _apply_stock_dividend(
    action: CorporateAction, position: Position | None, cash: CashLedger
) -> tuple[Position | None, CashLedger, str | None]:
    if action.ratio is None:
        raise ValueError(
            f"CorporateAction of type {action.action_type} for {action.symbol} requires a ratio"
        )
    if position is None:
        return None, cash, None
    factor = Decimal(1) + action.ratio
    new_position = _rescale_position(position, factor)
    description = (
        f"stock dividend applied to {action.symbol}: rate={action.ratio} "
        f"({position.quantity} -> {new_position.quantity} shares, "
        f"average_cost {position.average_cost} -> {new_position.average_cost})"
    )
    return new_position, cash, description


def _apply_symbol_change(
    action: CorporateAction, position: Position | None, cash: CashLedger
) -> tuple[Position | None, CashLedger, str | None]:
    if action.successor_symbol is None:
        raise ValueError(
            f"CorporateAction of type {action.action_type} for {action.symbol} requires a successor_symbol"
        )
    if position is None:
        return None, cash, None
    new_position = replace(position, symbol=action.successor_symbol)
    description = (
        f"{action.action_type.value}: {action.symbol} -> {action.successor_symbol}; "
        "caller must re-key the position map by the new symbol"
    )
    return new_position, cash, description


def _apply_delisting(
    action: CorporateAction, position: Position | None, cash: CashLedger
) -> tuple[Position | None, CashLedger, str | None]:
    if position is None:
        return None, cash, None
    pre_value = position.quantity * position.average_cost
    if action.cash_amount is not None:
        payout = (position.quantity * action.cash_amount).quantize(
            _COST_QUANTUM, rounding=ROUND_HALF_EVEN
        )
    else:
        payout = Decimal(0)
    new_cash = replace(cash, balance=cash.balance + payout) if payout != 0 else cash
    realized_adjustment = payout - pre_value
    new_position = replace(
        position,
        quantity=Decimal(0),
        average_cost=Decimal(0),
        realized_pnl=position.realized_pnl + realized_adjustment,
    )
    description = (
        f"delisting resolved for {action.symbol}: position flattened "
        f"({position.quantity} -> 0 shares), payout={payout}, "
        f"realized P&L adjustment={realized_adjustment}"
    )
    return new_position, new_cash, description


def apply_corporate_action(
    action: CorporateAction,
    position: Position | None,
    cash: CashLedger,
    clock: datetime,
) -> tuple[Position | None, CashLedger, str | None]:
    """Apply a single point-in-time ``CorporateAction`` to a position and cash ledger.

    Args:
        action: the ``CorporateAction`` to apply, as reported by the Market
            Data Service (Requirement 8.7) — never synthesized here.
        position: the current ``Position`` in ``action.symbol``, or ``None``
            if no position is held. When ``None``, every action type is a
            no-op: there is nothing to adjust.
        cash: the current ``Cash_Ledger``.
        clock: the current ``Simulation_Clock`` time. Only actions whose
            ``knowledge_time <= clock`` may be applied (Requirement 8.6);
            this is enforced by calling ``assert_knowable`` before any
            adjustment, so a caller can never accidentally apply an action
            before it was knowable.

    Returns:
        A ``(new_position, new_cash, description)`` tuple:

        - ``new_position``: the adjusted ``Position`` (or ``None`` if
          ``position`` was ``None``). For ``SYMBOL_CHANGE``/``MERGER``, the
          returned position carries the successor symbol — the caller is
          responsible for re-keying its position map by that new symbol.
        - ``new_cash``: the ``Cash_Ledger`` after any credit/debit (a new
          instance, since ``CashLedger`` is frozen; identical to ``cash``
          when no cash movement occurred).
        - ``description``: a human-readable, trade-log-style description of
          what happened (e.g. ``"split applied to AAPL: ratio=2 ..."``), or
          ``None`` when ``position`` was ``None`` and the action was a
          no-op. The caller is expected to record this in the ``Trade_Log``
          (Requirement 8.5 for delistings; useful context for every other
          action type too).

    Raises:
        LookAheadViolationError: if ``action.knowledge_time > clock``
            (Requirement 8.6; ``domain/lookahead.py``).
        ValueError: if ``action`` is missing a field required by its
            ``action_type`` (e.g. a ``SPLIT`` with no ``ratio``) — this
            indicates a malformed upstream record, not a valid corporate
            action, and must not be silently applied.
    """
    assert_knowable(
        action.knowledge_time,
        clock,
        context=f"corporate action {action.action_type.value} for {action.symbol}",
    )

    if action.action_type in (CorporateActionType.SPLIT, CorporateActionType.REVERSE_SPLIT):
        return _apply_split(action, position, cash)
    if action.action_type is CorporateActionType.CASH_DIVIDEND:
        return _apply_cash_dividend(action, position, cash)
    if action.action_type is CorporateActionType.STOCK_DIVIDEND:
        return _apply_stock_dividend(action, position, cash)
    if action.action_type in (CorporateActionType.SYMBOL_CHANGE, CorporateActionType.MERGER):
        return _apply_symbol_change(action, position, cash)
    if action.action_type is CorporateActionType.DELISTING:
        return _apply_delisting(action, position, cash)

    raise AssertionError(f"unhandled CorporateActionType: {action.action_type!r}")  # exhaustive
