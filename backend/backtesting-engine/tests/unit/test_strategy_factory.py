from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from aqros_backtesting_engine.adapters.strategy_factory import (
    SignalFollowingStrategy,
    default_strategy_factory,
)
from aqros_backtesting_engine.domain.models import AssetClass, BacktestConfiguration, ResolvedModel

from aqros_strategy_core.strategy import StrategyContext


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


def _model() -> ResolvedModel:
    return ResolvedModel("test_model", 1, "abc", "sha256", {}, "production")


class TestSignalFollowingStrategy:
    async def test_positive_signal_emits_buy(self) -> None:
        strategy = SignalFollowingStrategy(threshold=Decimal("0"), quantity=Decimal("10"))
        ctx = StrategyContext(
            as_of=datetime(2024, 1, 1, tzinfo=UTC),
            market_data={"AAPL": object()},
            model_outputs={"signal": 0.5},
        )
        intents = strategy.on_event(ctx)
        assert len(intents) == 1
        assert intents[0].side.value == "buy"
        assert intents[0].quantity == Decimal("10")

    async def test_negative_signal_emits_sell(self) -> None:
        strategy = SignalFollowingStrategy(threshold=Decimal("0"), quantity=Decimal("10"))
        ctx = StrategyContext(
            as_of=datetime(2024, 1, 1, tzinfo=UTC),
            market_data={"AAPL": object()},
            model_outputs={"signal": -0.5},
        )
        intents = strategy.on_event(ctx)
        assert len(intents) == 1
        assert intents[0].side.value == "sell"

    async def test_signal_below_threshold_no_order(self) -> None:
        strategy = SignalFollowingStrategy(threshold=Decimal("1.0"), quantity=Decimal("10"))
        ctx = StrategyContext(
            as_of=datetime(2024, 1, 1, tzinfo=UTC),
            market_data={"AAPL": object()},
            model_outputs={"signal": 0.5},
        )
        intents = strategy.on_event(ctx)
        assert len(intents) == 0

    async def test_no_signal_no_order(self) -> None:
        strategy = SignalFollowingStrategy()
        ctx = StrategyContext(
            as_of=datetime(2024, 1, 1, tzinfo=UTC),
            market_data={"AAPL": object()},
            model_outputs={},
        )
        intents = strategy.on_event(ctx)
        assert len(intents) == 0

    async def test_none_signal_no_order(self) -> None:
        strategy = SignalFollowingStrategy()
        ctx = StrategyContext(
            as_of=datetime(2024, 1, 1, tzinfo=UTC),
            market_data={"AAPL": object()},
            model_outputs={"signal": None},
        )
        intents = strategy.on_event(ctx)
        assert len(intents) == 0

    async def test_each_intent_gets_unique_id(self) -> None:
        strategy = SignalFollowingStrategy(threshold=Decimal("0"), quantity=Decimal("1"))
        ctx1 = StrategyContext(
            as_of=datetime(2024, 1, 1, tzinfo=UTC),
            market_data={"AAPL": object()},
            model_outputs={"signal": 1.0},
        )
        ctx2 = StrategyContext(
            as_of=datetime(2024, 1, 2, tzinfo=UTC),
            market_data={"AAPL": object()},
            model_outputs={"signal": 1.0},
        )
        id1 = strategy.on_event(ctx1)[0].client_order_id
        id2 = strategy.on_event(ctx2)[0].client_order_id
        assert id1 != id2

    async def test_unknown_symbol_when_no_market_data(self) -> None:
        strategy = SignalFollowingStrategy(threshold=Decimal("0"), quantity=Decimal("1"))
        ctx = StrategyContext(
            as_of=datetime(2024, 1, 1, tzinfo=UTC),
            market_data={},
            model_outputs={"signal": 1.0},
        )
        intents = strategy.on_event(ctx)
        assert len(intents) == 1
        assert intents[0].symbol == "UNKNOWN"


class TestDefaultStrategyFactory:
    async def test_creates_strategy_with_defaults(self) -> None:
        strategy = default_strategy_factory(_config(), _model(), b"")
        assert isinstance(strategy, SignalFollowingStrategy)

    async def test_creates_strategy_with_custom_threshold(self) -> None:
        strategy = default_strategy_factory(
            _config(strategy_params={"signal_threshold": "0.5"}),
            _model(),
            b"",
        )
        ctx = StrategyContext(
            as_of=datetime(2024, 1, 1, tzinfo=UTC),
            market_data={"AAPL": object()},
            model_outputs={"signal": 0.3},
        )
        assert len(strategy.on_event(ctx)) == 0

    async def test_creates_strategy_with_custom_quantity(self) -> None:
        strategy = default_strategy_factory(
            _config(strategy_params={"order_quantity": "500"}),
            _model(),
            b"",
        )
        ctx = StrategyContext(
            as_of=datetime(2024, 1, 1, tzinfo=UTC),
            market_data={"AAPL": object()},
            model_outputs={"signal": 1.0},
        )
        intents = strategy.on_event(ctx)
        assert intents[0].quantity == Decimal("500")

    async def test_ignores_artifact_bytes(self) -> None:
        strategy = default_strategy_factory(_config(), _model(), b"some-artifact-data")
        assert isinstance(strategy, SignalFollowingStrategy)

    async def test_ignores_model_metadata(self) -> None:
        model = ResolvedModel("other", 5, "xyz", "md5", {"key": "val"}, "pinned")
        strategy = default_strategy_factory(_config(), model, b"")
        assert isinstance(strategy, SignalFollowingStrategy)
