from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from aqros_live_trading.domain.models import (
    BrokerAccount,
    BrokerPosition,
    ConnectionHealth,
    ConnectionStatus,
    KillSwitch,
    KillSwitchStatus,
    LiveOrder,
    OrderRouteStatus,
    OrderSide,
    OrderStatus,
    OrderType,
    ReconnectionPolicy,
    RouteRule,
    SessionStatus,
    TradingSession,
)


class TestConnectionHealth:
    def test_default_state(self) -> None:
        h = ConnectionHealth()
        assert h.status == ConnectionStatus.DISCONNECTED
        assert h.consecutive_failures == 0
        assert h.is_connected() is False

    def test_mark_connected(self) -> None:
        h = ConnectionHealth()
        h.mark_connected()
        assert h.status == ConnectionStatus.CONNECTED
        assert h.is_connected() is True
        assert h.consecutive_failures == 0

    def test_mark_disconnected(self) -> None:
        h = ConnectionHealth()
        h.mark_connected()
        h.mark_disconnected()
        assert h.status == ConnectionStatus.DISCONNECTED
        assert h.total_disconnections == 1
        assert h.consecutive_failures == 1

    def test_heartbeat_stale_when_never_received(self) -> None:
        h = ConnectionHealth(heartbeat_timeout=5.0)
        assert h.is_heartbeat_stale() is True

    def test_heartbeat_not_stale(self) -> None:
        h = ConnectionHealth(heartbeat_timeout=60.0)
        h.mark_heartbeat()
        assert h.is_heartbeat_stale() is False


class TestKillSwitch:
    def test_default_armed(self) -> None:
        ks = KillSwitch()
        assert ks.status == KillSwitchStatus.ARMED
        assert ks.is_triggered() is False

    def test_trigger(self) -> None:
        ks = KillSwitch()
        ks.trigger(by="test", reason="testing")
        assert ks.is_triggered() is True
        assert ks.triggered_by == "test"
        assert ks.reason == "testing"

    def test_trigger_works_even_when_disabled(self) -> None:
        """A disabled kill switch must still fire.

        This assertion previously encoded the opposite. `disable()` now means
        "do not auto-arm after reset" (a maintenance window); it must never be
        able to neuter the emergency stop, or an operator who disabled it once
        and forgot would have a kill switch that silently did nothing during a
        real incident.
        """
        ks = KillSwitch(enabled=False)
        ks.trigger(by="test", reason="testing")
        assert ks.is_triggered() is True

    def test_reset(self) -> None:
        ks = KillSwitch()
        ks.trigger(by="test", reason="testing")
        ks.reset()
        assert ks.is_triggered() is False
        assert ks.triggered_by == ""

    def test_disable(self) -> None:
        ks = KillSwitch()
        ks.disable()
        assert ks.enabled is False
        assert ks.status == KillSwitchStatus.DISABLED

    def test_enable(self) -> None:
        ks = KillSwitch()
        ks.disable()
        ks.enable()
        assert ks.enabled is True
        assert ks.status == KillSwitchStatus.ARMED


class TestReconnectionPolicy:
    def test_initial_state(self) -> None:
        rp = ReconnectionPolicy()
        assert rp.attempt == 0
        assert rp.is_exhausted is False

    def test_exponential_backoff(self) -> None:
        rp = ReconnectionPolicy(base_delay=1.0, max_delay=60.0)
        delays = [rp.get_delay() for _ in range(5)]
        for i in range(1, len(delays)):
            assert delays[i] >= delays[i - 1] * 0.5

    def test_max_attempts(self) -> None:
        rp = ReconnectionPolicy(base_delay=1.0, max_attempts=3)
        rp.get_delay()
        rp.get_delay()
        rp.get_delay()
        assert rp.is_exhausted is True
        assert rp.get_delay() < 0

    def test_reset(self) -> None:
        rp = ReconnectionPolicy(base_delay=1.0, max_attempts=3)
        rp.get_delay()
        rp.get_delay()
        rp.reset()
        assert rp.attempt == 0
        assert rp.is_exhausted is False

    def test_get_delay_returns_positive(self) -> None:
        rp = ReconnectionPolicy(base_delay=1.0)
        delay = rp.get_delay()
        assert delay > 0


