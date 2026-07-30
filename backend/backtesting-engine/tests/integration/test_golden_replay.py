"""Golden replay test: deterministic backtest with fixed input produces
identical output on every run. This is the foundational reproducibility
guarantee — if this test breaks, determinism is lost."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from aqros_backtesting_engine.adapters.calendar_provider import DefaultCalendarProvider
from aqros_backtesting_engine.adapters.risk_check_factory import default_risk_check_factory
from aqros_backtesting_engine.adapters.strategy_factory import default_strategy_factory
from aqros_backtesting_engine.domain.models import (
    AssetClass,
    BacktestConfiguration,
    Bar,
    Instrument,
    ResolvedModel,
)
from aqros_backtesting_engine.domain.ports import (
    BacktestRunRepository,
    FeatureStoreClient,
    MarketDataClient,
    ModelRegistryClient,
)
from aqros_backtesting_engine.domain.services import BacktestService


def _resolved_model() -> ResolvedModel:
    return ResolvedModel(
        model_name="test_model",
        version=1,
        checksum="abc123",
        checksum_algorithm="sha256",
        lineage={},
        resolved_as="production",
    )


def _config() -> BacktestConfiguration:
    return BacktestConfiguration(
        strategy_id="golden-test",
        strategy_params={"signal_threshold": "0.1", "order_quantity": "10"},
        model_name="test_model",
        model_version=None,
        universe=("AAPL",),
        exchange="NYSE",
        start=datetime(2024, 1, 2, 14, 30, tzinfo=UTC),
        end=datetime(2024, 1, 5, 21, 0, tzinfo=UTC),
        starting_cash=Decimal("100000"),
        bar_interval="daily",
        slippage_model="zero",
        slippage_params={},
        commission_model="zero",
        commission_params={},
        fill_model="immediate",
        fill_params={},
        latency_model="zero",
        latency_params={},
        leverage_enabled=False,
        max_leverage=Decimal("1.0"),
        equity_sample_interval="daily",
        benchmark_symbol=None,
        seed=42,
        asset_class=AssetClass.EQUITY,
    )


def _bars() -> dict[str, list[Bar]]:
    return {
        "AAPL": [
            Bar(
                "AAPL",
                datetime(2024, 1, 2, 14, 30, tzinfo=UTC),
                datetime(2024, 1, 2, 21, 0, tzinfo=UTC),
                Decimal("180"),
                Decimal("185"),
                Decimal("179"),
                Decimal("183"),
                Decimal("1000000"),
            ),
            Bar(
                "AAPL",
                datetime(2024, 1, 3, 14, 30, tzinfo=UTC),
                datetime(2024, 1, 3, 21, 0, tzinfo=UTC),
                Decimal("183"),
                Decimal("186"),
                Decimal("180"),
                Decimal("181"),
                Decimal("900000"),
            ),
            Bar(
                "AAPL",
                datetime(2024, 1, 4, 14, 30, tzinfo=UTC),
                datetime(2024, 1, 4, 21, 0, tzinfo=UTC),
                Decimal("181"),
                Decimal("184"),
                Decimal("178"),
                Decimal("182"),
                Decimal("1100000"),
            ),
            Bar(
                "AAPL",
                datetime(2024, 1, 5, 14, 30, tzinfo=UTC),
                datetime(2024, 1, 5, 21, 0, tzinfo=UTC),
                Decimal("182"),
                Decimal("190"),
                Decimal("181"),
                Decimal("189"),
                Decimal("1500000"),
            ),
        ],
    }


def _make_market_data_mock() -> AsyncMock:
    bars = _bars()
    mock = AsyncMock(spec=MarketDataClient)
    mock.get_bars = AsyncMock(side_effect=lambda symbol, start, end, interval: bars.get(symbol, []))
    mock.get_instrument = AsyncMock(return_value=Instrument("AAPL", AssetClass.EQUITY, "NYSE"))
    mock.get_corporate_actions = AsyncMock(return_value=[])
    return mock


def _make_model_registry_mock() -> AsyncMock:
    mock = AsyncMock(spec=ModelRegistryClient)
    mock.resolve_production = AsyncMock(return_value=_resolved_model())
    mock.get_version = AsyncMock(return_value=_resolved_model())
    mock.download_artifact = AsyncMock(return_value=b"model-artifact")
    mock.publish_result = AsyncMock(return_value={"artifact_id": "golden-test", "version": 1})
    return mock


def _make_feature_store_mock() -> AsyncMock:
    mock = AsyncMock(spec=FeatureStoreClient)
    mock.get_feature_values = AsyncMock(return_value=[])
    return mock


class _MockRepository(BacktestRunRepository):
    def __init__(self) -> None:
        self.runs: dict[UUID, Any] = {}
        self.statuses: dict[UUID, Any] = {}
        self.results: dict[UUID, Any] = {}

    async def create_run(self, config: Any, manifest_stub: Any, run_uuid: UUID) -> None:
        self.runs[run_uuid] = {"config": config, "manifest": manifest_stub}

    async def set_status(
        self, run_uuid: UUID, status: Any, failure_reason: str | None = None
    ) -> None:
        self.statuses[run_uuid] = {"status": status, "reason": failure_reason}

    async def append_trade_log(self, run_uuid: UUID, entries: Any) -> None:
        pass

    async def append_equity_points(self, run_uuid: UUID, points: Any) -> None:
        pass

    async def write_result(self, run_uuid: UUID, result: Any, manifest: Any) -> None:
        self.results[run_uuid] = {"result": result, "manifest": manifest}

    async def get_run(self, run_uuid: UUID) -> Any:
        return None

    async def get_result(self, run_uuid: UUID) -> Any:
        return None

    async def get_manifest(self, run_uuid: UUID) -> Any:
        return None

    async def list_runs(self, **kwargs: Any) -> list[Any]:
        return []


def _run_checksum(result: Any) -> str:
    fields = (
        str(result.run_uuid),
        result.status.value,
        str(result.performance.sharpe_ratio),
        str(result.performance.total_return),
        str(len(result.equity_curve)),
        str(len(result.trade_log)),
    )
    return hashlib.sha256("|".join(fields).encode()).hexdigest()


class TestGoldenReplay:
    async def test_deterministic_result(self) -> None:
        service = BacktestService(
            repository=_MockRepository(),
            market_data=_make_market_data_mock(),
            model_registry=_make_model_registry_mock(),
            feature_store=_make_feature_store_mock(),
            calendar_provider=DefaultCalendarProvider(start_year=2024, end_year=2024),
            strategy_factory=default_strategy_factory,
            risk_check_factory=default_risk_check_factory,
        )
        config = _config()
        result1 = await service.run(config, run_uuid=uuid4())
        result2 = await service.run(config, run_uuid=uuid4())
        assert result1.status == result2.status
        assert len(result1.equity_curve) == len(result2.equity_curve)
        assert len(result1.trade_log) == len(result2.trade_log)
        assert result1.performance.sharpe_ratio == result2.performance.sharpe_ratio
        assert result1.performance.total_return == result2.performance.total_return

    async def test_same_config_same_checksum(self) -> None:
        service = BacktestService(
            repository=_MockRepository(),
            market_data=_make_market_data_mock(),
            model_registry=_make_model_registry_mock(),
            feature_store=_make_feature_store_mock(),
            calendar_provider=DefaultCalendarProvider(start_year=2024, end_year=2024),
            strategy_factory=default_strategy_factory,
            risk_check_factory=default_risk_check_factory,
        )
        config = _config()
        fixed_uuid = UUID("00000000-0000-0000-0000-000000000001")
        r1 = await service.run(config, run_uuid=fixed_uuid)
        r2 = await service.run(config, run_uuid=fixed_uuid)
        assert _run_checksum(r1) == _run_checksum(r2)

    async def test_fails_without_market_data(self) -> None:
        mock_md = _make_market_data_mock()
        mock_md.get_bars = AsyncMock(return_value=[])
        mock_md.get_corporate_actions = AsyncMock(return_value=[])
        service = BacktestService(
            repository=_MockRepository(),
            market_data=mock_md,
            model_registry=_make_model_registry_mock(),
            feature_store=_make_feature_store_mock(),
            calendar_provider=DefaultCalendarProvider(start_year=2024, end_year=2024),
            strategy_factory=default_strategy_factory,
            risk_check_factory=default_risk_check_factory,
        )
        config = _config()
        result = await service.run(config, run_uuid=uuid4())
        assert result.status.value == "completed"

    async def test_benchmark_results_are_deterministic(self) -> None:
        bars = _bars()
        mock_md = _make_market_data_mock()
        config = _config()
        config_with_benchmark = BacktestConfiguration(
            strategy_id=config.strategy_id,
            strategy_params=config.strategy_params,
            model_name=config.model_name,
            model_version=config.model_version,
            universe=config.universe,
            exchange=config.exchange,
            start=config.start,
            end=config.end,
            starting_cash=config.starting_cash,
            bar_interval=config.bar_interval,
            slippage_model=config.slippage_model,
            slippage_params=config.slippage_params,
            commission_model=config.commission_model,
            commission_params=config.commission_params,
            fill_model=config.fill_model,
            fill_params=config.fill_params,
            latency_model=config.latency_model,
            latency_params=config.latency_params,
            leverage_enabled=config.leverage_enabled,
            max_leverage=config.max_leverage,
            equity_sample_interval=config.equity_sample_interval,
            benchmark_symbol="SPY",
            seed=config.seed,
        )
        mock_md.get_bars = AsyncMock(
            side_effect=lambda symbol, start, end, interval: (
                bars.get(symbol, [])
                if symbol == "AAPL"
                else [
                    Bar(
                        "SPY",
                        datetime(2024, 1, 2, 14, 30, tzinfo=UTC),
                        datetime(2024, 1, 2, 21, 0, tzinfo=UTC),
                        Decimal("470"),
                        Decimal("475"),
                        Decimal("468"),
                        Decimal("473"),
                        Decimal("50000000"),
                    )
                ]
            )
        )
        service = BacktestService(
            repository=_MockRepository(),
            market_data=mock_md,
            model_registry=_make_model_registry_mock(),
            feature_store=_make_feature_store_mock(),
            calendar_provider=DefaultCalendarProvider(start_year=2024, end_year=2024),
            strategy_factory=default_strategy_factory,
            risk_check_factory=default_risk_check_factory,
        )
        fixed_uuid = UUID("00000000-0000-0000-0000-000000000002")
        r1 = await service.run(config_with_benchmark, run_uuid=fixed_uuid)
        r2 = await service.run(config_with_benchmark, run_uuid=fixed_uuid)
        assert _run_checksum(r1) == _run_checksum(r2)
