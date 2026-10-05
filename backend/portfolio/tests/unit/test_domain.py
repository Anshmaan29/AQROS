from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from aqros_portfolio.domain.models import (
    CashBalance,
    Portfolio,
    PortfolioStatistics,
    Position,
    PositionStatus,
    TradeDirection,
)


class TestCashBalance:
    def test_deposit_increases_balance(self) -> None:
        balance = CashBalance(Decimal("1000"))
        new = balance.deposit(Decimal("500"))
        assert new.total == Decimal("1500")
        assert new.reserved == Decimal("0")

    def test_withdraw_decreases_balance(self) -> None:
        balance = CashBalance(Decimal("1000"))
        new = balance.withdraw(Decimal("600"))
        assert new.total == Decimal("400")

    def test_withdraw_insufficient_raises(self) -> None:
        balance = CashBalance(Decimal("100"))
        with pytest.raises(ValueError, match="Insufficient available cash"):
            balance.withdraw(Decimal("200"))

    def test_reserve_moves_from_available(self) -> None:
        balance = CashBalance(Decimal("1000"))
        new = balance.reserve(Decimal("300"))
        assert new.total == Decimal("1000")
        assert new.reserved == Decimal("300")
        assert new.available == Decimal("700")

    def test_reserve_insufficient_raises(self) -> None:
        balance = CashBalance(Decimal("100"))
        with pytest.raises(ValueError, match="Insufficient available cash to reserve"):
            balance.reserve(Decimal("200"))

    def test_release_frees_reserved(self) -> None:
        balance = CashBalance(Decimal("1000"), reserved=Decimal("300"))
        new = balance.release(Decimal("200"))
        assert new.reserved == Decimal("100")
        assert new.available == Decimal("900")

    def test_release_excess_caps_at_reserved(self) -> None:
        balance = CashBalance(Decimal("1000"), reserved=Decimal("100"))
        new = balance.release(Decimal("500"))
        assert new.reserved == Decimal("0")

    def test_chaining_operations(self) -> None:
        balance = CashBalance(Decimal("1000"))
        result = balance.deposit(Decimal("500")).reserve(Decimal("200"))
        assert result.total == Decimal("1500")
        assert result.reserved == Decimal("200")
        assert result.available == Decimal("1300")


