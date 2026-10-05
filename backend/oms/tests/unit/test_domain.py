from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from aqros_oms.domain.models import (
    Fill,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    RejectReason,
    TimeInForce,
    calculate_order_expiry,
    validate_order,
)


class TestOrderStateMachine:
    def test_pending_can_accept(self) -> None:
        order = _make_order()
        order.accept()
        assert order.status == OrderStatus.OPEN

    def test_pending_can_reject(self) -> None:
        order = _make_order()
        order.reject(RejectReason.INVALID_QUANTITY, "Bad qty")
        assert order.status == OrderStatus.REJECTED
        assert order.reject_reason == RejectReason.INVALID_QUANTITY
        assert order.reject_message == "Bad qty"

    def test_pending_can_cancel(self) -> None:
        order = _make_order()
        order.cancel()
        assert order.status == OrderStatus.CANCELLED

    def test_open_can_fill(self) -> None:
        order = _make_order()
        order.accept()
        fill = Fill(
            trade_id="t1", order_id=order.order_id, quantity=Decimal(10), price=Decimal(100)
        )
        order.apply_fill(fill)
        assert order.status == OrderStatus.FILLED
        assert order.filled_quantity == Decimal(10)
        assert order.avg_fill_price == Decimal(100)

    def test_open_can_partially_fill(self) -> None:
        order = _make_order(quantity=Decimal(20))
        order.accept()
        fill = Fill(
            trade_id="t1", order_id=order.order_id, quantity=Decimal(10), price=Decimal(100)
        )
        order.apply_fill(fill)
        assert order.status == OrderStatus.PARTIALLY_FILLED
        assert order.filled_quantity == Decimal(10)
        assert order.remaining_quantity == Decimal(10)

    def test_open_can_cancel(self) -> None:
        order = _make_order()
        order.accept()
        order.cancel()
        assert order.status == OrderStatus.CANCELLED

    def test_open_can_expire(self) -> None:
        order = _make_order()
        order.accept()
        order.expire()
        assert order.status == OrderStatus.EXPIRED

    def test_partially_filled_can_fill_remaining(self) -> None:
        order = _make_order(quantity=Decimal(20))
        order.accept()
        fill1 = Fill(
            trade_id="t1", order_id=order.order_id, quantity=Decimal(10), price=Decimal(100)
        )
        order.apply_fill(fill1)
        assert order.status == OrderStatus.PARTIALLY_FILLED
        fill2 = Fill(
            trade_id="t2", order_id=order.order_id, quantity=Decimal(10), price=Decimal(101)
        )
        order.apply_fill(fill2)
        assert order.status == OrderStatus.FILLED
        assert order.filled_quantity == Decimal(20)

    def test_cancelled_cannot_transition(self) -> None:
        order = _make_order()
        order.cancel()
        with pytest.raises(ValueError, match="Cannot transition"):
            order.accept()

    def test_rejected_cannot_transition(self) -> None:
        order = _make_order()
        order.reject(RejectReason.INVALID_QUANTITY)
        with pytest.raises(ValueError, match="Cannot transition"):
            order.accept()

    def test_filled_cannot_transition(self) -> None:
        order = _make_order()
        order.accept()
        fill = Fill(
            trade_id="t1", order_id=order.order_id, quantity=Decimal(10), price=Decimal(100)
        )
        order.apply_fill(fill)
        with pytest.raises(ValueError, match="Cannot transition"):
            order.cancel()

    def test_fill_exceeds_remaining_raises(self) -> None:
        order = _make_order(quantity=Decimal(10))
        order.accept()
        fill = Fill(
            trade_id="t1", order_id=order.order_id, quantity=Decimal(15), price=Decimal(100)
        )
        with pytest.raises(ValueError, match="exceeds remaining"):
            order.apply_fill(fill)

    def test_fill_completed_order_raises(self) -> None:
        order = _make_order()
        order.accept()
        fill = Fill(
            trade_id="t1", order_id=order.order_id, quantity=Decimal(10), price=Decimal(100)
        )
        order.apply_fill(fill)
        fill2 = Fill(
            trade_id="t2", order_id=order.order_id, quantity=Decimal(5), price=Decimal(100)
        )
        with pytest.raises(ValueError, match="Cannot fill completed order"):
            order.apply_fill(fill2)


