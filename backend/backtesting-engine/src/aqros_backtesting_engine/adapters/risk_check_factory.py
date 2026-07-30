from __future__ import annotations

from decimal import Decimal

from aqros_backtesting_engine.domain.models import BacktestConfiguration
from aqros_strategy_core import RiskCheck, StrategyContext
from aqros_strategy_core.contracts import OrderIntent
from aqros_strategy_core.risk import RiskDecision


class ConfigurableRiskCheck:
    def __init__(self, max_notional: Decimal | None = None) -> None:
        self._max_notional = max_notional

    def check(self, order_intent: OrderIntent, context: StrategyContext) -> RiskDecision:
        if self._max_notional is not None and order_intent.quantity > self._max_notional:
            return RiskDecision(
                approved=False,
                reason=f"quantity {order_intent.quantity} exceeds max {self._max_notional}",
            )
        return RiskDecision(approved=True, reason=None)


def default_risk_check_factory(configuration: BacktestConfiguration) -> RiskCheck:
    params = configuration.strategy_params
    max_notional_str = params.get("max_notional")
    max_notional = Decimal(str(max_notional_str)) if max_notional_str is not None else None
    return ConfigurableRiskCheck(max_notional=max_notional)
