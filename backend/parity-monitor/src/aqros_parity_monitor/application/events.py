"""Event payload schemas for parity monitor events.

Every event wraps a Pydantic model serialised as JSON bytes inside an
``EventEnvelope``. Schema versions follow ``MAJOR.MINOR``.

Events follow the ``subject.verb`` naming convention
(``CLAUDE.md`` §10): e.g. ``parity.check.started``, ``parity.check.completed``.
"""

from __future__ import annotations

from pydantic import BaseModel


class ParityCheckStarted(BaseModel):
    """Emitted when a parity check begins for one symbol."""

    schema_version: str = "1.0"
    symbol: str
    correlation_id: str | None = None


class ParityCheckCompleted(BaseModel):
    """Emitted when a parity check finishes for one symbol.

    Carries summary statistics (not the full report, which would exceed
    typical Kafka message limits for large portfolios).
    """

    schema_version: str = "1.0"
    symbol: str
    correlation_id: str | None = None
    feature_count: int = 0
    matching_count: int = 0
    failed_count: int = 0
    missing_count: int = 0
    stale_count: int = 0
    version_mismatch_count: int = 0
    passed: bool = True
    latency_ms: float | None = None


class ParityViolationDetected(BaseModel):
    """Emitted when a parity violation (failed comparison) is detected."""

    schema_version: str = "1.0"
    symbol: str
    feature_name: str
    correlation_id: str | None = None
    offline_value: float | str | None = None
    online_value: float | str | None = None
    difference: float | None = None
    tolerance: float | None = None
    reason: str = ""


class ParityRecovered(BaseModel):
    """Emitted when a previously-violated feature returns to parity."""

    schema_version: str = "1.0"
    symbol: str
    feature_name: str
    correlation_id: str | None = None
    previous_violation_reason: str = ""


class ParityReportGenerated(BaseModel):
    """Emitted when a full parity report is generated and persisted."""

    schema_version: str = "1.0"
    symbol: str
    correlation_id: str | None = None
    report_id: str | None = None
    feature_count: int = 0
    passed: bool = True
    latency_ms: float | None = None
