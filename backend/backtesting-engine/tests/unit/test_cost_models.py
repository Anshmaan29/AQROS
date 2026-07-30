from __future__ import annotations

import random
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from aqros_backtesting_engine.domain.commission import (
    PctNotionalCommission,
    PerShareCommission,
    ZeroCommission,
)
from aqros_backtesting_engine.domain.fills import (
    ImmediateFillModel,
    LiquidityCappedFillModel,
)
from aqros_backtesting_engine.domain.latency import (
    ConfigurableLatency,
    FixedLatency,
    ZeroLatency,
)
from aqros_backtesting_engine.domain.models import (
    Bar,
    OrderSide,
    OrderType,
    SimulatedOrder,
)
from aqros_backtesting_engine.domain.slippage import FixedBpsSlippage, ZeroSlippage

# ---------------------------------------------------------------------------
# Commission models
# ---------------------------------------------------------------------------


class TestZeroCommission:
    def test_cost_is_always_zero(self) -> None:
        model = ZeroCommission()
        assert model.cost(Decimal("100"), Decimal("50")) == Decimal(0)
        assert model.cost(Decimal("0"), Decimal("0")) == Decimal(0)
        assert model.cost(Decimal("999999"), Decimal("1e-8")) == Decimal(0)


class TestPerShareCommission:
    def test_cost_multiplies_quantity(self) -> None:
        model = PerShareCommission(per_share=Decimal("0.01"))
        assert model.cost(Decimal("100"), Decimal("50")) == Decimal("1.00")
        assert model.cost(Decimal("0"), Decimal("50")) == Decimal(0)

    def test_cost_independent_of_price(self) -> None:
        model = PerShareCommission(per_share=Decimal("0.01"))
        assert model.cost(Decimal("100"), Decimal("1")) == model.cost(
            Decimal("100"), Decimal("9999")
        )

    def test_negative_per_share_raises(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            PerShareCommission(per_share=Decimal("-0.01"))


class TestPctNotionalCommission:
    def test_cost_multiplies_notional(self) -> None:
        model = PctNotionalCommission(pct=Decimal("0.001"))
        qty = Decimal("100")
        price = Decimal("50")
        assert model.cost(qty, price) == qty * price * Decimal("0.001")

    def test_zero_pct_is_free(self) -> None:
        model = PctNotionalCommission(pct=Decimal("0"))
        assert model.cost(Decimal("100"), Decimal("50")) == Decimal(0)

    def test_negative_pct_raises(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            PctNotionalCommission(pct=Decimal("-0.001"))


# ---------------------------------------------------------------------------
# Slippage models
# ---------------------------------------------------------------------------


class TestZeroSlippage:
    def test_price_unchanged(self) -> None:
        model = ZeroSlippage()
        price = Decimal("100.00")
        rng = random.Random(42)
        assert model.adjust(price, OrderSide.BUY, rng) == price
        assert model.adjust(price, OrderSide.SELL, rng) == price


class TestFixedBpsSlippage:
    def test_buy_increases_price(self) -> None:
        model = FixedBpsSlippage(basis_points=Decimal("10"))
        price = Decimal("100.00")
        expected = price + price * Decimal("10") / Decimal(10_000)
        assert model.adjust(price, OrderSide.BUY, random.Random(0)) == expected

    def test_sell_decreases_price(self) -> None:
        model = FixedBpsSlippage(basis_points=Decimal("10"))
        price = Decimal("100.00")
        expected = price - price * Decimal("10") / Decimal(10_000)
        assert model.adjust(price, OrderSide.SELL, random.Random(0)) == expected

    def test_zero_bps_no_adjustment(self) -> None:
        model = FixedBpsSlippage(basis_points=Decimal("0"))
        price = Decimal("100.00")
        assert model.adjust(price, OrderSide.BUY, random.Random(0)) == price
        assert model.adjust(price, OrderSide.SELL, random.Random(0)) == price

    def test_negative_bps_raises(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            FixedBpsSlippage(basis_points=Decimal("-1"))

    def test_deterministic(self) -> None:
        model = FixedBpsSlippage(basis_points=Decimal("5"))
        rng = random.Random(99)
        r1 = model.adjust(Decimal("200"), OrderSide.BUY, rng)
        rng = random.Random(99)
        r2 = model.adjust(Decimal("200"), OrderSide.BUY, rng)
        assert r1 == r2


# ---------------------------------------------------------------------------
# Latency models
# ---------------------------------------------------------------------------


class TestZeroLatency:
    def test_eligible_immediately(self) -> None:
        model = ZeroLatency()
        dt = datetime(2024, 1, 2, 14, 30)
        assert model.eligible_time(dt, random.Random(0)) == dt


class TestFixedLatency:
    def test_delay_added(self) -> None:
        model = FixedLatency(delay=timedelta(seconds=5))
        dt = datetime(2024, 1, 2, 14, 30, 0)
        assert model.eligible_time(dt, random.Random(0)) == dt + timedelta(seconds=5)

    def test_negative_delay_raises(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            FixedLatency(delay=timedelta(seconds=-1))

    def test_deterministic(self) -> None:
        model = FixedLatency(delay=timedelta(seconds=10))
        dt = datetime(2024, 1, 2, 14, 30, 0)
        r1 = model.eligible_time(dt, random.Random(42))
        r2 = model.eligible_time(dt, random.Random(99))
        assert r1 == r2


class TestConfigurableLatency:
    def test_eligible_within_range(self) -> None:
        model = ConfigurableLatency(
            min_delay=timedelta(seconds=1),
            max_delay=timedelta(seconds=10),
        )
        dt = datetime(2024, 1, 2, 14, 30, 0)
        for _ in range(100):
            result = model.eligible_time(dt, random.Random(_))
            assert dt + timedelta(seconds=1) <= result <= dt + timedelta(seconds=10)

    def test_negative_min_raises(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            ConfigurableLatency(
                min_delay=timedelta(seconds=-1),
                max_delay=timedelta(seconds=1),
            )

    def test_min_exceeds_max_raises(self) -> None:
        with pytest.raises(ValueError, match="max_delay"):
            ConfigurableLatency(
                min_delay=timedelta(seconds=10),
                max_delay=timedelta(seconds=1),
            )

    def test_min_equals_max_is_deterministic(self) -> None:
        model = ConfigurableLatency(
            min_delay=timedelta(seconds=5),
            max_delay=timedelta(seconds=5),
        )
        dt = datetime(2024, 1, 2, 14, 30, 0)
        r1 = model.eligible_time(dt, random.Random(42))
        r2 = model.eligible_time(dt, random.Random(99))
        assert r1 == r2 == dt + timedelta(seconds=5)


# ---------------------------------------------------------------------------
# Fill models
# ---------------------------------------------------------------------------


@pytest.fixture
def rng() -> random.Random:
    return random.Random(42)


@pytest.fixture
def aapl_bar() -> Bar:
    return Bar(
        symbol="AAPL",
        event_time=datetime(2024, 1, 2, 14, 30),
        knowledge_time=datetime(2024, 1, 2, 14, 30),
        open=Decimal("100"),
        high=Decimal("105"),
        low=Decimal("99"),
        close=Decimal("103"),
        volume=Decimal("1000000"),
    )


def _market_order(
    side: OrderSide = OrderSide.BUY,
    quantity: Decimal = Decimal("100"),
    symbol: str = "AAPL",
) -> SimulatedOrder:
    return SimulatedOrder(
        client_order_id="test-1",
        symbol=symbol,
        side=side,
        order_type=OrderType.MARKET,
        quantity=quantity,
        limit_price=None,
        emitted_at=datetime(2024, 1, 2, 14, 30),
        eligible_at=datetime(2024, 1, 2, 14, 30),
        status=None,
        reject_reason=None,
    )


class TestImmediateFillModel:
    def test_fills_full_quantity(self, rng: random.Random, aapl_bar: Bar) -> None:
        model = ImmediateFillModel()
        order = _market_order()
        fill = model.fill(
            order, aapl_bar, aapl_bar.event_time, ZeroSlippage(), ZeroCommission(), rng
        )
        assert fill is not None
        assert fill.quantity == order.quantity
        assert fill.price == aapl_bar.open
        assert fill.commission == Decimal(0)

    def test_market_fills_at_open(self, rng: random.Random, aapl_bar: Bar) -> None:
        model = ImmediateFillModel()
        order = _market_order()
        fill = model.fill(
            order, aapl_bar, aapl_bar.event_time, ZeroSlippage(), ZeroCommission(), rng
        )
        assert fill is not None
        assert fill.price == aapl_bar.open

    def test_sell_market_fills_at_open(self, rng: random.Random, aapl_bar: Bar) -> None:
        model = ImmediateFillModel()
        order = _market_order(side=OrderSide.SELL)
        fill = model.fill(
            order, aapl_bar, aapl_bar.event_time, ZeroSlippage(), ZeroCommission(), rng
        )
        assert fill is not None
        assert fill.price == aapl_bar.open

    def test_limit_buy_fills_when_low_satisfies(self, rng: random.Random, aapl_bar: Bar) -> None:
        model = ImmediateFillModel()
        order = SimulatedOrder(
            client_order_id="limit-1",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("100"),
            limit_price=Decimal("101"),
            emitted_at=datetime(2024, 1, 2, 14, 30),
            eligible_at=datetime(2024, 1, 2, 14, 30),
            status=None,
            reject_reason=None,
        )
        fill = model.fill(
            order, aapl_bar, aapl_bar.event_time, ZeroSlippage(), ZeroCommission(), rng
        )
        assert fill is not None
        assert fill.price == Decimal("101")

    def test_limit_buy_not_satisfied_returns_none(self, rng: random.Random, aapl_bar: Bar) -> None:
        model = ImmediateFillModel()
        order = SimulatedOrder(
            client_order_id="limit-2",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("100"),
            limit_price=Decimal("50"),
            emitted_at=datetime(2024, 1, 2, 14, 30),
            eligible_at=datetime(2024, 1, 2, 14, 30),
            status=None,
            reject_reason=None,
        )
        fill = model.fill(
            order, aapl_bar, aapl_bar.event_time, ZeroSlippage(), ZeroCommission(), rng
        )
        assert fill is None

    def test_limit_sell_fills_when_high_satisfies(self, rng: random.Random, aapl_bar: Bar) -> None:
        model = ImmediateFillModel()
        order = SimulatedOrder(
            client_order_id="limit-3",
            symbol="AAPL",
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            quantity=Decimal("100"),
            limit_price=Decimal("102"),
            emitted_at=datetime(2024, 1, 2, 14, 30),
            eligible_at=datetime(2024, 1, 2, 14, 30),
            status=None,
            reject_reason=None,
        )
        fill = model.fill(
            order, aapl_bar, aapl_bar.event_time, ZeroSlippage(), ZeroCommission(), rng
        )
        assert fill is not None
        assert fill.price == Decimal("102")

    def test_limit_sell_not_satisfied_returns_none(self, rng: random.Random, aapl_bar: Bar) -> None:
        model = ImmediateFillModel()
        order = SimulatedOrder(
            client_order_id="limit-4",
            symbol="AAPL",
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            quantity=Decimal("100"),
            limit_price=Decimal("200"),
            emitted_at=datetime(2024, 1, 2, 14, 30),
            eligible_at=datetime(2024, 1, 2, 14, 30),
            status=None,
            reject_reason=None,
        )
        fill = model.fill(
            order, aapl_bar, aapl_bar.event_time, ZeroSlippage(), ZeroCommission(), rng
        )
        assert fill is None

    def test_not_eligible_yet_returns_none(self, rng: random.Random, aapl_bar: Bar) -> None:
        model = ImmediateFillModel()
        order = SimulatedOrder(
            client_order_id="test-1",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("100"),
            limit_price=None,
            emitted_at=datetime(2024, 1, 2, 14, 30),
            eligible_at=datetime(2024, 1, 2, 14, 31),
            status=None,
            reject_reason=None,
        )
        clock = datetime(2024, 1, 2, 14, 30)
        fill = model.fill(order, aapl_bar, clock, ZeroSlippage(), ZeroCommission(), rng)
        assert fill is None

    def test_deterministic(self, aapl_bar: Bar) -> None:
        model = ImmediateFillModel()
        order = _market_order()
        r1 = model.fill(
            order, aapl_bar, aapl_bar.event_time, ZeroSlippage(), ZeroCommission(), random.Random(0)
        )
        r2 = model.fill(
            order, aapl_bar, aapl_bar.event_time, ZeroSlippage(), ZeroCommission(), random.Random(0)
        )
        assert r1 == r2


class TestLiquidityCappedFillModel:
    def test_respects_participation_rate(self, rng: random.Random, aapl_bar: Bar) -> None:
        rate = Decimal("0.05")
        model = LiquidityCappedFillModel(max_participation_rate=rate)
        order = _market_order(quantity=Decimal("1000000"))
        fill = model.fill(
            order, aapl_bar, aapl_bar.event_time, ZeroSlippage(), ZeroCommission(), rng
        )
        assert fill is not None
        max_allowed = aapl_bar.volume * rate
        assert fill.quantity <= max_allowed

    def test_small_order_not_capped(self, rng: random.Random, aapl_bar: Bar) -> None:
        model = LiquidityCappedFillModel(max_participation_rate=Decimal("0.1"))
        order = _market_order(quantity=Decimal("10"))
        fill = model.fill(
            order, aapl_bar, aapl_bar.event_time, ZeroSlippage(), ZeroCommission(), rng
        )
        assert fill is not None
        assert fill.quantity == order.quantity

    def test_negative_rate_raises(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            LiquidityCappedFillModel(max_participation_rate=Decimal("-0.1"))

    def test_zero_rate_blocks_all(self, rng: random.Random, aapl_bar: Bar) -> None:
        model = LiquidityCappedFillModel(max_participation_rate=Decimal("0"))
        order = _market_order(quantity=Decimal("10"))
        fill = model.fill(
            order, aapl_bar, aapl_bar.event_time, ZeroSlippage(), ZeroCommission(), rng
        )
        assert fill is None


class TestFillModelComposition:
    def test_commission_and_slippage_both_applied(self, rng: random.Random, aapl_bar: Bar) -> None:
        model = ImmediateFillModel()
        slippage = FixedBpsSlippage(basis_points=Decimal("10"))
        commission = PerShareCommission(per_share=Decimal("0.01"))
        order = _market_order(quantity=Decimal("100"))
        fill = model.fill(order, aapl_bar, aapl_bar.event_time, slippage, commission, rng)
        assert fill is not None
        assert fill.price != aapl_bar.open
        assert fill.commission > Decimal(0)

    def test_fill_metadata(self, rng: random.Random, aapl_bar: Bar) -> None:
        model = ImmediateFillModel()
        order = _market_order()
        fill = model.fill(
            order, aapl_bar, aapl_bar.event_time, ZeroSlippage(), ZeroCommission(), rng
        )
        assert fill is not None
        assert fill.client_order_id == "test-1"
        assert fill.symbol == "AAPL"
        assert fill.side is OrderSide.BUY
        assert fill.filled_at == aapl_bar.event_time
