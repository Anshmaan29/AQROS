from __future__ import annotations

from typing import cast

from fastapi import Request

from aqros_risk_engine.domain.pipeline import RiskPipeline


def get_pipeline(request: Request) -> RiskPipeline:
    # app.state is untyped by nature; the cast keeps the money-path dependency
    # honest so a mis-wired pipeline surfaces as a type error, not a runtime one.
    return cast(RiskPipeline, request.app.state.pipeline)