class TestLiveOrder:
    def test_default_state(self) -> None:
        order = LiveOrder(
            order_id="test-1",
            client_order_id="client-1",
            portfolio_id="portfolio-1",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("100"),
        )
        assert order.status == OrderStatus.PENDING
        assert order.route_status == OrderRouteStatus.PENDING_ROUTE
        assert order.is_complete is False

    def test_is_complete_filled(self) -> None:
        order = LiveOrder(
            order_id="test-1",
            client_order_id="client-1",
            status=OrderStatus.FILLED,
            quantity=Decimal("100"),
        )
        assert order.is_complete is True

    def test_is_complete_cancelled(self) -> None:
        order = LiveOrder(
            order_id="test-1",
            client_order_id="client-1",
            status=OrderStatus.CANCELLED,
            quantity=Decimal("100"),
        )
        assert order.is_complete is True

    def test_fill_pct_zero_quantity(self) -> None:
        order = LiveOrder(
            order_id="test-1",
            client_order_id="client-1",
            quantity=Decimal("0"),
        )
        assert order.fill_pct == 0.0

    def test_fill_pct_partial(self) -> None:
        order = LiveOrder(
            order_id="test-1",
            client_order_id="client-1",
            quantity=Decimal("100"),
        )
        assert order.fill_pct == 0.0


class TestBrokerPosition:
    def test_create_position(self) -> None:
        pos = BrokerPosition(
            symbol="AAPL",
            quantity=Decimal("100"),
            market_value=Decimal("15000"),
            cost_basis=Decimal("14000"),
        )
        assert pos.symbol == "AAPL"
        assert pos.quantity == Decimal("100")

    def test_create_position_with_pl(self) -> None:
        pos = BrokerPosition(
            symbol="AAPL",
            quantity=Decimal("100"),
            market_value=Decimal("15000"),
            cost_basis=Decimal("14000"),
            unrealized_pl=Decimal("1000"),
            realized_pl=Decimal("500"),
        )
        assert pos.unrealized_pl == Decimal("1000")
        assert pos.realized_pl == Decimal("500")


class TestBrokerAccount:
    def test_create_account(self) -> None:
        acc = BrokerAccount(
            account_id="acc-1",
            buying_power=Decimal("50000"),
            cash=Decimal("25000"),
            portfolio_value=Decimal("100000"),
        )
        assert acc.account_id == "acc-1"
        assert acc.currency == "USD"


class TestTradingSession:
    def test_session_open(self) -> None:
        now = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
        session = TradingSession(
            date=date(2026, 7, 31),
            open=datetime(2026, 7, 31, 9, 30, tzinfo=UTC),
            close=datetime(2026, 7, 31, 16, 0, tzinfo=UTC),
        )
        assert session.is_open(now) is True
        assert session.get_session_for(now) == SessionStatus.REGULAR

    def test_session_closed(self) -> None:
        now = datetime(2026, 7, 31, 20, 0, tzinfo=UTC)
        session = TradingSession(
            date=date(2026, 7, 31),
            open=datetime(2026, 7, 31, 9, 30, tzinfo=UTC),
            close=datetime(2026, 7, 31, 16, 0, tzinfo=UTC),
        )
        assert session.is_open(now) is False
        assert session.get_session_for(now) == SessionStatus.CLOSED

    def test_pre_market(self) -> None:
        now = datetime(2026, 7, 31, 6, 0, tzinfo=UTC)
        session = TradingSession(
            date=date(2026, 7, 31),
            open=datetime(2026, 7, 31, 9, 30, tzinfo=UTC),
            close=datetime(2026, 7, 31, 16, 0, tzinfo=UTC),
            pre_market_open=datetime(2026, 7, 31, 4, 0, tzinfo=UTC),
            pre_market_close=datetime(2026, 7, 31, 9, 30, tzinfo=UTC),
        )
        assert session.is_pre_market(now) is True

    def test_after_hours(self) -> None:
        now = datetime(2026, 7, 31, 17, 0, tzinfo=UTC)
        session = TradingSession(
            date=date(2026, 7, 31),
            open=datetime(2026, 7, 31, 9, 30, tzinfo=UTC),
            close=datetime(2026, 7, 31, 16, 0, tzinfo=UTC),
            after_hours_open=datetime(2026, 7, 31, 16, 0, tzinfo=UTC),
            after_hours_close=datetime(2026, 7, 31, 20, 0, tzinfo=UTC),
        )
        assert session.is_after_hours(now) is True


class TestRouteRule:
    def test_default_rule(self) -> None:
        rule = RouteRule(preferred_broker="paper")
        assert rule.symbol_pattern == "*"
        assert rule.preferred_broker == "paper"
        assert rule.requires_pre_trade_risk is True

    def test_custom_rule(self) -> None:
        rule = RouteRule(
            symbol_pattern="AAPL",
            order_types=(OrderType.LIMIT,),
            min_quantity=Decimal("10"),
            max_quantity=Decimal("1000"),
            preferred_broker="ibkr",
            fallback_brokers=("alpaca",),
        )
        assert rule.symbol_pattern == "AAPL"
        assert rule.preferred_broker == "ibkr"
