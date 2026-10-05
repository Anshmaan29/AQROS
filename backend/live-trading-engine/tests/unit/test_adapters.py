from __future__ import annotations

from datetime import UTC
from decimal import Decimal

import pytest

from aqros_live_trading.adapters.broker.alpaca_stub import AlpacaAdapter
from aqros_live_trading.adapters.broker.ibkr_stub import IBKRAdapter
from aqros_live_trading.adapters.broker.paper_broker import PaperBrokerAdapter
from aqros_live_trading.adapters.calendar import TradingCalendar
from aqros_live_trading.adapters.execution import ExecutionEngine
from aqros_live_trading.adapters.kill_switch import KillSwitchManager
from aqros_live_trading.adapters.routing import OrderRouter
from aqros_live_trading.domain.models import (
    KillSwitch,
    LiveOrder,
    OrderSide,
    OrderStatus,
    OrderType,
    RouteRule,
)


class TestPaperBrokerAdapter:
    @pytest.mark.asyncio
    async def test_connect(self) -> None:
        broker = PaperBrokerAdapter()
        assert await broker.is_connected() is False
        await broker.connect()
        assert await broker.is_connected() is True

    @pytest.mark.asyncio
    async def test_disconnect(self) -> None:
        broker = PaperBrokerAdapter()
        await broker.connect()
        await broker.disconnect()
        assert await broker.is_connected() is False

    @pytest.mark.asyncio
    async def test_heartbeat_when_connected(self) -> None:
        broker = PaperBrokerAdapter()
        await broker.connect()
        assert await broker.heartbeat() is True

    @pytest.mark.asyncio
    async def test_heartbeat_when_disconnected(self) -> None:
        broker = PaperBrokerAdapter()
        assert await broker.heartbeat() is False

    @pytest.mark.asyncio
    async def test_submit_market_order(self) -> None:
        broker = PaperBrokerAdapter()
        await broker.connect()
        order = LiveOrder(
            order_id="test-1",
            client_order_id="client-1",
            portfolio_id="portfolio-1",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("100"),
        )
        report = await broker.submit_order(order)
        assert report.broker_order_id.startswith("paper-")
        assert report.status == "filled"
        assert report.filled_quantity == Decimal("100")

    @pytest.mark.asyncio
    async def test_cancel_order_found(self) -> None:
        broker = PaperBrokerAdapter()
        await broker.connect()
        order = LiveOrder(
            order_id="test-2",
            client_order_id="client-2",
            portfolio_id="portfolio-1",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("100"),
        )
        report = await broker.submit_order(order)
        cancel_report = await broker.cancel_order(report.broker_order_id)
        assert cancel_report.status == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_order_not_found(self) -> None:
        broker = PaperBrokerAdapter()
        await broker.connect()
        report = await broker.cancel_order("nonexistent")
        assert report.status == "rejected"

    @pytest.mark.asyncio
    async def test_get_account(self) -> None:
        broker = PaperBrokerAdapter()
        await broker.connect()
        account = await broker.get_account()
        assert account.account_id == "paper-001"
        assert account.buying_power == Decimal("1000000")

    @pytest.mark.asyncio
    async def test_get_positions_empty(self) -> None:
        broker = PaperBrokerAdapter()
        await broker.connect()
        positions = await broker.get_positions()
        assert positions == []

    @pytest.mark.asyncio
    async def test_name(self) -> None:
        broker = PaperBrokerAdapter()
        assert broker.name == "paper"


class TestAlpacaStub:
    @pytest.mark.asyncio
    async def test_connect(self) -> None:
        adapter = AlpacaAdapter()
        assert await adapter.is_connected() is False
        await adapter.connect()
        assert await adapter.is_connected() is True

    @pytest.mark.asyncio
    async def test_submit_order_not_implemented(self) -> None:
        adapter = AlpacaAdapter()
        with pytest.raises(NotImplementedError):
            await adapter.submit_order(
                LiveOrder(
                    order_id="test",
                    client_order_id="test",
                    quantity=Decimal("1"),
                )
            )

    @pytest.mark.asyncio
    async def test_name(self) -> None:
        adapter = AlpacaAdapter()
        assert adapter.name == "alpaca"


