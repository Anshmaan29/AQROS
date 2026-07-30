from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from aqros_backtesting_engine.adapters.risk_check_factory import (
    ConfigurableRiskCheck,
    default_risk_check_factory,
)
from aqros_backtesting_engine.domain.models import AssetClass, BacktestConfiguration

from aqros_strategy_core.contracts import OrderIntent, OrderSide, OrderType
from aqros_strategy_core.strategy import StrategyContext


def _intent(quantity: Decimal = Decimal("100")) -> OrderIntent:
    return OrderIntent(
        client_order_id="test-1",
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=quantity,
        limit_price=None,
        emitted_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def _context() -> StrategyContext:
    return StrategyContext(as_of=datetime(2024, 1, 1, tzinfo=UTC))


def _config(**overrides: object) -> BacktestConfiguration:
    params: dict[str, object] = {
        "strategy_id": "test",
        "strategy_params": {},
        "model_name": "test_model",
        "model_version": None,
        "universe": ("AAPL",),
        "exchange": "NYSE",
        "start": datetime(2024, 1, 1, tzinfo=UTC),
        "end": datetime(2024, 1, 10, tzinfo=UTC),
        "starting_cash": Decimal("100000"),
        "bar_interval": "daily",
        "slippage_model": "zero",
        "slippage_params": {},
        "commission_model": "zero",
        "commission_params": {},
        "fill_model": "immediate",
        "fill_params": {},
        "latency_model": "zero",
        "latency_params": {},
        "leverage_enabled": False,
        "max_leverage": Decimal("1.0"),
        "equity_sample_interval": "daily",
        "benchmark_symbol": None,
        "seed": 42,
        "asset_class": AssetClass.EQUITY,
    }
    params.update(overrides)
    return BacktestConfiguration(**params)


class TestConfigurableRiskCheck:
    async def test_approves_without_limits(self) -> None:
        check = ConfigurableRiskCheck()
        decision = check.check(_intent(), _context())
        assert decision.approved is True
        assert decision.reason is None

    async def test_approves_when_under_limit(self) -> None:
        check = ConfigurableRiskCheck(max_notional=Decimal("1000"))
        decision = check.check(_intent(quantity=Decimal("100")), _context())
        assert decision.approved is True

    async def test_rejects_when_over_limit(self) -> None:
        check = ConfigurableRiskCheck(max_notional=Decimal("50"))
        decision = check.check(_intent(quantity=Decimal("100")), _context())
        assert decision.approved is False
        assert "exceeds max" in (decision.reason or "")

    async def test_approves_at_exact_limit_edge(self) -> None:
        check = ConfigurableRiskCheck(max_notional=Decimal("100"))
        decision = check.check(_intent(quantity=Decimal("100")), _context())
        assert decision.approved is True

    async def test_approves_below_limit(self) -> None:
        check = ConfigurableRiskCheck(max_notional=Decimal("200"))
        decision = check.check(_intent(quantity=Decimal("100")), _context())
        assert decision.approved is True

    async def test_zero_max_notional_rejects_all(self) -> None:
        check = ConfigurableRiskCheck(max_notional=Decimal("0"))
        decision = check.check(_intent(quantity=Decimal("1")), _context())
        assert decision.approved is False

    async def test_provides_reason_on_rejection(self) -> None:
        check = ConfigurableRiskCheck(max_notional=Decimal("10"))
        decision = check.check(_intent(quantity=Decimal("100")), _context())
        assert decision.reason is not None
        assert "100" in decision.reason


class TestDefaultRiskCheckFactory:
    async def test_factory_creates_check_without_limits(self) -> None:
        check = default_risk_check_factory(_config())
        assert isinstance(check, ConfigurableRiskCheck)
        decision = check.check(_intent(), _context())
        assert decision.approved is True

    async def test_factory_creates_check_with_max_notional(self) -> None:
        check = default_risk_check_factory(_config(strategy_params={"max_notional": "50"}))
        decision = check.check(_intent(quantity=Decimal("100")), _context())
        assert decision.approved is False

    async def test_factory_defaults_to_no_limit(self) -> None:
        check = default_risk_check_factory(_config(strategy_params={"other_param": "value"}))
        decision = check.check(_intent(quantity=Decimal("999999")), _context())
        assert decision.approved is True