class TestPositionLifespan:
    def test_long_unrealized_pnl_positive(self) -> None:
        pos = Position(
            position_id="p1",
            portfolio_id="port1",
            symbol="AAPL",
            direction=TradeDirection.LONG,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150"),
            current_price=Decimal("160"),
            opened_at=datetime.now(UTC),
        )
        assert pos.unrealized_pnl == Decimal("1000")
        assert pos.unrealized_pnl_pct == pytest.approx(6.6667, rel=0.01)

    def test_long_unrealized_pnl_negative(self) -> None:
        pos = Position(
            position_id="p2",
            portfolio_id="port1",
            symbol="AAPL",
            direction=TradeDirection.LONG,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150"),
            current_price=Decimal("140"),
        )
        assert pos.unrealized_pnl == Decimal("-1000")

    def test_short_unrealized_pnl_positive(self) -> None:
        pos = Position(
            position_id="p3",
            portfolio_id="port1",
            symbol="AAPL",
            direction=TradeDirection.SHORT,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150"),
            current_price=Decimal("140"),
        )
        assert pos.unrealized_pnl == Decimal("1000")

    def test_market_value(self) -> None:
        pos = Position(
            position_id="p4",
            portfolio_id="port1",
            symbol="AAPL",
            direction=TradeDirection.LONG,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150"),
            current_price=Decimal("160"),
        )
        assert pos.market_value == Decimal("16000")

    def test_close_long_position(self) -> None:
        pos = Position(
            position_id="p5",
            portfolio_id="port1",
            symbol="AAPL",
            direction=TradeDirection.LONG,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150"),
            current_price=Decimal("150"),
        )
        now = datetime.now(UTC)
        result = pos.close(Decimal("170"), now)
        assert result.status == PositionStatus.CLOSED
        assert result.realized_pnl == Decimal("2000")
        assert result.closed_at == now

    def test_total_pnl(self) -> None:
        pos = Position(
            position_id="p6",
            portfolio_id="port1",
            symbol="AAPL",
            direction=TradeDirection.LONG,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150"),
            current_price=Decimal("160"),
            realized_pnl=Decimal("500"),
        )
        assert pos.total_pnl == Decimal("1500")

    def test_update_price(self) -> None:
        pos = Position(
            position_id="p7",
            portfolio_id="port1",
            symbol="AAPL",
            direction=TradeDirection.LONG,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150"),
            current_price=Decimal("150"),
        )
        pos.update_price(Decimal("200"))
        assert pos.current_price == Decimal("200")
        assert pos.unrealized_pnl == Decimal("5000")

    def test_stop_loss_update(self) -> None:
        pos = Position(
            position_id="p8",
            portfolio_id="port1",
            symbol="AAPL",
            direction=TradeDirection.LONG,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150"),
            current_price=Decimal("150"),
        )
        pos.update_stop_loss(Decimal("140"))
        assert pos.stop_loss == Decimal("140")

    def test_take_profit_update(self) -> None:
        pos = Position(
            position_id="p9",
            portfolio_id="port1",
            symbol="AAPL",
            direction=TradeDirection.LONG,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150"),
            current_price=Decimal("150"),
        )
        pos.update_take_profit(Decimal("200"))
        assert pos.take_profit == Decimal("200")

    def test_modify_quantity_increase_long(self) -> None:
        pos = Position(
            position_id="p10",
            portfolio_id="port1",
            symbol="AAPL",
            direction=TradeDirection.LONG,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150"),
            current_price=Decimal("150"),
        )
        pos.modify_quantity(Decimal("200"), Decimal("160"))
        assert pos.quantity == Decimal("200")
        assert pos.avg_entry_price == Decimal("155")
        assert pos.realized_pnl == Decimal("0")

    def test_modify_quantity_decrease_long(self) -> None:
        pos = Position(
            position_id="p11",
            portfolio_id="port1",
            symbol="AAPL",
            direction=TradeDirection.LONG,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150"),
            current_price=Decimal("160"),
        )
        pos.modify_quantity(Decimal("60"), Decimal("160"))
        assert pos.quantity == Decimal("60")
        assert pos.realized_pnl == Decimal("400")

    def test_zero_entry_value_pnl_pct(self) -> None:
        pos = Position(
            position_id="p12",
            portfolio_id="port1",
            symbol="AAPL",
            direction=TradeDirection.LONG,
            quantity=Decimal("0"),
            avg_entry_price=Decimal("0"),
            current_price=Decimal("0"),
        )
        assert pos.unrealized_pnl_pct == 0.0