class TestIBKRStub:
    @pytest.mark.asyncio
    async def test_connect(self) -> None:
        adapter = IBKRAdapter()
        assert await adapter.is_connected() is False
        await adapter.connect()
        assert await adapter.is_connected() is True

    @pytest.mark.asyncio
    async def test_name(self) -> None:
        adapter = IBKRAdapter()
        assert adapter.name == "ibkr"


class TestExecutionEngine:
    @pytest.mark.asyncio
    async def test_execute_market_order(self) -> None:
        broker = PaperBrokerAdapter()
        await broker.connect()
        engine = ExecutionEngine(broker)
        order = LiveOrder(
            order_id="exec-1",
            client_order_id="exec-client-1",
            portfolio_id="portfolio-1",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("100"),
        )
        result = await engine.execute(order)
        assert result.broker_order_id is not None
        assert result.status == OrderStatus.FILLED
        assert result.routed_to == "paper"

    @pytest.mark.asyncio
    async def test_cancel(self) -> None:
        broker = PaperBrokerAdapter()
        await broker.connect()
        engine = ExecutionEngine(broker)
        order = LiveOrder(
            order_id="exec-2",
            client_order_id="exec-client-2",
            portfolio_id="portfolio-1",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("100"),
        )
        result = await engine.execute(order)
        cancel_report = await engine.cancel(result.broker_order_id)
        assert cancel_report.status == "cancelled"


class TestOrderRouter:
    @pytest.mark.asyncio
    async def test_route_to_available_broker(self) -> None:
        broker = PaperBrokerAdapter()
        await broker.connect()
        router = OrderRouter({"paper": broker})
        order = LiveOrder(
            order_id="route-1",
            client_order_id="route-client-1",
            portfolio_id="portfolio-1",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("100"),
        )
        name, _ = await router.route(order)
        assert name == "paper"

    @pytest.mark.asyncio
    async def test_route_with_preferred_broker(self) -> None:
        broker = PaperBrokerAdapter()
        await broker.connect()
        router = OrderRouter({"paper": broker})
        router.add_rule(
            RouteRule(
                symbol_pattern="*",
                order_types=(OrderType.MARKET,),
                preferred_broker="paper",
            )
        )
        order = LiveOrder(
            order_id="route-2",
            client_order_id="route-client-2",
            portfolio_id="portfolio-1",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("100"),
        )
        name, _ = await router.route(order)
        assert name == "paper"

    @pytest.mark.asyncio
    async def test_no_available_broker(self) -> None:
        broker = PaperBrokerAdapter()
        router = OrderRouter({"paper": broker})
        order = LiveOrder(
            order_id="route-3",
            client_order_id="route-client-3",
            portfolio_id="portfolio-1",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("100"),
        )
        with pytest.raises(ConnectionError, match="No available broker"):
            await router.route(order)

    def test_matches_symbol_wildcard(self) -> None:
        broker = PaperBrokerAdapter()
        router = OrderRouter({"paper": broker})
        assert router._matches_symbol("AAPL", "*") is True
        assert router._matches_symbol("ANYTHING", "*") is True

    def test_matches_symbol_exact(self) -> None:
        broker = PaperBrokerAdapter()
        router = OrderRouter({"paper": broker})
        assert router._matches_symbol("AAPL", "AAPL") is True
        assert router._matches_symbol("MSFT", "AAPL") is False

    def test_matches_symbol_pattern(self) -> None:
        broker = PaperBrokerAdapter()
        router = OrderRouter({"paper": broker})
        assert router._matches_symbol("AAPL", "A*") is True
        assert router._matches_symbol("MSFT", "A*") is False

    def test_add_and_get_rules(self) -> None:
        broker = PaperBrokerAdapter()
        router = OrderRouter({"paper": broker})
        assert len(router.get_rules()) == 0
        router.add_rule(RouteRule(preferred_broker="paper"))
        assert len(router.get_rules()) == 1

    def test_clear_rules(self) -> None:
        broker = PaperBrokerAdapter()
        router = OrderRouter({"paper": broker})
        router.add_rule(RouteRule(preferred_broker="paper"))
        router.clear_rules()
        assert len(router.get_rules()) == 0


