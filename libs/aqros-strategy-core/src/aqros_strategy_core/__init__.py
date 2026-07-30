"""AQROS shared strategy/risk/OMS contracts.

The single home for the `Strategy`, `RiskCheck`, and order/fill value types
shared unmodified across backtest, paper, and live execution (CLAUDE.md
§7.1). Contains **no business logic of its own** beyond these contracts —
consumers invoke them rather than reimplementing or forking them.
"""

from __future__ import annotations

from aqros_strategy_core.contracts import Fill, Order, OrderIntent, OrderSide, OrderType
from aqros_strategy_core.risk import RiskCheck, RiskDecision
from aqros_strategy_core.sizing import PositionSizer
from aqros_strategy_core.strategy import Strategy, StrategyContext

__version__ = "0.1.0"

__all__ = [
    "Fill",
    "Order",
    "OrderIntent",
    "OrderSide",
    "OrderType",
    "PositionSizer",
    "RiskCheck",
    "RiskDecision",
    "Strategy",
    "StrategyContext",
    "__version__",
]