class TestPortfolio:
    def test_create_empty_portfolio(self) -> None:
        portfolio = Portfolio(
            portfolio_id="port1",
            name="Test Portfolio",
            cash=CashBalance(Decimal("1000000")),
            created_at=datetime.now(UTC),
        )
        assert portfolio.total_equity == Decimal("1000000")
        assert portfolio.position_count == 0
        assert portfolio.leverage == 0.0

    def test_add_open_position_increases_exposure(self) -> None:
        portfolio = Portfolio(
            portfolio_id="port1",
            name="Test",
            cash=CashBalance(Decimal("1000000")),
        )
        pos = Position(
            position_id="p1",
            portfolio_id="port1",
            symbol="AAPL",
            direction=TradeDirection.LONG,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150"),
            current_price=Decimal("160"),
            status=PositionStatus.OPEN,
        )
        portfolio.add_position(pos)
        assert portfolio.position_count == 1
        assert portfolio.market_value == Decimal("16000")
        assert portfolio.gross_exposure == Decimal("16000")
        expected_pct = float(Decimal("16000") / (Decimal("1000000") + Decimal("16000"))) * 100.0
        assert portfolio.gross_exposure_pct == pytest.approx(expected_pct, rel=0.01)

    def test_close_position_updates_cash_and_pnl(self) -> None:
        portfolio = Portfolio(
            portfolio_id="port1",
            name="Test",
            cash=CashBalance(Decimal("1000000")),
        )
        pos = Position(
            position_id="p1",
            portfolio_id="port1",
            symbol="AAPL",
            direction=TradeDirection.LONG,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150"),
            current_price=Decimal("150"),
            status=PositionStatus.OPEN,
        )
        portfolio.add_position(pos)
        now = datetime.now(UTC)
        result = portfolio.close_position("AAPL", Decimal("170"), now)
        assert result is not None
        assert result.status == PositionStatus.CLOSED
        assert portfolio.total_pnl_realized == Decimal("2000")
        assert portfolio.daily_pnl == Decimal("2000")
        assert portfolio.cash.total > Decimal("1000000")

    def test_close_nonexistent_position(self) -> None:
        portfolio = Portfolio(
            portfolio_id="port1",
            name="Test",
            cash=CashBalance(Decimal("1000000")),
        )
        result = portfolio.close_position("NONEXIST", Decimal("100"))
        assert result is None

    def test_sector_exposures(self) -> None:
        portfolio = Portfolio(
            portfolio_id="port1",
            name="Test",
            cash=CashBalance(Decimal("1000000")),
        )
        pos1 = Position(
            position_id="p1",
            portfolio_id="port1",
            symbol="AAPL",
            direction=TradeDirection.LONG,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150"),
            current_price=Decimal("160"),
            status=PositionStatus.OPEN,
            sector="Technology",
        )
        pos2 = Position(
            position_id="p2",
            portfolio_id="port1",
            symbol="JPM",
            direction=TradeDirection.LONG,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("200"),
            current_price=Decimal("210"),
            status=PositionStatus.OPEN,
            sector="Financial",
        )
        portfolio.add_position(pos1)
        portfolio.add_position(pos2)
        exposures = portfolio.sector_exposures()
        assert exposures["Technology"] == Decimal("16000")
        assert exposures["Financial"] == Decimal("21000")

    def test_drawdown_calculation(self) -> None:
        portfolio = Portfolio(
            portfolio_id="port1",
            name="Test",
            cash=CashBalance(Decimal("1000000")),
            peak_equity=Decimal("1200000"),
        )
        assert portfolio.drawdown_pct > 0

    def test_revalue_positions(self) -> None:
        portfolio = Portfolio(
            portfolio_id="port1",
            name="Test",
            cash=CashBalance(Decimal("1000000")),
        )
        pos = Position(
            position_id="p1",
            portfolio_id="port1",
            symbol="AAPL",
            direction=TradeDirection.LONG,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150"),
            current_price=Decimal("150"),
            status=PositionStatus.OPEN,
        )
        portfolio.add_position(pos)
        old_equity = portfolio.total_equity
        portfolio.revalue_positions({"AAPL": Decimal("200")})
        assert portfolio.total_equity > old_equity

    def test_cash_operations(self) -> None:
        portfolio = Portfolio(
            portfolio_id="port1",
            name="Test",
            cash=CashBalance(Decimal("1000000")),
        )
        portfolio.reserve_cash(Decimal("50000"))
        assert portfolio.cash.reserved == Decimal("50000")
        portfolio.release_cash(Decimal("50000"))
        assert portfolio.cash.reserved == Decimal("0")

    def test_net_exposure_mixed_long_short(self) -> None:
        portfolio = Portfolio(
            portfolio_id="port1",
            name="Test",
            cash=CashBalance(Decimal("1000000")),
        )
        long_pos = Position(
            position_id="p1",
            portfolio_id="port1",
            symbol="AAPL",
            direction=TradeDirection.LONG,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150"),
            current_price=Decimal("160"),
            status=PositionStatus.OPEN,
        )
        short_pos = Position(
            position_id="p2",
            portfolio_id="port1",
            symbol="TSLA",
            direction=TradeDirection.SHORT,
            quantity=Decimal("50"),
            avg_entry_price=Decimal("300"),
            current_price=Decimal("280"),
            status=PositionStatus.OPEN,
        )
        portfolio.add_position(long_pos)
        portfolio.add_position(short_pos)
        assert portfolio.net_exposure == Decimal("2000")