class TestKillSwitchManager:
    def test_trigger(self) -> None:
        ks = KillSwitch()
        manager = KillSwitchManager(ks)
        manager.trigger(by="test", reason="test reason")
        assert manager.is_triggered is True
        status = manager.get_status()
        assert status["triggered_by"] == "test"
        assert status["reason"] == "test reason"

    def test_reset(self) -> None:
        ks = KillSwitch()
        manager = KillSwitchManager(ks)
        manager.trigger(by="test", reason="test reason")
        manager.reset()
        assert manager.is_triggered is False

    def test_disable(self) -> None:
        ks = KillSwitch()
        manager = KillSwitchManager(ks)
        manager.disable()
        status = manager.get_status()
        assert status["enabled"] is False
        assert status["status"] == "disabled"

    def test_enable(self) -> None:
        ks = KillSwitch()
        manager = KillSwitchManager(ks)
        manager.disable()
        manager.enable()
        status = manager.get_status()
        assert status["enabled"] is True
        assert status["status"] == "armed"

    def test_arm(self) -> None:
        ks = KillSwitch()
        manager = KillSwitchManager(ks)
        manager.trigger(by="test", reason="test")
        manager.arm()
        status = manager.get_status()
        assert status["status"] == "armed"


class TestTradingCalendar:
    def test_weekend(self) -> None:
        cal = TradingCalendar()
        from datetime import date

        saturday = date(2026, 8, 1)
        sunday = date(2026, 8, 2)
        assert cal.is_weekend(saturday) is True
        assert cal.is_weekend(sunday) is True

    def test_weekday(self) -> None:
        cal = TradingCalendar()
        from datetime import date

        friday = date(2026, 7, 31)
        assert cal.is_weekend(friday) is False

    def test_holiday(self) -> None:
        cal = TradingCalendar()
        from datetime import date

        holiday = date(2026, 12, 25)
        cal.add_holiday(holiday)
        assert cal.is_holiday(holiday) is True

    def test_trading_day(self) -> None:
        cal = TradingCalendar()
        from datetime import date

        friday = date(2026, 7, 31)
        assert cal.is_trading_day(friday) is True
        saturday = date(2026, 8, 1)
        assert cal.is_trading_day(saturday) is False

    def test_is_market_open(self) -> None:
        cal = TradingCalendar()
        from datetime import datetime

        now = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
        assert cal.is_market_open(now) is True

    def test_is_market_closed(self) -> None:
        cal = TradingCalendar()
        from datetime import datetime

        now = datetime(2026, 7, 31, 20, 0, tzinfo=UTC)
        assert cal.is_market_open(now) is False

    def test_get_trading_session(self) -> None:
        cal = TradingCalendar()
        from datetime import datetime

        now = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
        session = cal.get_trading_session(now)
        assert session.date.isoformat() == "2026-07-31"
        assert session.is_open(now) is True

    def test_current_session_status_regular(self) -> None:
        cal = TradingCalendar()
        from datetime import datetime

        now = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
        status = cal.current_session_status(now)
        assert status.value == "regular"

    def test_current_session_status_closed(self) -> None:
        cal = TradingCalendar()
        from datetime import datetime

        now = datetime(2026, 7, 31, 21, 0, tzinfo=UTC)
        status = cal.current_session_status(now)
        assert status.value == "closed"
