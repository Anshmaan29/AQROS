"""Inference service REST API routes."""

from __future__ import annotations

from datetime import UTC

from fastapi import APIRouter, Depends, HTTPException, status

from aqros_inference_service.api.deps import get_pipeline
from aqros_inference_service.api.schemas import (
    BatchPredictRequest,
    BatchPredictResponse,
    InferenceHealthResponse,
    InferenceStatisticsResponse,
    ModelInfo,
    PredictRequest,
    PredictResponse,
    ReloadRequest,
    RollbackRequest,
)
from aqros_inference_service.application.pipeline import PredictionPipeline
from aqros_inference_service.domain.models import PredictionRequest as DomainRequest

router = APIRouter(tags=["inference"])


@router.post("/v1/predict", response_model=PredictResponse)
async def predict(
    body: PredictRequest,
    pipeline: PredictionPipeline = Depends(get_pipeline),
) -> PredictResponse:
    """Run inference for a single symbol."""
    result = await pipeline.predict(
        symbol=body.symbol,
        model_name=body.model_name,
        model_version=body.model_version,
        features=body.features,
        correlation_id=body.correlation_id,
    )
    return PredictResponse(
        symbol=result.symbol,
        value=result.value,
        confidence=result.confidence.score,
        model_name=result.model_name,
        model_version=result.model_version,
        feature_version=result.feature_version,
        latency_ms=result.latency_ms,
        status=result.status.value,
        request_id="",
        explanation=(dict(result.explanation.feature_importance) if result.explanation else None),
        error=result.error.message if result.error else None,
    )


@router.post("/v1/predict/batch", response_model=BatchPredictResponse)
async def predict_batch(
    body: BatchPredictRequest,
    pipeline: PredictionPipeline = Depends(get_pipeline),
) -> BatchPredictResponse:
    """Run inference for multiple symbols."""
    domain_requests = [
        DomainRequest(
            symbol=r.symbol,
            model_name=r.model_name,
            model_version=r.model_version,
            features=r.features,
            correlation_id=r.correlation_id,
        )
        for r in body.requests
    ]
    results = await pipeline.predict_batch(domain_requests)
    success_count = sum(1 for r in results if r.status.value == "success")
    failure_count = len(results) - success_count
    return BatchPredictResponse(
        results=[
            PredictResponse(
                symbol=r.symbol,
                value=r.value,
                confidence=r.confidence.score,
                model_name=r.model_name,
                model_version=r.model_version,
                feature_version=r.feature_version,
                latency_ms=r.latency_ms,
                status=r.status.value,
                request_id="",
                explanation=(dict(r.explanation.feature_importance) if r.explanation else None),
                error=r.error.message if r.error else None,
            )
            for r in results
        ],
        total_latency_ms=sum(r.latency_ms for r in results),
        success_count=success_count,
        failure_count=failure_count,
    )


@router.get("/v1/models", response_model=list[ModelInfo])
async def list_models(
    pipeline: PredictionPipeline = Depends(get_pipeline),
) -> list[ModelInfo]:
    """Return all currently loaded models."""

    mgr = pipeline.model_manager
    models: list[ModelInfo] = []
    for (name, version), loaded in mgr.get_loaded_models():
        prod_version = mgr.get_production_version(name)
        models.append(
            ModelInfo(
                name=name,
                version=version,
                model_type=loaded.model_type.value,
                checksum=loaded.checksum,
                loaded_at=loaded.loaded_at.replace(tzinfo=UTC),
                production=(prod_version == version),
            )
        )
    return sorted(models, key=lambda m: (m.name, m.version))


@router.get("/v1/models/current", response_model=ModelInfo | None)
async def get_current_model(
    model_name: str = "default",
    pipeline: PredictionPipeline = Depends(get_pipeline),
) -> ModelInfo | None:
    """Return the current production model version."""
    mgr = pipeline.model_manager
    version = mgr.get_production_version(model_name)
    if version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No production version for model '{model_name}'",
        )
    loaded = mgr.get_loaded_model(model_name, version)
    if loaded is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model '{model_name}' version {version} not loaded",
        )

    return ModelInfo(
        name=loaded.name,
        version=loaded.version,
        model_type=loaded.model_type.value,
        checksum=loaded.checksum,
        loaded_at=loaded.loaded_at.replace(tzinfo=UTC),
        production=True,
    )


@router.post("/v1/models/reload")
async def reload_model(
    body: ReloadRequest,
    pipeline: PredictionPipeline = Depends(get_pipeline),
) -> dict[str, str]:
    """Force-reload a model (hot-reload)."""
    ok = await pipeline.reload_model(body.model_name, body.model_version)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model '{body.model_name}' not found for reload",
        )
    return {"status": "reloaded", "model_name": body.model_name}


@router.post("/v1/models/rollback")
async def rollback_model(
    body: RollbackRequest,
    pipeline: PredictionPipeline = Depends(get_pipeline),
) -> dict[str, str]:
    """Roll back to the previous production version."""
    ok = await pipeline.rollback_model(body.model_name)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"No previous version to roll back to for '{body.model_name}'",
        )
    return {"status": "rolled_back", "model_name": body.model_name}


@router.get("/v1/statistics", response_model=InferenceStatisticsResponse)
async def get_statistics(
    pipeline: PredictionPipeline = Depends(get_pipeline),
) -> InferenceStatisticsResponse:
    """Return aggregate inference statistics."""
    stats = pipeline.statistics
    total = int(stats.get("total_requests", 0))
    success = int(stats.get("total_success", 0))
    failures = int(stats.get("total_failures", 0))
    total_latency = float(stats.get("total_latency_ms", 0.0))
    avg_latency = total_latency / max(total, 1)

    return InferenceStatisticsResponse(
        total_requests=total,
        total_success=success,
        total_failures=failures,
        average_latency_ms=avg_latency,
        loaded_models=pipeline.model_manager.loaded_count,
        model_cache_hits=success,
        model_cache_misses=0,
        model_reload_count=pipeline.model_manager.reload_count,
        feature_validation_failures=failures,
    )


@router.get("/health/inference", response_model=InferenceHealthResponse)
async def inference_health(
    pipeline: PredictionPipeline = Depends(get_pipeline),
) -> InferenceHealthResponse:
    """Return the inference subsystem's health."""
    fs_healthy = await pipeline.check_feature_store_health()
    return InferenceHealthResponse(
        feature_store_reachable=fs_healthy,
        model_loaded=pipeline.model_manager.loaded_count > 0,
        loaded_model_count=pipeline.model_manager.loaded_count,
    )