class TestOrderProperties:
    def test_is_complete_true_for_filled(self) -> None:
        order = _make_order()
        order.accept()
        fill = Fill(
            trade_id="t1", order_id=order.order_id, quantity=Decimal(10), price=Decimal(100)
        )
        order.apply_fill(fill)
        assert order.is_complete

    def test_is_complete_true_for_cancelled(self) -> None:
        order = _make_order()
        order.cancel()
        assert order.is_complete

    def test_is_complete_true_for_rejected(self) -> None:
        order = _make_order()
        order.reject(RejectReason.INVALID_QUANTITY)
        assert order.is_complete

    def test_is_complete_false_for_open(self) -> None:
        order = _make_order()
        order.accept()
        assert not order.is_complete

    def test_remaining_quantity(self) -> None:
        order = _make_order(quantity=Decimal(20))
        order.accept()
        fill = Fill(trade_id="t1", order_id=order.order_id, quantity=Decimal(7), price=Decimal(100))
        order.apply_fill(fill)
        assert order.remaining_quantity == Decimal(13)

    def test_fill_pct(self) -> None:
        order = _make_order(quantity=Decimal(20))
        order.accept()
        fill = Fill(trade_id="t1", order_id=order.order_id, quantity=Decimal(5), price=Decimal(100))
        order.apply_fill(fill)
        assert order.fill_pct == 25.0

    def test_avg_fill_price_multiple_fills(self) -> None:
        order = _make_order(quantity=Decimal(20))
        order.accept()
        fill1 = Fill(
            trade_id="t1", order_id=order.order_id, quantity=Decimal(10), price=Decimal(100)
        )
        order.apply_fill(fill1)
        fill2 = Fill(
            trade_id="t2", order_id=order.order_id, quantity=Decimal(10), price=Decimal(110)
        )
        order.apply_fill(fill2)
        assert order.avg_fill_price == Decimal(105)

    def test_last_fill_price(self) -> None:
        order = _make_order(quantity=Decimal(20))
        order.accept()
        fill1 = Fill(
            trade_id="t1", order_id=order.order_id, quantity=Decimal(10), price=Decimal(100)
        )
        order.apply_fill(fill1)
        fill2 = Fill(
            trade_id="t2", order_id=order.order_id, quantity=Decimal(10), price=Decimal(105)
        )
        order.apply_fill(fill2)
        assert order.last_fill_price == Decimal(105)

    def test_total_commission(self) -> None:
        order = _make_order(quantity=Decimal(10))
        order.accept()
        fill = Fill(
            trade_id="t1",
            order_id=order.order_id,
            quantity=Decimal(10),
            price=Decimal(100),
            commission=Decimal(5),
        )
        order.apply_fill(fill)
        assert order.total_commission == Decimal(5)

    def test_multiple_fills_commission(self) -> None:
        order = _make_order(quantity=Decimal(20))
        order.accept()
        fill1 = Fill(
            trade_id="t1",
            order_id=order.order_id,
            quantity=Decimal(10),
            price=Decimal(100),
            commission=Decimal(2),
        )
        order.apply_fill(fill1)
        fill2 = Fill(
            trade_id="t2",
            order_id=order.order_id,
            quantity=Decimal(10),
            price=Decimal(100),
            commission=Decimal(3),
        )
        order.apply_fill(fill2)
        assert order.total_commission == Decimal(5)


class TestOrderValidation:
    def test_valid_market_order(self) -> None:
        errors = validate_order(
            client_order_id="co1",
            portfolio_id="p1",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal(10),
        )
        assert errors == []

    def test_valid_limit_order(self) -> None:
        errors = validate_order(
            client_order_id="co1",
            portfolio_id="p1",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal(10),
            price=Decimal(150),
        )
        assert errors == []

    def test_invalid_quantity_zero(self) -> None:
        errors = validate_order(
            client_order_id="co1",
            portfolio_id="p1",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal(0),
        )
        assert any(r == RejectReason.INVALID_QUANTITY for r, _ in errors)

    def test_limit_order_no_price(self) -> None:
        errors = validate_order(
            client_order_id="co1",
            portfolio_id="p1",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal(10),
        )
        assert any(r == RejectReason.INVALID_PRICE for r, _ in errors)

    def test_stop_order_no_stop_price(self) -> None:
        errors = validate_order(
            client_order_id="co1",
            portfolio_id="p1",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.STOP,
            quantity=Decimal(10),
        )
        assert any(r == RejectReason.INVALID_PRICE for r, _ in errors)

    def test_exceeds_max_quantity(self) -> None:
        errors = validate_order(
            client_order_id="co1",
            portfolio_id="p1",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal(2000000),
            max_quantity=Decimal(1000000),
        )
        assert any(r == RejectReason.EXCEEDS_MAX_ORDER_QUANTITY for r, _ in errors)

    def test_exceeds_max_value(self) -> None:
        errors = validate_order(
            client_order_id="co1",
            portfolio_id="p1",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal(100),
            price=Decimal(500),
            max_value=Decimal(100000000),
        )
        assert not any(r == RejectReason.EXCEEDS_MAX_ORDER_VALUE for r, _ in errors)

    def test_empty_symbol(self) -> None:
        errors = validate_order(
            client_order_id="co1",
            portfolio_id="p1",
            symbol="",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal(10),
        )
        assert any(r == RejectReason.INVALID_SYMBOL for r, _ in errors)

    def test_empty_client_order_id(self) -> None:
        errors = validate_order(
            client_order_id="",
            portfolio_id="p1",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal(10),
        )
        assert any(r == RejectReason.INVALID_SYMBOL for r, _ in errors)

    def test_empty_portfolio_id(self) -> None:
        errors = validate_order(
            client_order_id="co1",
            portfolio_id="",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal(10),
        )
        assert any(r == RejectReason.PORTFOLIO_NOT_FOUND for r, _ in errors)


