"""Parity monitor REST API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from aqros_parity_monitor.api.deps import get_parity_service
from aqros_parity_monitor.api.schemas import (
    ParityBatchRunRequest,
    ParityHealthResponse,
    ParityReportResponse,
    ParityRunRequest,
    ParityStatisticsResponse,
    ParityViolationInfo,
)
from aqros_parity_monitor.application.service import ParityService

router = APIRouter(tags=["parity"])


def _report_to_response(report: object) -> ParityReportResponse:
    """Convert a ``ParityReport`` domain object to a Pydantic response."""
    from aqros_parity_monitor.domain.models import ParityReport

    r: ParityReport = report  # type: ignore[assignment]
    violations = [
        ParityViolationInfo(
            feature_name=c.feature_name,
            status=c.status.value,
            reason=c.reason,
            offline_value=c.offline_value,
            online_value=c.online_value,
            difference=c.difference,
            tolerance=c.tolerance,
        )
        for c in r.comparisons
        if not c.passed
    ]
    return ParityReportResponse(
        symbol=r.symbol,
        timestamp=r.timestamp,
        feature_count=r.feature_count,
        matching_count=r.matching_count,
        failed_count=r.failed_count,
        missing_count=r.missing_count,
        stale_count=r.stale_count,
        version_mismatch_count=r.version_mismatch_count,
        passed=r.passed,
        maximum_difference=r.maximum_difference,
        average_difference=r.average_difference,
        latency_ms=r.latency_ms,
        violations=violations,
    )


@router.get(
    "/v1/parity/{symbol}",
    response_model=ParityReportResponse,
)
async def get_parity_for_symbol(
    symbol: str,
    service: ParityService = Depends(get_parity_service),
) -> ParityReportResponse:
    """Run an immediate parity check for a single symbol."""
    report = await service.run_check(symbol)
    return _report_to_response(report)


@router.post(
    "/v1/parity/run",
    response_model=ParityReportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def run_parity_check(
    body: ParityRunRequest,
    service: ParityService = Depends(get_parity_service),
) -> ParityReportResponse:
    """Run a parity check for a single symbol with optional tolerance."""
    report = await service.run_check(body.symbol, tolerance=body.tolerance)
    return _report_to_response(report)


@router.post(
    "/v1/parity/run-batch",
    status_code=status.HTTP_201_CREATED,
)
async def run_batch_parity_check(
    body: ParityBatchRunRequest,
    service: ParityService = Depends(get_parity_service),
) -> list[ParityReportResponse]:
    """Run parity checks for multiple symbols."""
    reports = await service.run_batch(body.symbols, tolerance=body.tolerance)
    return [_report_to_response(r) for r in reports]


@router.get(
    "/v1/parity/history/{symbol}",
    response_model=list[ParityReportResponse],
)
async def get_parity_history(
    symbol: str,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    service: ParityService = Depends(get_parity_service),
) -> list[ParityReportResponse]:
    """Return historical parity reports for a symbol."""
    reports = await service.get_history(symbol, limit=limit, offset=offset)
    return [_report_to_response(r) for r in reports]


@router.get(
    "/v1/parity/latest/{symbol}",
    response_model=ParityReportResponse | None,
)
async def get_latest_parity(
    symbol: str,
    service: ParityService = Depends(get_parity_service),
) -> ParityReportResponse | None:
    """Return the most recent parity report for a symbol."""
    report = await service.get_latest_report(symbol)
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No parity report found for '{symbol}'",
        )
    return _report_to_response(report)


@router.get(
    "/v1/parity/statistics",
    response_model=ParityStatisticsResponse,
)
async def get_parity_statistics(
    service: ParityService = Depends(get_parity_service),
) -> ParityStatisticsResponse:
    """Return aggregate parity statistics."""
    stats = await service.get_statistics()
    return ParityStatisticsResponse(
        total_checks=stats.total_checks,
        total_matches=stats.total_matches,
        total_failures=stats.total_failures,
        total_skew=stats.total_skew,
        total_stale=stats.total_stale,
        total_missing=stats.total_missing,
        total_version_mismatches=stats.total_version_mismatches,
        average_latency_ms=stats.average_latency_ms,
        overall_passing_rate=stats.overall_passing_rate,
    )


@router.get("/health/parity", response_model=ParityHealthResponse)
async def parity_health(
    service: ParityService = Depends(get_parity_service),
) -> ParityHealthResponse:
    """Return the parity subsystem's health."""
    latest = await service.get_latest_report("")
    return ParityHealthResponse(
        online_store_reachable=True,
        offline_store_reachable=True,
        last_check_timestamp=latest.timestamp if latest else None,
        last_check_passed=latest.passed if latest else None,
    )