class TestPortfolioStatistics:
    def test_empty_positions(self) -> None:
        stats = PortfolioStatistics.calculate([])
        assert stats.total_trades == 0
        assert stats.win_rate == 0.0

    def test_all_winning_trades(self) -> None:
        closing_time = datetime.now(UTC)
        positions = [
            Position(
                position_id=f"p{i}",
                portfolio_id="port1",
                symbol="AAPL",
                direction=TradeDirection.LONG,
                quantity=Decimal("100"),
                avg_entry_price=Decimal("100"),
                current_price=Decimal("110"),
                status=PositionStatus.CLOSED,
                realized_pnl=Decimal("1000"),
                closed_at=closing_time,
            )
            for i in range(3)
        ]
        stats = PortfolioStatistics.calculate(positions)
        assert stats.total_trades == 3
        assert stats.winning_trades == 3
        assert stats.win_rate == 100.0

    def test_mixed_results(self) -> None:
        closing_time = datetime.now(UTC)
        positions = [
            Position(
                position_id="p1",
                portfolio_id="port1",
                symbol="AAPL",
                direction=TradeDirection.LONG,
                quantity=Decimal("100"),
                avg_entry_price=Decimal("100"),
                current_price=Decimal("100"),
                status=PositionStatus.CLOSED,
                realized_pnl=Decimal("2000"),
                closed_at=closing_time,
            ),
            Position(
                position_id="p2",
                portfolio_id="port1",
                symbol="MSFT",
                direction=TradeDirection.LONG,
                quantity=Decimal("100"),
                avg_entry_price=Decimal("100"),
                current_price=Decimal("100"),
                status=PositionStatus.CLOSED,
                realized_pnl=Decimal("-1000"),
                closed_at=closing_time,
            ),
        ]
        stats = PortfolioStatistics.calculate(positions)
        assert stats.total_trades == 2
        assert stats.winning_trades == 1
        assert stats.losing_trades == 1
        assert stats.total_pnl == Decimal("1000")
        assert stats.profit_factor == pytest.approx(2.0, rel=0.01)

    def test_profit_factor_with_losses(self) -> None:
        closing_time = datetime.now(UTC)
        positions = [
            Position(
                position_id="p1",
                portfolio_id="port1",
                symbol="AAPL",
                direction=TradeDirection.LONG,
                quantity=Decimal("100"),
                avg_entry_price=Decimal("100"),
                current_price=Decimal("100"),
                status=PositionStatus.CLOSED,
                realized_pnl=Decimal("3000"),
                closed_at=closing_time,
            ),
            Position(
                position_id="p2",
                portfolio_id="port1",
                symbol="MSFT",
                direction=TradeDirection.LONG,
                quantity=Decimal("100"),
                avg_entry_price=Decimal("100"),
                current_price=Decimal("100"),
                status=PositionStatus.CLOSED,
                realized_pnl=Decimal("-1000"),
                closed_at=closing_time,
            ),
        ]
        stats = PortfolioStatistics.calculate(positions)
        assert stats.gross_profit == Decimal("3000")
        assert stats.gross_loss == Decimal("1000")
        assert stats.profit_factor == pytest.approx(3.0, rel=0.01)


class TestPortfolioProperties:
    def test_leverage_is_zero_when_no_cash(self) -> None:
        portfolio = Portfolio(
            portfolio_id="port1",
            name="Test",
            cash=CashBalance(Decimal("0")),
        )
        assert portfolio.leverage == 0.0

    def test_zero_equity_prevents_division_errors(self) -> None:
        portfolio = Portfolio(
            portfolio_id="port1",
            name="Test",
            cash=CashBalance(Decimal("0")),
        )
        assert portfolio.gross_exposure_pct == 0.0
        assert portfolio.net_exposure_pct == 0.0
        assert portfolio.daily_return_pct == 0.0
        assert portfolio.daily_loss_pct == 0.0
        assert portfolio.drawdown_pct == 0.0
        assert portfolio.symbol_exposure_pct("AAPL") == 0.0
        assert portfolio.sector_exposure_pct("Tech") == 0.0
