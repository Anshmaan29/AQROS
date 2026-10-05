from __future__ import annotations

from decimal import Decimal

from hypothesis import assume, given
from hypothesis.strategies import decimals, sampled_from, text

from aqros_oms.domain.models import (
    Fill,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    RejectReason,
    TimeInForce,
    validate_order,
)

# --- Strategies -------------------------------------------------------------

valid_decimals = decimals(
    min_value=Decimal("0.0001"),
    max_value=Decimal(1000000),
    allow_nan=False,
    allow_infinity=False,
    places=4,
)

trade_decimals = decimals(
    min_value=Decimal("0.01"),
    max_value=Decimal(100000),
    allow_nan=False,
    allow_infinity=False,
    places=2,
)

side_strategy = sampled_from(list(OrderSide))
order_type_strategy = sampled_from(list(OrderType))
tif_strategy = sampled_from(list(TimeInForce))
status_strategy = sampled_from(list(OrderStatus))

# --- Property tests ---------------------------------------------------------


@given(
    quantity=valid_decimals,
    fill_qty=valid_decimals,
    fill_price=trade_decimals,
)
def test_fill_never_exceeds_quantity(
    quantity: Decimal, fill_qty: Decimal, fill_price: Decimal
) -> None:
    assume(quantity > 0)
    assume(fill_qty > 0)
    assume(fill_qty <= quantity)

    order = _make_order(quantity=quantity)
    order.accept()
    fill = Fill(trade_id="t-prop-1", order_id=order.order_id, quantity=fill_qty, price=fill_price)
    order.apply_fill(fill)

    assert order.filled_quantity <= order.quantity
    assert order.remaining_quantity >= 0
    assert order.filled_quantity + order.remaining_quantity == order.quantity


@given(
    quantity=valid_decimals,
    fill_qty=valid_decimals,
    fill_price=trade_decimals,
)
def test_fill_invariant(quantity: Decimal, fill_qty: Decimal, fill_price: Decimal) -> None:
    assume(quantity > 0)
    assume(fill_qty > 0)
    assume(fill_qty <= quantity)

    order = _make_order(quantity=quantity)
    order.accept()
    before = order.remaining_quantity
    fill = Fill(trade_id="t-prop-2", order_id=order.order_id, quantity=fill_qty, price=fill_price)
    order.apply_fill(fill)

    assert order.filled_quantity == quantity - order.remaining_quantity
    assert order.filled_quantity - (quantity - before) == fill_qty
    assert order.last_fill_price == fill_price


@given(
    q1=valid_decimals,
    q2=valid_decimals,
    p1=trade_decimals,
    p2=trade_decimals,
)
def test_avg_fill_price_weighted(q1: Decimal, q2: Decimal, p1: Decimal, p2: Decimal) -> None:
    assume(q1 > 0)
    assume(q2 > 0)
    total_q = q1 + q2
    assume(total_q <= Decimal(1000000))

    order = _make_order(quantity=total_q)
    order.accept()

    f1 = Fill(trade_id="t-prop-w1", order_id=order.order_id, quantity=q1, price=p1)
    order.apply_fill(f1)
    f2 = Fill(trade_id="t-prop-w2", order_id=order.order_id, quantity=q2, price=p2)
    order.apply_fill(f2)

    expected_avg = (q1 * p1 + q2 * p2) / total_q
    assert order.avg_fill_price is not None
    assert abs(order.avg_fill_price - expected_avg) < Decimal("0.01")


@given(
    quantity=valid_decimals,
    side=side_strategy,
)
def test_order_status_invariant(quantity: Decimal, side: OrderSide) -> None:
    assume(quantity > 0)
    order = _make_order(quantity=quantity, side=side)

    assert not order.is_complete
    assert order.remaining_quantity == quantity

    order.accept()
    assert order.status == OrderStatus.OPEN
    assert not order.is_complete


@given(
    q1=valid_decimals,
    q2=valid_decimals,
    p=trade_decimals,
)
def test_fill_commission_accumulates(q1: Decimal, q2: Decimal, p: Decimal) -> None:
    assume(q1 > 0)
    assume(q2 > 0)
    total = q1 + q2
    assume(total <= Decimal(1000000))

    order = _make_order(quantity=total)
    order.accept()

    c1 = Decimal("1.50")
    c2 = Decimal("2.50")

    f1 = Fill(trade_id="t-prop-c1", order_id=order.order_id, quantity=q1, price=p, commission=c1)
    order.apply_fill(f1)

    f2 = Fill(trade_id="t-prop-c2", order_id=order.order_id, quantity=q2, price=p, commission=c2)
    order.apply_fill(f2)

    assert order.total_commission == c1 + c2


