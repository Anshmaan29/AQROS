from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from aqros_backtesting_engine.adapters.report_signer import generate_report, sign_report
from aqros_backtesting_engine.api.deps import (
    get_backtest_query_service,
    get_backtest_service,
)
from aqros_backtesting_engine.domain.models import (
    AssetClass,
    BacktestConfiguration,
    RunStatus,
)
from aqros_backtesting_engine.domain.services import (
    BacktestQueryService,
    BacktestService,
    ManifestNotFoundError,
    ResultNotFoundError,
    RunNotFoundError,
)
from aqros_backtesting_engine.domain.validation import run_validation_gauntlet

router = APIRouter(prefix="/v1/backtests", tags=["backtests"])


class BacktestConfigRequest(BaseModel):
    strategy_id: str
    strategy_params: dict[str, Any] = Field(default_factory=dict)
    model_name: str
    model_version: int | None = None
    universe: list[str]
    exchange: str = "NYSE"
    start: datetime
    end: datetime
    starting_cash: Decimal = Decimal("100000")
    bar_interval: str = "daily"
    slippage_model: str = "zero"
    slippage_params: dict[str, Any] = Field(default_factory=dict)
    commission_model: str = "zero"
    commission_params: dict[str, Any] = Field(default_factory=dict)
    fill_model: str = "immediate"
    fill_params: dict[str, Any] = Field(default_factory=dict)
    latency_model: str = "zero"
    latency_params: dict[str, Any] = Field(default_factory=dict)
    leverage_enabled: bool = False
    max_leverage: Decimal = Decimal("1.0")
    equity_sample_interval: str = "daily"
    benchmark_symbol: str | None = None
    seed: int | None = None


class RunStatusResponse(BaseModel):
    run_uuid: str
    strategy_id: str
    model_name: str
    model_version: int | None
    status: str
    created_at: datetime
    completed_at: datetime | None
    failure_reason: str | None


class RunResultResponse(BaseModel):
    run_uuid: str
    status: str
    performance: dict[str, Any]
    risk: dict[str, Any]
    drawdown: dict[str, Any]
    benchmark: dict[str, Any] | None
    trade_log_count: int
    equity_curve_points: int
    failure_reason: str | None


class ValidationResponse(BaseModel):
    pbo: float
    dsr: float
    cpcv_mean_return: float
    cpcv_std_return: float
    num_trials: int


class SignatureResponse(BaseModel):
    run_uuid: str
    signature: str
    report: dict[str, Any]


def _config_from_request(req: BacktestConfigRequest) -> BacktestConfiguration:
    return BacktestConfiguration(
        strategy_id=req.strategy_id,
        strategy_params=req.strategy_params,
        model_name=req.model_name,
        model_version=req.model_version,
        universe=tuple(req.universe),
        exchange=req.exchange,
        start=req.start,
        end=req.end,
        starting_cash=req.starting_cash,
        bar_interval=req.bar_interval,
        slippage_model=req.slippage_model,
        slippage_params=req.slippage_params,
        commission_model=req.commission_model,
        commission_params=req.commission_params,
        fill_model=req.fill_model,
        fill_params=req.fill_params,
        latency_model=req.latency_model,
        latency_params=req.latency_params,
        leverage_enabled=req.leverage_enabled,
        max_leverage=req.max_leverage,
        equity_sample_interval=req.equity_sample_interval,
        benchmark_symbol=req.benchmark_symbol,
        seed=req.seed if req.seed is not None else 42,
        asset_class=AssetClass.EQUITY,
    )


def _run_to_response(run: Any) -> RunStatusResponse:
    return RunStatusResponse(
        run_uuid=str(run.run_uuid),
        strategy_id=run.strategy_id,
        model_name=run.model_name,
        model_version=run.model_version,
        status=run.status.value,
        created_at=run.created_at,
        completed_at=run.completed_at,
        failure_reason=run.failure_reason,
    )


