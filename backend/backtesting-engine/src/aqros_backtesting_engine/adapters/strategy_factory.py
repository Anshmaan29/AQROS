from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from aqros_backtesting_engine.domain.models import BacktestConfiguration, ResolvedModel
from aqros_strategy_core import Strategy, StrategyContext
from aqros_strategy_core.contracts import OrderIntent, OrderSide, OrderType


class SignalFollowingStrategy:
    def __init__(
        self, threshold: Decimal = Decimal("0"), quantity: Decimal = Decimal("100")
    ) -> None:
        self._threshold = threshold
        self._quantity = quantity

    def on_event(self, context: StrategyContext) -> list[OrderIntent]:
        signal = context.model_outputs.get("signal")
        if signal is None:
            return []
        try:
            signal_val = float(str(signal))
        except (ValueError, TypeError):
            return []
        if abs(signal_val) < float(self._threshold):
            return []
        side = OrderSide.BUY if signal_val > 0 else OrderSide.SELL
        symbol = next(iter(context.market_data), "UNKNOWN")
        return [
            OrderIntent(
                client_order_id=str(uuid4()),
                symbol=symbol,
                side=side,
                order_type=OrderType.MARKET,
                quantity=self._quantity,
                limit_price=None,
                emitted_at=context.as_of,
            )
        ]


def default_strategy_factory(
    configuration: BacktestConfiguration,
    model: ResolvedModel,
    artifact: bytes,
) -> Strategy:
    params = configuration.strategy_params
    threshold = Decimal(str(params.get("signal_threshold", "0")))
    quantity = Decimal(str(params.get("order_quantity", "100")))
    return SignalFollowingStrategy(threshold=threshold, quantity=quantity)