class TestOrderExpiry:
    def test_day_expiry(self) -> None:
        now = datetime(2024, 1, 15, 10, 30, tzinfo=UTC)
        expiry = calculate_order_expiry(TimeInForce.DAY, now)
        assert expiry is not None
        assert expiry.hour == 23
        assert expiry.minute == 59

    def test_gtc_no_expiry(self) -> None:
        now = datetime(2024, 1, 15, 10, 30, tzinfo=UTC)
        expiry = calculate_order_expiry(TimeInForce.GTC, now)
        assert expiry is None

    def test_ioc_expiry(self) -> None:
        now = datetime(2024, 1, 15, 10, 30, tzinfo=UTC)
        expiry = calculate_order_expiry(TimeInForce.IOC, now)
        assert expiry is not None
        assert expiry > now

    def test_fok_expiry(self) -> None:
        now = datetime(2024, 1, 15, 10, 30, tzinfo=UTC)
        expiry = calculate_order_expiry(TimeInForce.FOK, now)
        assert expiry is not None

    def test_gtd_expiry(self) -> None:
        now = datetime(2024, 1, 15, 10, 30, tzinfo=UTC)
        gtd = datetime(2024, 1, 16, 10, 30, tzinfo=UTC)
        expiry = calculate_order_expiry(TimeInForce.GTD, now, gtd)
        assert expiry == gtd


class TestFillModel:
    def test_fill_creation(self) -> None:
        now = datetime.now(UTC)
        fill = Fill(
            trade_id="t1",
            order_id="o1",
            quantity=Decimal(10),
            price=Decimal(100),
            commission=Decimal(1),
            created_at=now,
        )
        assert fill.trade_id == "t1"
        assert fill.quantity == Decimal(10)
        assert fill.price == Decimal(100)
        assert fill.commission == Decimal(1)

    def test_fill_default_commission(self) -> None:
        fill = Fill(trade_id="t1", order_id="o1", quantity=Decimal(10), price=Decimal(100))
        assert fill.commission == Decimal(0)


class TestOrderBuySell:
    def test_buy_side(self) -> None:
        assert OrderSide.BUY.value == "buy"

    def test_sell_side(self) -> None:
        assert OrderSide.SELL.value == "sell"

    def test_buy_order(self) -> None:
        order = _make_order(side=OrderSide.BUY)
        assert order.side == OrderSide.BUY

    def test_sell_order(self) -> None:
        order = _make_order(side=OrderSide.SELL)
        assert order.side == OrderSide.SELL


class TestOrderExpiration:
    def test_should_expire_gtd(self) -> None:
        now = datetime.now(UTC)
        past = now - timedelta(hours=1)
        order = _make_order(expires_at=past)
        order.accept()

        assert order.should_expire(now)

    def test_should_not_expire_gtd_future(self) -> None:
        now = datetime.now(UTC)
        future = now + timedelta(hours=1)
        order = _make_order(expires_at=future)
        order.accept()
        assert not order.should_expire(now)

    def test_has_expired_true_when_expired(self) -> None:
        now = datetime.now(UTC)
        past = now - timedelta(hours=1)
        order = _make_order(expires_at=past)
        order.accept()
        assert order.has_expired(now)

    def test_has_expired_false_when_not_expired(self) -> None:
        now = datetime.now(UTC)
        future = now + timedelta(hours=1)
        order = _make_order(expires_at=future)
        order.accept()
        assert not order.has_expired(now)


def _make_order(
    quantity: Decimal = Decimal(10),
    side: OrderSide = OrderSide.BUY,
    price: Decimal | None = Decimal(100),
    order_type: OrderType = OrderType.LIMIT,
    time_in_force: TimeInForce = TimeInForce.DAY,
    expires_at: datetime | None = None,
) -> Order:
    return Order(
        order_id="test-order-id",
        client_order_id="test-client-id",
        portfolio_id="test-portfolio",
        symbol="AAPL",
        side=side,
        order_type=order_type,
        quantity=quantity,
        price=price,
        time_in_force=time_in_force,
        expires_at=expires_at,
        strategy="test",
        correlation_id="corr-123",
    )
