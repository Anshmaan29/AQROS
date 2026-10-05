from __future__ import annotations

from decimal import Decimal

from hypothesis import assume, given
from hypothesis import strategies as st

from aqros_live_trading.domain.models import (
    ConnectionHealth,
    KillSwitch,
    LiveOrder,
    OrderRouteStatus,
    OrderSide,
    OrderStatus,
    OrderType,
    ReconnectionPolicy,
    TimeInForce,
)

positive_decimals = st.decimals(
    min_value=Decimal("0.0001"),
    max_value=Decimal("1000000"),
    allow_nan=False,
    allow_infinity=False,
    places=4,
)

order_side_strategy = st.sampled_from(list(OrderSide))
order_type_strategy = st.sampled_from(list(OrderType))
order_status_strategy = st.sampled_from(list(OrderStatus))
time_in_force_strategy = st.sampled_from(list(TimeInForce))


@given(
    side=order_side_strategy,
    order_type=order_type_strategy,
    tif=time_in_force_strategy,
    quantity=positive_decimals,
)
def test_live_order_creation(
    side: OrderSide,
    order_type: OrderType,
    tif: TimeInForce,
    quantity: Decimal,
) -> None:
    assume(quantity > Decimal("0"))
    order = LiveOrder(
        order_id="prop-test-1",
        client_order_id="prop-client-1",
        portfolio_id="portfolio-1",
        symbol="AAPL",
        side=side,
        order_type=order_type,
        quantity=quantity,
        time_in_force=tif,
    )
    assert order.order_id == "prop-test-1"
    assert order.side == side
    assert order.order_type == order_type
    assert order.time_in_force == tif
    assert order.quantity == quantity
    assert order.status == OrderStatus.PENDING
    assert order.route_status == OrderRouteStatus.PENDING_ROUTE
    assert order.is_complete is False
    assert order.remaining_quantity == quantity
    assert order.fill_pct == 0.0


@given(
    filled=positive_decimals,
    total=positive_decimals,
)
def test_live_order_fill_pct(filled: Decimal, total: Decimal) -> None:
    assume(total > Decimal("0"))
    assume(filled <= total)
    order = LiveOrder(
        order_id="prop-fill-1",
        client_order_id="prop-client-1",
        quantity=total,
        filled_quantity=filled,
        remaining_quantity=total - filled,
    )
    expected_pct = float(filled / total) * 100.0
    assert abs(order.fill_pct - expected_pct) < 0.001


@given(st.integers(min_value=0, max_value=100))
def test_reconnection_policy_attempts(attempts: int) -> None:
    max_attempts = 10
    policy = ReconnectionPolicy(base_delay=1.0, max_delay=60.0, max_attempts=max_attempts)
    for _ in range(attempts):
        policy.get_delay()
    capped = min(attempts, max_attempts)
    assert policy.attempt == capped
    assert policy.is_exhausted == (attempts >= max_attempts)


@given(st.floats(min_value=0.1, max_value=10.0))
def test_reconnection_policy_delays_positive(base_delay: float) -> None:
    policy = ReconnectionPolicy(base_delay=base_delay, max_delay=60.0)
    delay = policy.get_delay()
    assert delay > 0
    assert delay >= base_delay


@given(st.booleans())
def test_kill_switch_triggers_regardless_of_enabled(enabled: bool) -> None:
    """`enabled` gates re-arming, never the emergency stop itself.

    Property form of the same safety rule as the unit test: for every value of
    `enabled`, triggering the switch stops trading.
    """
    ks = KillSwitch(enabled=enabled)
    ks.trigger(by="test", reason="prop test")
    assert ks.is_triggered() is True


@given(st.integers(min_value=0, max_value=100))
def test_connection_health_consecutive_failures(failures: int) -> None:
    h = ConnectionHealth()
    for _ in range(failures):
        h.mark_disconnected()
    assert h.consecutive_failures == failures
    assert h.total_disconnections == failures


@given(st.integers(min_value=0, max_value=10))
def test_connection_health_reconnections(reconnections: int) -> None:
    h = ConnectionHealth()
    for _ in range(reconnections):
        h.total_reconnections += 1
    assert h.total_reconnections == reconnections
