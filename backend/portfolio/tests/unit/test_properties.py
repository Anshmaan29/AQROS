from __future__ import annotations

from decimal import Decimal

from hypothesis import assume, given
from hypothesis import strategies as st

from aqros_portfolio.domain.models import (
    CashBalance,
    Portfolio,
    PortfolioStatistics,
    Position,
    PositionStatus,
    TradeDirection,
)

EQUITY = st.decimals(min_value=100_000, max_value=100_000_000, places=8)
QUANTITY = st.integers(min_value=1, max_value=1_000_000)
PRICE = st.decimals(min_value=1, max_value=10_000, places=2)
AMOUNT = st.decimals(min_value=1, max_value=10_000_000, places=2)


@given(EQUITY)
def test_cash_balance_invariant(equity: Decimal) -> None:
    balance = CashBalance(equity)
    assert balance.total >= 0
    assert balance.reserved >= 0
    assert balance.available >= 0
    total = balance.available + balance.reserved
    assert abs(total - balance.total) < Decimal("1e-28")


@given(EQUITY, AMOUNT)
def test_cash_deposit_withdraw_invariant(initial: Decimal, amount: Decimal) -> None:
    assume(initial > 0 and amount > 0 and amount <= initial)
    balance = CashBalance(initial)
    deposited = balance.deposit(amount)
    assert deposited.total == initial + amount
    withdrawn = deposited.withdraw(amount)
    assert abs(withdrawn.total - initial) < Decimal("0.001")


@given(QUANTITY, PRICE, PRICE)
def test_long_position_unrealized_pnl(qty: int, entry: Decimal, current: Decimal) -> None:
    assume(qty > 0 and entry > 0 and current > 0)
    pos = Position(
        position_id="prop1",
        portfolio_id="port1",
        symbol="TEST",
        direction=TradeDirection.LONG,
        quantity=Decimal(qty),
        avg_entry_price=entry,
        current_price=current,
    )
    expected = (current - entry) * Decimal(qty)
    assert pos.unrealized_pnl == expected
    assert pos.market_value == current * Decimal(qty)


@given(QUANTITY, PRICE, PRICE)
def test_short_position_unrealized_pnl(qty: int, entry: Decimal, current: Decimal) -> None:
    assume(qty > 0 and entry > 0 and current > 0)
    pos = Position(
        position_id="prop2",
        portfolio_id="port1",
        symbol="TEST",
        direction=TradeDirection.SHORT,
        quantity=Decimal(qty),
        avg_entry_price=entry,
        current_price=current,
    )
    expected = (entry - current) * Decimal(qty)
    assert pos.unrealized_pnl == expected


@given(EQUITY, st.integers(min_value=0, max_value=50))
def test_portfolio_position_count_bounds(equity: Decimal, count: int) -> None:
    assume(equity > 0)
    portfolio = Portfolio(
        portfolio_id="prop_port",
        name="Prop Test",
        cash=CashBalance(equity),
    )
    for i in range(count):
        pos = Position(
            position_id=f"prop_p{i}",
            portfolio_id="prop_port",
            symbol=f"S{i}",
            direction=TradeDirection.LONG,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("100"),
            current_price=Decimal("100"),
            status=PositionStatus.OPEN,
        )
        portfolio.add_position(pos)
    assert portfolio.position_count == count
    assert portfolio.gross_exposure_pct >= 0
    assert portfolio.net_exposure_pct >= 0


@given(EQUITY, st.floats(min_value=0.0, max_value=50.0))
def test_portfolio_leverage_bounds(equity: Decimal, exposure_pct: float) -> None:
    assume(equity > 0)
    portfolio = Portfolio(
        portfolio_id="prop_port2",
        name="Prop Test",
        cash=CashBalance(equity),
    )
    pos = Position(
        position_id="prop_p",
        portfolio_id="prop_port2",
        symbol="S",
        direction=TradeDirection.LONG,
        quantity=Decimal(int(equity * Decimal(str(exposure_pct / 100)) / Decimal("100"))),
        avg_entry_price=Decimal("100"),
        current_price=Decimal("100"),
        status=PositionStatus.OPEN,
    )
    if pos.quantity > 0 and pos.market_value > 0:
        portfolio.add_position(pos)
        assert portfolio.leverage >= 0.0


@given(st.lists(st.decimals(min_value=-5000, max_value=5000), min_size=0, max_size=20))
def test_portfolio_statistics_accumulation(pnls: list[Decimal]) -> None:
    positions = []
    for i, pnl in enumerate(pnls):
        pos = Position(
            position_id=f"stat_{i}",
            portfolio_id="port1",
            symbol=f"S{i}",
            direction=TradeDirection.LONG,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("100"),
            current_price=Decimal("100"),
            status=PositionStatus.CLOSED,
            realized_pnl=pnl,
        )
        positions.append(pos)

    stats = PortfolioStatistics.calculate(positions)
    assert stats.total_trades == len([p for p in positions if p.is_closed])
    assert stats.winning_trades + stats.losing_trades <= stats.total_trades
    assert stats.win_rate >= 0.0 and stats.win_rate <= 100.0
    assert stats.sharpe_ratio >= float("-inf")
    assert stats.sortino_ratio >= float("-inf")
    if stats.gross_loss > 0:
        assert stats.profit_factor >= 0.0
