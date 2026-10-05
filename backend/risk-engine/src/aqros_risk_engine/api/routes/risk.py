from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from structlog import get_logger

from aqros_risk_engine.api.deps import get_pipeline
from aqros_risk_engine.api.schemas import (
    BatchEvaluateRequest,
    BatchEvaluateResponse,
    EvaluateRequest,
    EvaluateResponse,
    HealthResponse,
    RiskLimitCreateRequest,
    RiskLimitResponse,
    RiskLimitsResponse,
    RiskStatisticsResponse,
)
from aqros_risk_engine.domain.models import Signal
from aqros_risk_engine.domain.pipeline import RiskPipeline

_logger = get_logger(__name__)

router = APIRouter(tags=["risk"])


def _signal_from_request(req: EvaluateRequest) -> Signal:
    return Signal(
        signal_id=req.signal.signal_id,
        symbol=req.signal.symbol,
        side=req.signal.side,
        quantity=req.signal.quantity,
        confidence=req.signal.confidence,
        prediction=req.signal.prediction,
        prediction_quality=req.signal.prediction_quality,
        current_price=req.signal.current_price,
        sector=req.signal.sector,
        volatility=req.signal.volatility,
        avg_daily_volume=req.signal.avg_daily_volume,
        strategy=req.signal.strategy,
        correlation_id=req.signal.correlation_id,
    )


def _result_to_response(result: Any, signal: Signal) -> EvaluateResponse:
    return EvaluateResponse(
        signal_id=signal.signal_id,
        symbol=signal.symbol,
        side=signal.side,
        decision=result.decision.value,
        reasons=list(result.reasons),
        message=result.message,
        risk_level=result.risk_level.label,
        requested_quantity=str(signal.quantity),
        approved_quantity=str(result.approved_quantity),
        stop_loss=str(result.stop_loss) if result.stop_loss else None,
        latency_ms=round(result.latency_ms, 2),
    )


@router.post("/v1/risk/evaluate", response_model=EvaluateResponse)
async def evaluate_signal(
    request: EvaluateRequest,
    pipeline: RiskPipeline = Depends(get_pipeline),
) -> EvaluateResponse:
    signal = _signal_from_request(request)
    result = await pipeline.evaluate(signal)
    return _result_to_response(result, signal)


@router.post("/v1/risk/batch", response_model=BatchEvaluateResponse)
async def evaluate_batch(
    request: BatchEvaluateRequest,
    pipeline: RiskPipeline = Depends(get_pipeline),
) -> BatchEvaluateResponse:
    results: list[EvaluateResponse] = []
    for req in request.signals:
        signal = _signal_from_request(req)
        result = await pipeline.evaluate(signal)
        results.append(_result_to_response(result, signal))
    return BatchEvaluateResponse(results=results)


@router.get("/v1/risk/statistics", response_model=RiskStatisticsResponse)
async def get_statistics(
    pipeline: RiskPipeline = Depends(get_pipeline),
) -> RiskStatisticsResponse:
    stats = pipeline.statistics.snapshot()
    return RiskStatisticsResponse(**stats)


@router.get("/v1/risk/limits", response_model=RiskLimitsResponse)
async def get_limits(
    pipeline: RiskPipeline = Depends(get_pipeline),
) -> RiskLimitsResponse:
    limits = pipeline.limits.snapshot()
    return RiskLimitsResponse(
        limits=[
            RiskLimitResponse(
                id=0,
                limit_type=k,
                limit_value=v,
                is_kernel=True,
                scope="global",
                scope_ref=None,
                created_by="system",
                approved_by=None,
                created_at="",
            )
            for k, v in limits.items()
        ]
    )


@router.post("/v1/risk/limits", response_model=RiskLimitResponse)
async def update_limits(
    request: RiskLimitCreateRequest,
    pipeline: RiskPipeline = Depends(get_pipeline),
) -> RiskLimitResponse:
    if hasattr(pipeline.limits, request.limit_type):
        current = pipeline.limits.snapshot()
        current[request.limit_type] = request.limit_value
        from aqros_risk_engine.domain.models import RiskLimits

        pipeline.set_limits(RiskLimits(**current))
    return RiskLimitResponse(
        id=0,
        limit_type=request.limit_type,
        limit_value=request.limit_value,
        is_kernel=request.is_kernel,
        scope=request.scope,
        scope_ref=request.scope_ref,
        created_by=request.created_by,
        approved_by=None,
        created_at="",
    )


@router.get("/health/risk", response_model=HealthResponse)
async def risk_health(
    pipeline: RiskPipeline = Depends(get_pipeline),
) -> HealthResponse:
    return HealthResponse(
        status="healthy",
        database=False,
        portfolio=False,
        market_data=False,
        check_count=0,
    )