@router.post("", status_code=201)
async def run_backtest(
    request: BacktestConfigRequest,
    service: BacktestService = Depends(get_backtest_service),
) -> RunStatusResponse:
    config = _config_from_request(request)
    result = await service.run(config)
    return RunStatusResponse(
        run_uuid=str(result.run_uuid),
        strategy_id=config.strategy_id,
        model_name=config.model_name,
        model_version=config.model_version,
        status=result.status.value,
        created_at=config.start,
        completed_at=config.end,
        failure_reason=result.failure_reason,
    )


@router.get("")
async def list_backtests(
    strategy_id: str | None = None,
    model_name: str | None = None,
    status: str | None = None,
    query_service: BacktestQueryService = Depends(get_backtest_query_service),
) -> list[RunStatusResponse]:
    parsed_status = RunStatus(status) if status else None
    runs = await query_service.list_runs(
        strategy_id=strategy_id,
        model_name=model_name,
        status=parsed_status,
    )
    return [_run_to_response(r) for r in runs]


@router.get("/{run_uuid}")
async def get_backtest(
    run_uuid: UUID,
    query_service: BacktestQueryService = Depends(get_backtest_query_service),
) -> RunStatusResponse:
    try:
        run = await query_service.get_run(run_uuid)
    except RunNotFoundError:
        raise HTTPException(status_code=404, detail="backtest run not found") from None
    return _run_to_response(run)


@router.get("/{run_uuid}/result")
async def get_backtest_result(
    run_uuid: UUID,
    query_service: BacktestQueryService = Depends(get_backtest_query_service),
) -> RunResultResponse:
    try:
        result = await query_service.get_result(run_uuid)
    except (RunNotFoundError, ResultNotFoundError):
        raise HTTPException(status_code=404, detail="backtest result not found") from None
    perf = asdict(result.performance)
    risk = asdict(result.risk)
    dd = asdict(result.drawdown)
    return RunResultResponse(
        run_uuid=str(run_uuid),
        status=result.status.value,
        performance={k: (float(v) if isinstance(v, Decimal) else v) for k, v in perf.items()},
        risk={k: (float(v) if isinstance(v, Decimal) else v) for k, v in risk.items()},
        drawdown={k: (float(v) if isinstance(v, Decimal) else v) for k, v in dd.items()},
        benchmark=asdict(result.benchmark) if result.benchmark else None,
        trade_log_count=len(result.trade_log),
        equity_curve_points=len(result.equity_curve),
        failure_reason=result.failure_reason,
    )


@router.post("/{run_uuid}/sign")
async def sign_backtest_report(
    run_uuid: UUID,
    query_service: BacktestQueryService = Depends(get_backtest_query_service),
) -> SignatureResponse:
    try:
        result = await query_service.get_result(run_uuid)
    except (RunNotFoundError, ResultNotFoundError):
        raise HTTPException(status_code=404, detail="backtest result not found") from None
    report = generate_report(result)
    signing_key = b"default-signing-key"
    signature = sign_report(report, signing_key)
    return SignatureResponse(
        run_uuid=str(run_uuid),
        signature=signature,
        report=report,
    )


@router.post("/{run_uuid}/validate")
async def validate_backtest(
    run_uuid: UUID,
    num_trials: int = 10,
    query_service: BacktestQueryService = Depends(get_backtest_query_service),
    run_service: BacktestService = Depends(get_backtest_service),
) -> ValidationResponse:
    try:
        manifest = await query_service.get_manifest(run_uuid)
    except (RunNotFoundError, ManifestNotFoundError):
        raise HTTPException(status_code=404, detail="backtest manifest not found") from None
    config = manifest.configuration
    result = await run_validation_gauntlet(
        run_service,
        config,
        num_trials=num_trials,
    )
    return ValidationResponse(
        pbo=float(result.pbo),
        dsr=float(result.dsr),
        cpcv_mean_return=float(result.cpcv_mean_return),
        cpcv_std_return=float(result.cpcv_std_return),
        num_trials=result.num_trials,
    )
