"""API request/response schemas for the parity monitor."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ParityViolationInfo(BaseModel):
    """Summary of a single failed comparison."""

    feature_name: str
    status: str
    reason: str
    offline_value: float | str | None = None
    online_value: float | str | None = None
    difference: float | None = None
    tolerance: float | None = None


class ParityReportResponse(BaseModel):
    """Full parity report response."""

    symbol: str
    timestamp: datetime
    feature_count: int
    matching_count: int
    failed_count: int
    missing_count: int
    stale_count: int
    version_mismatch_count: int
    passed: bool
    maximum_difference: float | None = None
    average_difference: float | None = None
    latency_ms: float | None = None
    violations: list[ParityViolationInfo] = []


class ParityRunRequest(BaseModel):
    """Request body for a single parity check run."""

    symbol: str
    tolerance: float | None = None


class ParityBatchRunRequest(BaseModel):
    """Request body for a batch parity check run."""

    symbols: list[str] = Field(..., min_length=1)
    tolerance: float | None = None


class ParityStatisticsResponse(BaseModel):
    """Aggregate parity statistics."""

    total_checks: int = 0
    total_matches: int = 0
    total_failures: int = 0
    total_skew: int = 0
    total_stale: int = 0
    total_missing: int = 0
    total_version_mismatches: int = 0
    average_latency_ms: float = 0.0
    overall_passing_rate: float = 0.0


class ParityHealthResponse(BaseModel):
    """Parity subsystem health status."""

    online_store_reachable: bool
    offline_store_reachable: bool
    last_check_timestamp: datetime | None = None
    last_check_passed: bool | None = None
