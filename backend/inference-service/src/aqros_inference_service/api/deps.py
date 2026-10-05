"""FastAPI dependency wiring for the inference service."""

from __future__ import annotations

from fastapi import Request

from aqros_inference_service.application.pipeline import PredictionPipeline


def get_pipeline(request: Request) -> PredictionPipeline:
    svc: PredictionPipeline = request.app.state.pipeline
    return svc
