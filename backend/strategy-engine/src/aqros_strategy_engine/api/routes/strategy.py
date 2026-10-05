"""Strategy Engine REST API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from aqros_strategy_engine.api.deps import get_pipeline
from aqros_strategy_engine.api.schemas import (
    BatchEvaluateRequest,
    BatchEvaluateResponse,
    EvaluateRequest,
    EvaluateResponse,
    ReloadStrategyRequest,
    StrategyHealthResponse,
    StrategyInfo,
    StrategyStatisticsResponse,
)
from aqros_strategy_engine.application.pipeline import StrategyPipeline
from aqros_strategy_engine.domain.models import StrategyEvaluationRequest as DomainRequest

router = APIRouter(tags=["strategy"])


@router.post("/v1/strategy/evaluate", response_model=EvaluateResponse)
async def evaluate(
    body: EvaluateRequest,
    pipeline: StrategyPipeline = Depends(get_pipeline),
) -> EvaluateResponse:
    domain_req = DomainRequest(
        symbol=body.symbol,
        model_name=body.model_name,
        model_version=body.model_version,
        features=body.features,
        prediction=body.prediction,
        prediction_confidence=body.prediction_confidence,
        strategy_name=body.strategy_name,
        correlation_id=body.correlation_id,
    )
    result = await pipeline.evaluate(domain_req)
    return EvaluateResponse(
        symbol=result.symbol,
        signal=result.decision.signal.value,
        confidence=result.decision.confidence.score,
        strength=result.decision.strength.value,
        reason=result.decision.reason.value,
        explanation=result.decision.explanation,
        latency_ms=result.latency_ms,
        strategy_name=result.decision.strategy_version or "",
        strategy_version=result.decision.strategy_version or "",
        prediction=result.decision.prediction_value,
        model_version=result.decision.model_version,
        error=result.errors[0] if result.errors else None,
    )


@router.post("/v1/strategy/batch", response_model=BatchEvaluateResponse)
async def evaluate_batch(
    body: BatchEvaluateRequest,
    pipeline: StrategyPipeline = Depends(get_pipeline),
) -> BatchEvaluateResponse:
    domain_requests = [
        DomainRequest(
            symbol=r.symbol,
            model_name=r.model_name,
            model_version=r.model_version,
            features=r.features,
            prediction=r.prediction,
            prediction_confidence=r.prediction_confidence,
            strategy_name=r.strategy_name,
            correlation_id=r.correlation_id,
        )
        for r in body.requests
    ]
    results = await pipeline.evaluate_batch(domain_requests)
    success_count = sum(1 for r in results if not r.errors)
    failure_count = len(results) - success_count
    return BatchEvaluateResponse(
        results=[
            EvaluateResponse(
                symbol=r.symbol,
                signal=r.decision.signal.value,
                confidence=r.decision.confidence.score,
                strength=r.decision.strength.value,
                reason=r.decision.reason.value,
                explanation=r.decision.explanation,
                latency_ms=r.latency_ms,
                strategy_version=r.decision.strategy_version or "",
                prediction=r.decision.prediction_value,
                model_version=r.decision.model_version,
                error=r.errors[0] if r.errors else None,
            )
            for r in results
        ],
        total_latency_ms=sum(r.latency_ms for r in results),
        success_count=success_count,
        failure_count=failure_count,
    )


@router.get("/v1/strategy/list", response_model=list[StrategyInfo])
async def list_strategies(
    pipeline: StrategyPipeline = Depends(get_pipeline),
) -> list[StrategyInfo]:
    mgr = pipeline.strategy_manager
    active_names = set(mgr.active_strategies)
    metas = mgr.list_strategies()
    return [
        StrategyInfo(
            name=m.name,
            version=m.version,
            description=m.description,
            model_family=m.model_family,
            active=m.name in active_names,
            tags=m.tags,
        )
        for m in metas
    ]


@router.get("/v1/strategy/current", response_model=StrategyInfo | None)
async def get_current_strategy(
    strategy_name: str = "default",
    pipeline: StrategyPipeline = Depends(get_pipeline),
) -> StrategyInfo | None:
    meta = pipeline.strategy_manager.get_strategy_metadata(strategy_name)
    if meta is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Strategy '{strategy_name}' not found",
        )
    return StrategyInfo(
        name=meta.name,
        version=meta.version,
        description=meta.description,
        model_family=meta.model_family,
        active=True,
        tags=meta.tags,
    )


@router.post("/v1/strategy/reload")
async def reload_strategy(
    body: ReloadStrategyRequest,
    pipeline: StrategyPipeline = Depends(get_pipeline),
) -> dict[str, str]:
    ok = await pipeline.reload_strategy(body.strategy_name, body.strategy_version)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Strategy '{body.strategy_name}' not found for reload",
        )
    return {"status": "reloaded", "strategy_name": body.strategy_name}


@router.get("/v1/statistics", response_model=StrategyStatisticsResponse)
async def get_statistics(
    pipeline: StrategyPipeline = Depends(get_pipeline),
) -> StrategyStatisticsResponse:
    stats = pipeline.statistics
    total = int(stats.get("total_evaluations", 0))
    total_latency = float(stats.get("total_latency_ms", 0.0))
    avg_latency = total_latency / max(total, 1)
    return StrategyStatisticsResponse(
        total_evaluations=total,
        total_signals=int(stats.get("total_signals", 0)),
        total_rejected=int(stats.get("total_rejected", 0)),
        total_errors=int(stats.get("total_errors", 0)),
        average_latency_ms=avg_latency,
        buy_count=int(stats.get("buy_count", 0)),
        sell_count=int(stats.get("sell_count", 0)),
        hold_count=int(stats.get("hold_count", 0)),
        exit_count=int(stats.get("exit_count", 0)),
        reduce_count=int(stats.get("reduce_count", 0)),
        increase_count=int(stats.get("increase_count", 0)),
        loaded_strategies=len(pipeline.strategy_manager.active_strategies),
    )


@router.get("/health/strategy", response_model=StrategyHealthResponse)
async def strategy_health(
    pipeline: StrategyPipeline = Depends(get_pipeline),
) -> StrategyHealthResponse:
    fs_healthy = True
    inf_healthy = True
    fp = pipeline.feature_provider
    if fp is not None:
        fs_healthy = await fp.health_check()
    return StrategyHealthResponse(
        inference_reachable=inf_healthy,
        feature_store_reachable=fs_healthy,
        strategy_loaded=len(pipeline.strategy_manager.active_strategies) > 0,
        loaded_strategy_count=len(pipeline.strategy_manager.active_strategies),
    )
