"""SQLAlchemy repository for persisted backtest runs and results."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aqros_backtesting_engine.adapters.orm import (
    BacktestResultORM,
    BacktestRunORM,
    EquityPointORM,
    TradeLogEntryORM,
)
from aqros_backtesting_engine.domain.models import (
    AssetClass,
    BacktestConfiguration,
    BacktestResult,
    BacktestRun,
    BenchmarkComparison,
    CashLedger,
    DrawdownSummary,
    EquityPoint,
    OrderSide,
    OrderStatus,
    OrderType,
    PerformanceMetrics,
    Portfolio,
    Position,
    RiskMetrics,
    RunManifest,
    RunStatus,
    TradeLogEntry,
)
from aqros_backtesting_engine.domain.ports import (
    BacktestResultAlreadyExistsError,
    BacktestRunRepository,
)


def _json_value(value: Any) -> Any:
    """Convert domain values to JSON-compatible values without losing types."""
    if isinstance(value, Decimal):
        return {"__decimal__": str(value)}
    if isinstance(value, datetime):
        return {"__datetime__": value.isoformat()}
    if isinstance(value, timedelta):
        return {"__timedelta__": value.total_seconds()}
    if isinstance(value, UUID):
        return {"__uuid__": str(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _dump(value: Any) -> dict[str, object]:
    encoded = _json_value(value)
    if not isinstance(encoded, dict):
        raise TypeError("repository JSON root must be an object")
    return encoded


def _load(value: Any) -> Any:
    if isinstance(value, list):
        return [_load(item) for item in value]
    if isinstance(value, dict):
        if set(value) == {"__decimal__"}:
            return Decimal(str(value["__decimal__"]))
        if set(value) == {"__datetime__"}:
            return datetime.fromisoformat(str(value["__datetime__"]))
        if set(value) == {"__timedelta__"}:
            return timedelta(seconds=float(value["__timedelta__"]))
        if set(value) == {"__uuid__"}:
            return UUID(str(value["__uuid__"]))
        return {str(key): _load(item) for key, item in value.items()}
    return value


def _configuration_from_json(data: dict[str, Any]) -> BacktestConfiguration:
    value = _load(data)
    value["universe"] = tuple(value["universe"])
    value["asset_class"] = AssetClass(value["asset_class"])
    return BacktestConfiguration(**value)


def _manifest_to_json(manifest: RunManifest) -> dict[str, object]:
    return _dump(asdict(manifest))


def _manifest_from_json(data: dict[str, Any]) -> RunManifest:
    value = _load(data)
    value["run_uuid"] = UUID(str(value["run_uuid"]))
    value["configuration"] = _configuration_from_json(value["configuration"])
    value["resolved_models"] = tuple(
        # ResolvedModel is reconstructed through its manifest field below.
        _resolved_model_from_json(item)
        for item in value["resolved_models"]
    )
    value["universe"] = tuple(value["universe"])
    value["corporate_actions_applied"] = tuple(value["corporate_actions_applied"])
    value["corporate_actions_unavailable"] = tuple(value["corporate_actions_unavailable"])
    return RunManifest(**value)


def _resolved_model_from_json(data: dict[str, Any]) -> Any:
    from aqros_backtesting_engine.domain.models import ResolvedModel

    return ResolvedModel(**_load(data))


def _to_domain_run(row: BacktestRunORM) -> BacktestRun:
    return BacktestRun(
        run_uuid=row.run_uuid,
        strategy_id=row.strategy_id,
        model_name=row.model_name,
        model_version=row.model_version,
        status=RunStatus(row.status),
        created_at=row.created_at,
        completed_at=row.completed_at,
        failure_reason=row.failure_reason,
    )


def _to_domain_trade(row: TradeLogEntryORM) -> TradeLogEntry:
    return TradeLogEntry(
        sequence=row.sequence,
        client_order_id=row.client_order_id,
        symbol=row.symbol,
        side=OrderSide(row.side),
        order_type=OrderType(row.order_type),
        quantity=Decimal(row.quantity),
        price=None if row.price is None else Decimal(row.price),
        commission=Decimal(row.commission),
        clock_time=row.clock_time,
        outcome=OrderStatus(row.outcome),
        reason=row.reason,
    )


def _to_domain_equity(row: EquityPointORM) -> EquityPoint:
    return EquityPoint(clock_time=row.clock_time, total_value=Decimal(row.total_value))


def _portfolio_from_json(data: dict[str, Any]) -> Portfolio:
    value = _load(data)
    cash = value["cash"]
    return Portfolio(
        cash=CashLedger(**cash),
        positions=tuple(Position(**item) for item in value["positions"]),
        as_of=value["as_of"],
    )


def _result_from_row(
    row: BacktestResultORM,
    manifest: RunManifest,
    status: RunStatus,
    trade_log: tuple[TradeLogEntry, ...],
    equity_curve: tuple[EquityPoint, ...],
    failure_reason: str | None,
) -> BacktestResult:
    performance_data = _load(row.performance_json)
    risk_data = _load(row.risk_json)
    drawdown_data = _load(row.drawdown_json)
    benchmark_data = None if row.benchmark_json is None else _load(row.benchmark_json)
    return BacktestResult(
        run_uuid=row.run_uuid,
        manifest=manifest,
        status=status,
        trade_log=trade_log,
        equity_curve=equity_curve,
        drawdown=DrawdownSummary(**drawdown_data),
        performance=PerformanceMetrics(**performance_data),
        risk=RiskMetrics(**risk_data),
        benchmark=None if benchmark_data is None else BenchmarkComparison(**benchmark_data),
        final_portfolio=_portfolio_from_json(row.final_portfolio_json),
        failure_reason=failure_reason,
    )


class SqlAlchemyBacktestRunRepository(BacktestRunRepository):
    """Postgres-backed repository that never commits its injected session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_run(
        self, config: BacktestConfiguration, manifest_stub: RunManifest, run_uuid: UUID
    ) -> None:
        row = BacktestRunORM(
            run_uuid=run_uuid,
            strategy_id=config.strategy_id,
            model_name=config.model_name,
            model_version=config.model_version,
            status=RunStatus.PENDING.value,
            config_json=_dump(asdict(config)),
            manifest_json=_manifest_to_json(manifest_stub),
            created_at=datetime.now(UTC),
        )
        self._session.add(row)
        await self._session.flush()

    async def set_status(
        self, run_uuid: UUID, status: RunStatus, failure_reason: str | None = None
    ) -> None:
        row = await self._run_row(run_uuid)
        row.status = status.value
        row.failure_reason = failure_reason
        if status in (RunStatus.COMPLETED, RunStatus.FAILED):
            row.completed_at = datetime.now(UTC)
        await self._session.flush()

    async def append_trade_log(self, run_uuid: UUID, entries: list[TradeLogEntry]) -> None:
        for entry in entries:
            self._session.add(
                TradeLogEntryORM(
                    run_uuid=run_uuid,
                    sequence=entry.sequence,
                    client_order_id=entry.client_order_id,
                    symbol=entry.symbol,
                    side=entry.side.value,
                    order_type=entry.order_type.value,
                    quantity=entry.quantity,
                    price=entry.price,
                    commission=entry.commission,
                    clock_time=entry.clock_time,
                    outcome=entry.outcome.value,
                    reason=entry.reason,
                )
            )
        await self._session.flush()

    async def append_equity_points(self, run_uuid: UUID, points: list[EquityPoint]) -> None:
        for point in points:
            self._session.add(
                EquityPointORM(
                    run_uuid=run_uuid,
                    clock_time=point.clock_time,
                    total_value=point.total_value,
                )
            )
        await self._session.flush()

    async def write_result(
        self, run_uuid: UUID, result: BacktestResult, manifest: RunManifest
    ) -> None:
        existing = await self._session.execute(
            select(BacktestResultORM).where(BacktestResultORM.run_uuid == run_uuid)
        )
        if existing.scalar_one_or_none() is not None:
            raise BacktestResultAlreadyExistsError(
                f"a result already exists for backtest run {run_uuid}"
            )
        run = await self._run_row(run_uuid)
        run.manifest_json = _manifest_to_json(manifest)
        self._session.add(
            BacktestResultORM(
                run_uuid=run_uuid,
                performance_json=_dump(asdict(result.performance)),
                risk_json=_dump(asdict(result.risk)),
                drawdown_json=_dump(asdict(result.drawdown)),
                benchmark_json=(
                    None if result.benchmark is None else _dump(asdict(result.benchmark))
                ),
                final_portfolio_json=_dump(asdict(result.final_portfolio)),
                written_at=datetime.now(UTC),
            )
        )
        await self._session.flush()

    async def get_run(self, run_uuid: UUID) -> BacktestRun | None:
        result = await self._session.execute(
            select(BacktestRunORM).where(BacktestRunORM.run_uuid == run_uuid)
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_domain_run(row)

    async def get_result(self, run_uuid: UUID) -> BacktestResult | None:
        result = await self._session.execute(
            select(BacktestResultORM).where(BacktestResultORM.run_uuid == run_uuid)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        run = await self._run_row(run_uuid)
        manifest = _manifest_from_json(run.manifest_json)
        trades_result = await self._session.execute(
            select(TradeLogEntryORM)
            .where(TradeLogEntryORM.run_uuid == run_uuid)
            .order_by(TradeLogEntryORM.sequence.asc(), TradeLogEntryORM.id.asc())
        )
        equity_result = await self._session.execute(
            select(EquityPointORM)
            .where(EquityPointORM.run_uuid == run_uuid)
            .order_by(EquityPointORM.clock_time.asc(), EquityPointORM.id.asc())
        )
        trades = tuple(_to_domain_trade(item) for item in trades_result.scalars().all())
        equity = tuple(_to_domain_equity(item) for item in equity_result.scalars().all())
        return _result_from_row(
            row, manifest, RunStatus(run.status), trades, equity, run.failure_reason
        )

    async def get_manifest(self, run_uuid: UUID) -> RunManifest | None:
        result = await self._session.execute(
            select(BacktestRunORM).where(BacktestRunORM.run_uuid == run_uuid)
        )
        row = result.scalar_one_or_none()
        return None if row is None else _manifest_from_json(row.manifest_json)

    async def list_runs(
        self,
        *,
        strategy_id: str | None = None,
        model_name: str | None = None,
        status: RunStatus | None = None,
    ) -> list[BacktestRun]:
        stmt = select(BacktestRunORM)
        if strategy_id is not None:
            stmt = stmt.where(BacktestRunORM.strategy_id == strategy_id)
        if model_name is not None:
            stmt = stmt.where(BacktestRunORM.model_name == model_name)
        if status is not None:
            stmt = stmt.where(BacktestRunORM.status == status.value)
        stmt = stmt.order_by(BacktestRunORM.created_at.asc(), BacktestRunORM.id.asc())
        result = await self._session.execute(stmt)
        return [_to_domain_run(row) for row in result.scalars().all()]

    async def _run_row(self, run_uuid: UUID) -> BacktestRunORM:
        result = await self._session.execute(
            select(BacktestRunORM).where(BacktestRunORM.run_uuid == run_uuid)
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise ValueError(f"no backtest run persisted for {run_uuid}")
        return row


__all__ = ["SqlAlchemyBacktestRunRepository"]
