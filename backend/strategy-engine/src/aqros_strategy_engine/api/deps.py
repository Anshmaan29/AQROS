"""FastAPI dependency wiring for the Strategy Engine."""

from __future__ import annotations

from fastapi import Request

from aqros_strategy_engine.application.pipeline import StrategyPipeline


def get_pipeline(request: Request) -> StrategyPipeline:
    svc: StrategyPipeline = request.app.state.pipeline
    return svc
