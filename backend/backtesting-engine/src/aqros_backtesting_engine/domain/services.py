"""Application services for deterministic backtest orchestration."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID, uuid4

from aqros_backtesting_engine.domain.calendar import session_days
from aqros_backtesting_engine.domain.commission import (
    CommissionModel,
    PctNotionalCommission,
    PerShareCommission,
    ZeroCommission,
)
from aqros_backtesting_engine.domain.fills import (
    FillModel,
    ImmediateFillModel,
    LiquidityCappedFillModel,
)
from aqros_backtesting_engine.domain.latency import (
    ConfigurableLatency,
    FixedLatency,
    LatencyModel,
    ZeroLatency,
)
from aqros_backtesting_engine.domain.metrics import (
    compute_benchmark_comparison,
    compute_drawdown,
    compute_performance_metrics,
    compute_risk_metrics,
)
from aqros_backtesting_engine.domain.models import (
    BacktestConfiguration,
    BacktestResult,
    BacktestRun,
    CashLedger,
    DrawdownSummary,
    EquityPoint,
    PerformanceMetrics,
    Portfolio,
    ResolvedModel,
    RiskMetrics,
    RunManifest,
    RunStatus,
)
from aqros_backtesting_engine.domain.ports import (
    BacktestRunRepository,
    CalendarProvider,
    FeatureStoreClient,
    MarketDataClient,
    ModelNotFoundError,
    ModelRegistryClient,
    ResultArtifactStore,
)
from aqros_backtesting_engine.domain.replay import build_event_stream
from aqros_backtesting_engine.domain.simulation import SimulationEngine
from aqros_backtesting_engine.domain.slippage import FixedBpsSlippage, SlippageModel, ZeroSlippage
from aqros_strategy_core import RiskCheck, Strategy


class StrategyFactory(Protocol):
    """Build the shared strategy using the resolved registry artifact."""

    def __call__(
        self,
        configuration: BacktestConfiguration,
        model: ResolvedModel,
        artifact: bytes,
    ) -> Strategy: ...


class RiskCheckFactory(Protocol):
    """Build the non-bypassable shared risk check for a run."""

    def __call__(self, configuration: BacktestConfiguration) -> RiskCheck: ...


class ExecutionModelFactory(Protocol):
    """Optional factory hook for custom execution models."""

    def __call__(self, parameters: Mapping[str, object]) -> Any: ...


class BacktestService:
    """Coordinate ports and pure domain components for one backtest run.

    The service owns orchestration only. Strategy/risk decisions remain in the
    shared strategy core; upstream data is obtained exclusively through the
    injected ports. Factories are injectable so adapters and tests can provide
    concrete strategy and execution implementations without domain imports.
    """

    def __init__(
        self,
        repository: BacktestRunRepository,
        market_data: MarketDataClient,
        model_registry: ModelRegistryClient,
        feature_store: FeatureStoreClient,
        calendar_provider: CalendarProvider,
        strategy_factory: Callable[[BacktestConfiguration, ResolvedModel, bytes], Strategy],
        risk_check_factory: Callable[[BacktestConfiguration], RiskCheck],
        *,
        engine_git_commit: str = "unknown",
        strategy_core_git_commit: str = "unknown",
        library_versions: Mapping[str, str] | None = None,
        feature_versions: Mapping[str, int] | None = None,
        strategy_model_outputs: Mapping[str, object] | None = None,
        latency_factory: Callable[[BacktestConfiguration], LatencyModel] | None = None,
        slippage_factory: Callable[[BacktestConfiguration], SlippageModel] | None = None,
        commission_factory: Callable[[BacktestConfiguration], CommissionModel] | None = None,
        fill_factory: Callable[[BacktestConfiguration], FillModel] | None = None,
    ) -> None:
        self.repository = repository
        self.market_data = market_data
        self.model_registry = model_registry
        self.feature_store = feature_store
        self.calendar_provider = calendar_provider
        self.strategy_factory = strategy_factory
        self.risk_check_factory = risk_check_factory
        self.engine_git_commit = engine_git_commit
        self.strategy_core_git_commit = strategy_core_git_commit
        self.library_versions = dict(library_versions or {})
        self.feature_versions = dict(feature_versions or {})
        self.strategy_model_outputs = dict(strategy_model_outputs or {})
        self.latency_factory = latency_factory
        self.slippage_factory = slippage_factory
        self.commission_factory = commission_factory
        self.fill_factory = fill_factory

    async def run(
        self, configuration: BacktestConfiguration, run_uuid: UUID | None = None
    ) -> BacktestResult:
        """Create and execute a run, returning its immutable result."""
        run_id = run_uuid or uuid4()
        stub = self._manifest_stub(run_id, configuration)
        await self.repository.create_run(configuration, stub, run_id)
        await self.repository.set_status(run_id, RunStatus.RUNNING)
        try:
            result, manifest = await self._execute(run_id, configuration)
            await self.repository.append_trade_log(run_id, list(result.trade_log))
            await self.repository.append_equity_points(run_id, list(result.equity_curve))
            await self.repository.write_result(run_id, result, manifest)
            await self.repository.set_status(run_id, RunStatus.COMPLETED)
            return result
        except Exception as exc:
            reason = self._failure_reason(exc)
            await self.repository.set_status(run_id, RunStatus.FAILED, reason)
            return self._failed_result(run_id, configuration, stub, reason)

    async def execute(
        self, configuration: BacktestConfiguration, run_uuid: UUID | None = None
    ) -> BacktestResult:
        """Compatibility spelling for callers that use an execution verb."""
        return await self.run(configuration, run_uuid)

    async def _execute(
        self, run_uuid: UUID, configuration: BacktestConfiguration
    ) -> tuple[BacktestResult, RunManifest]:
        model = await self._resolve_model(configuration)
        artifact = await self.model_registry.download_artifact(model.model_name, model.version)
        calendar_data = await self.calendar_provider.get_calendar(configuration.exchange)

        bars_by_symbol: dict[str, list[Any]] = {}
        actions_by_symbol: dict[str, list[Any]] = {}
        for symbol in configuration.universe:
            bars_by_symbol[symbol] = await self.market_data.get_bars(
                symbol, configuration.start, configuration.end, configuration.bar_interval
            )
            actions_by_symbol[symbol] = await self.market_data.get_corporate_actions(
                symbol, configuration.start, configuration.end, configuration.end
            )

        features_by_symbol: dict[str, dict[str, dict[datetime, float]]] = {}
        for symbol in configuration.universe:
            symbol_features: dict[str, dict[datetime, float]] = {}
            for feature_name, feature_version in self.feature_versions.items():
                values = await self.feature_store.get_feature_values(
                    symbol=symbol,
                    feature_name=feature_name,
                    feature_version=feature_version,
                    start=configuration.start,
                    end=configuration.end,
                    as_of=configuration.end,
                )
                time_index: dict[datetime, float] = {}
                for fv in values:
                    time_index[fv.event_time] = fv.value
                symbol_features[feature_name] = time_index
            if symbol_features:
                features_by_symbol[symbol] = symbol_features

        sample_days = tuple(
            session_days(calendar_data, configuration.start.date(), configuration.end.date())
        )
        events, _no_data = build_event_stream(
            bars_by_symbol,
            actions_by_symbol,
            calendar_data,
            sample_days,
            configuration.universe,
        )
        for symbol in sorted(actions_by_symbol):
            for action in actions_by_symbol[symbol]:
                if action.knowledge_time > action.event_time:
                    raise ValueError(
                        "look-ahead violation: corporate action "
                        f"{symbol} at {action.event_time.isoformat()} was known at "
                        f"{action.knowledge_time.isoformat()}"
                    )
        manifest = self._manifest(
            run_uuid, configuration, model, calendar_data.source, actions_by_symbol
        )
        strategy = self.strategy_factory(configuration, model, artifact)
        outcome = SimulationEngine(
            events=events,
            strategy=strategy,
            risk_check=self.risk_check_factory(configuration),
            latency_model=self._latency(configuration),
            slippage_model=self._slippage(configuration),
            commission_model=self._commission(configuration),
            fill_model=self._fill(configuration),
            starting_cash=configuration.starting_cash,
            leverage_enabled=configuration.leverage_enabled,
            max_leverage=configuration.max_leverage,
            seed=configuration.seed,
            features_by_symbol=features_by_symbol,
        ).run()

        equity_curve = list(outcome.equity_curve)
        if not equity_curve:
            equity_curve = [EquityPoint(configuration.start, configuration.starting_cash)]
        drawdown = compute_drawdown(equity_curve)
        performance = compute_performance_metrics(equity_curve, list(outcome.trade_log))
        latest_prices = self._latest_prices(bars_by_symbol)
        positions = {position.symbol: position for position in outcome.final_positions}
        risk = compute_risk_metrics(equity_curve, positions, latest_prices)
        benchmark = await self._benchmark(configuration, calendar_data, performance.total_return)
        final_time = equity_curve[-1].clock_time
        portfolio = Portfolio(
            cash=outcome.final_cash,
            positions=outcome.final_positions,
            as_of=final_time,
        )
        result = BacktestResult(
            run_uuid=run_uuid,
            manifest=manifest,
            status=RunStatus.COMPLETED,
            trade_log=outcome.trade_log,
            equity_curve=tuple(equity_curve),
            drawdown=drawdown,
            performance=performance,
            risk=risk,
            benchmark=benchmark,
            final_portfolio=portfolio,
            failure_reason=None,
        )
        return result, manifest

    async def _resolve_model(self, configuration: BacktestConfiguration) -> ResolvedModel:
        if configuration.model_version is None:
            return await self.model_registry.resolve_production(configuration.model_name)
        return await self.model_registry.get_version(
            configuration.model_name, configuration.model_version
        )

    async def _benchmark(
        self, configuration: BacktestConfiguration, calendar_data: Any, strategy_return: Decimal
    ) -> Any:
        if configuration.benchmark_symbol is None:
            return None
        bars = await self.market_data.get_bars(
            configuration.benchmark_symbol,
            configuration.start,
            configuration.end,
            configuration.bar_interval,
        )
        benchmark_curve = [
            EquityPoint(bar.event_time, bar.close)
            for bar in sorted(bars, key=lambda item: item.event_time)
        ]
        return compute_benchmark_comparison(
            configuration.benchmark_symbol, benchmark_curve, strategy_return
        )

    @staticmethod
    def _latest_prices(bars_by_symbol: Mapping[str, Sequence[Any]]) -> dict[str, Decimal]:
        return {
            symbol: sorted(bars, key=lambda bar: bar.event_time)[-1].close
            for symbol, bars in bars_by_symbol.items()
            if bars
        }

    def _latency(self, configuration: BacktestConfiguration) -> LatencyModel:
        if self.latency_factory is not None:
            return self.latency_factory(configuration)
        params = configuration.latency_params
        name = configuration.latency_model.lower()
        if name in {"zero", "zero_latency"}:
            return ZeroLatency()
        if name in {"fixed", "fixed_latency"}:
            return FixedLatency(timedelta(seconds=float(str(params.get("seconds", 0)))))
        return ConfigurableLatency(
            timedelta(seconds=float(str(params.get("min_seconds", 0)))),
            timedelta(seconds=float(str(params.get("max_seconds", 0)))),
        )

    def _slippage(self, configuration: BacktestConfiguration) -> SlippageModel:
        if self.slippage_factory is not None:
            return self.slippage_factory(configuration)
        if configuration.slippage_model.lower() in {"zero", "none", "zero_slippage"}:
            return ZeroSlippage()
        return FixedBpsSlippage(Decimal(str(configuration.slippage_params.get("bps", 0))))

    def _commission(self, configuration: BacktestConfiguration) -> CommissionModel:
        if self.commission_factory is not None:
            return self.commission_factory(configuration)
        name = configuration.commission_model.lower()
        params = configuration.commission_params
        if name in {"zero", "none", "zero_commission"}:
            return ZeroCommission()
        if name in {"per_share", "per-share"}:
            return PerShareCommission(Decimal(str(params.get("per_share", 0))))
        return PctNotionalCommission(Decimal(str(params.get("pct", 0))))

    def _fill(self, configuration: BacktestConfiguration) -> FillModel:
        if self.fill_factory is not None:
            return self.fill_factory(configuration)
        if configuration.fill_model.lower() in {"liquidity_capped", "liquidity-capped"}:
            return LiquidityCappedFillModel(
                Decimal(str(configuration.fill_params.get("max_participation_rate", "0.1")))
            )
        return ImmediateFillModel()

    def _manifest_stub(self, run_uuid: UUID, configuration: BacktestConfiguration) -> RunManifest:
        return RunManifest(
            run_uuid=run_uuid,
            engine_git_commit=self.engine_git_commit,
            strategy_core_git_commit=self.strategy_core_git_commit,
            configuration=configuration,
            resolved_models=(),
            feature_versions=self.feature_versions.copy(),
            universe=configuration.universe,
            period_start=configuration.start,
            period_end=configuration.end,
            knowledge_time_boundary=configuration.end,
            calendar_source="unresolved",
            price_adjustment_convention="as-provided-by-market-data",
            corporate_actions_applied=(),
            corporate_actions_unavailable=(),
            library_versions=self.library_versions.copy(),
            seed=configuration.seed,
        )

    def _manifest(
        self,
        run_uuid: UUID,
        configuration: BacktestConfiguration,
        model: ResolvedModel,
        calendar_source: str,
        actions_by_symbol: Mapping[str, Sequence[Any]],
    ) -> RunManifest:
        applied = tuple(
            f"{action.symbol}:{action.action_type.value}:{action.event_time.isoformat()}"
            for symbol in sorted(actions_by_symbol)
            for action in sorted(actions_by_symbol[symbol], key=lambda item: item.event_time)
        )
        return RunManifest(
            run_uuid=run_uuid,
            engine_git_commit=self.engine_git_commit,
            strategy_core_git_commit=self.strategy_core_git_commit,
            configuration=configuration,
            resolved_models=(model,),
            feature_versions=self.feature_versions.copy(),
            universe=configuration.universe,
            period_start=configuration.start,
            period_end=configuration.end,
            knowledge_time_boundary=configuration.end,
            calendar_source=calendar_source,
            price_adjustment_convention="as-provided-by-market-data",
            corporate_actions_applied=applied,
            corporate_actions_unavailable=tuple(
                sorted(symbol for symbol, actions in actions_by_symbol.items() if not actions)
            ),
            library_versions=self.library_versions.copy(),
            seed=configuration.seed,
        )

    @staticmethod
    def _failure_reason(error: Exception) -> str:
        if isinstance(error, ModelNotFoundError):
            return f"no approved model available: {error}".rstrip(": ")
        message = str(error).strip()
        return f"{error.__class__.__name__}: {message}" if message else error.__class__.__name__

    def _failed_result(
        self,
        run_uuid: UUID,
        configuration: BacktestConfiguration,
        manifest: RunManifest,
        reason: str,
    ) -> BacktestResult:
        point = EquityPoint(configuration.start, configuration.starting_cash)
        drawdown = DrawdownSummary(
            Decimal(0), configuration.start, configuration.start, timedelta(0)
        )
        performance = PerformanceMetrics(Decimal(0), Decimal(0), None, None, Decimal(0))
        risk = RiskMetrics(Decimal(0), Decimal(0), Decimal(0), Decimal(0), Decimal(0))
        return BacktestResult(
            run_uuid=run_uuid,
            manifest=manifest,
            status=RunStatus.FAILED,
            trade_log=(),
            equity_curve=(point,),
            drawdown=drawdown,
            performance=performance,
            risk=risk,
            benchmark=None,
            final_portfolio=Portfolio(
                CashLedger(configuration.starting_cash, configuration.starting_cash),
                (),
                configuration.start,
            ),
            failure_reason=reason,
        )


class BacktestQueryError(LookupError):
    """Base error for missing backtest query resources."""


class RunNotFoundError(BacktestQueryError):
    """Raised when a requested backtest run does not exist."""


class ResultNotFoundError(BacktestQueryError):
    """Raised when a requested run has no persisted result."""


class ManifestNotFoundError(BacktestQueryError):
    """Raised when a requested run has no persisted manifest."""


class BacktestQueryService:
    """Read-only application service for persisted backtest data."""

    def __init__(
        self,
        repository: BacktestRunRepository,
        artifact_store: ResultArtifactStore | None = None,
    ) -> None:
        self.repository = repository
        self.artifact_store = artifact_store

    async def get_run(self, run_uuid: UUID) -> BacktestRun:
        """Return a run's identity and status, or raise if it is unknown."""
        run = await self.repository.get_run(run_uuid)
        if run is None:
            raise RunNotFoundError(f"backtest run {run_uuid} was not found")
        return run

    async def status(self, run_uuid: UUID) -> RunStatus:
        """Return a run's status, or raise if the run is unknown."""
        return (await self.get_run(run_uuid)).status

    async def get_status(self, run_uuid: UUID) -> RunStatus:
        """Compatibility spelling for callers requesting a run status."""
        return await self.status(run_uuid)

    async def get_result(self, run_uuid: UUID) -> BacktestResult:
        """Return a persisted result, or raise a typed missing-resource error."""
        await self.get_run(run_uuid)
        result = await self.repository.get_result(run_uuid)
        if result is None:
            raise ResultNotFoundError(f"result for backtest run {run_uuid} was not found")
        return result

    async def get_manifest(self, run_uuid: UUID) -> RunManifest:
        """Return a persisted manifest, or raise a typed missing-resource error."""
        await self.get_run(run_uuid)
        manifest = await self.repository.get_manifest(run_uuid)
        if manifest is None:
            raise ManifestNotFoundError(f"manifest for backtest run {run_uuid} was not found")
        return manifest

    async def list_runs(
        self,
        strategy_id: str | None = None,
        model_name: str | None = None,
        status: RunStatus | None = None,
    ) -> list[BacktestRun]:
        """Return runs matching the supplied optional repository filters."""
        return await self.repository.list_runs(
            strategy_id=strategy_id,
            model_name=model_name,
            status=status,
        )


__all__ = [
    "BacktestQueryError",
    "BacktestQueryService",
    "BacktestService",
    "ExecutionModelFactory",
    "ManifestNotFoundError",
    "ResultNotFoundError",
    "RiskCheckFactory",
    "RunNotFoundError",
    "StrategyFactory",
]