@given(
    quantity=valid_decimals,
    fill_qty=valid_decimals,
)
def test_fill_pct_bounds(quantity: Decimal, fill_qty: Decimal) -> None:
    assume(quantity > 0)
    assume(fill_qty > 0)
    assume(fill_qty <= quantity)

    order = _make_order(quantity=quantity)
    order.accept()

    fill = Fill(
        trade_id="t-prop-pct", order_id=order.order_id, quantity=fill_qty, price=Decimal(100)
    )
    order.apply_fill(fill)

    assert 0.0 <= order.fill_pct <= 100.0


@given(
    client_order_id=text(max_size=20),
    portfolio_id=text(max_size=20),
    symbol=text(max_size=10),
    quantity=decimals(
        min_value=Decimal(-1000),
        max_value=Decimal(1000),
        allow_nan=False,
        allow_infinity=False,
        places=4,
    ),
)
def test_validate_order_rejects_bad_input(
    client_order_id: str,
    portfolio_id: str,
    symbol: str,
    quantity: Decimal,
) -> None:
    errors = validate_order(
        client_order_id=client_order_id,
        portfolio_id=portfolio_id,
        symbol=symbol,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=quantity,
    )
    has_quantity_error = any(r == RejectReason.INVALID_QUANTITY for r, _ in errors)
    has_symbol_error = any(r == RejectReason.INVALID_SYMBOL for r, _ in errors)
    has_portfolio_error = any(r == RejectReason.PORTFOLIO_NOT_FOUND for r, _ in errors)
    is_quantity_bad = quantity <= 0
    is_symbol_bad = not symbol.strip()
    is_client_id_bad = not client_order_id.strip()
    is_portfolio_bad = not portfolio_id.strip()

    assert has_quantity_error == is_quantity_bad
    assert has_symbol_error == (is_symbol_bad or is_client_id_bad)
    assert has_portfolio_error == is_portfolio_bad


@given(
    q1=valid_decimals,
    q2=valid_decimals,
    p1=trade_decimals,
    p2=trade_decimals,
)
def test_multiple_fills_produce_correct_fill_list(
    q1: Decimal, q2: Decimal, p1: Decimal, p2: Decimal
) -> None:
    assume(q1 > 0)
    assume(q2 > 0)
    total = q1 + q2
    assume(total <= Decimal(1000000))

    order = _make_order(quantity=total)
    order.accept()

    f1 = Fill(trade_id="t-mf1", order_id=order.order_id, quantity=q1, price=p1)
    order.apply_fill(f1)
    f2 = Fill(trade_id="t-mf2", order_id=order.order_id, quantity=q2, price=p2)
    order.apply_fill(f2)

    assert len(order.fills) == 2
    assert order.fills[0].trade_id == "t-mf1"
    assert order.fills[1].trade_id == "t-mf2"
    assert sum(f.quantity for f in order.fills) == order.filled_quantity
    assert order.filled_value == q1 * p1 + q2 * p2


@given(
    q1=valid_decimals,
    q2=valid_decimals,
    p=trade_decimals,
)
def test_fill_then_cancel_adds_to_fills(q1: Decimal, q2: Decimal, p: Decimal) -> None:
    assume(q1 > 0)
    assume(q2 > 0)
    total = q1 + q2
    assume(total <= Decimal(1000000))

    order = _make_order(quantity=total)
    order.accept()

    f1 = Fill(trade_id="t-fc1", order_id=order.order_id, quantity=q1, price=p)
    order.apply_fill(f1)
    assert order.status == OrderStatus.PARTIALLY_FILLED

    order.cancel()
    assert order.status == OrderStatus.CANCELLED
    assert order.filled_quantity == q1


def _make_order(
    quantity: Decimal = Decimal(10),
    side: OrderSide = OrderSide.BUY,
    price: Decimal | None = Decimal(100),
    order_type: OrderType = OrderType.LIMIT,
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
        time_in_force=TimeInForce.DAY,
        strategy="test",
        correlation_id="corr-123",
    )
