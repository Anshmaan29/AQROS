"""Health-check framework.

A small, dependency-injectable registry of named readiness checks plus a
FastAPI router exposing Kubernetes-style probes:

* ``GET /health/live``  — liveness: the process is up (always healthy).
* ``GET /health/ready`` — readiness: all registered checks pass (503 if not).
* ``GET /health``       — alias for readiness (overall status).

Checks are ``Callable[[], bool | Awaitable[bool]]``. Services register real
checks (DB reachable, broker connected, ...) in later phases; in Phase 0 the
registry is simply empty and readiness is trivially healthy.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from aqros_core.config import BaseServiceSettings

CheckFn = Callable[[], bool | Awaitable[bool]]


class HealthState(StrEnum):
    """Aggregate health state."""

    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"


class ComponentHealth(BaseModel):
    """Result of a single named check."""

    name: str
    healthy: bool
    detail: str | None = None


class HealthReport(BaseModel):
    """Aggregate health response."""

    status: HealthState
    service: str
    version: str
    checks: list[ComponentHealth] = []


@dataclass
class HealthRegistry:
    """Registry of named readiness checks."""

    _checks: dict[str, CheckFn] = field(default_factory=dict)

    def register(self, name: str, check: CheckFn) -> None:
        """Register a named readiness check."""
        self._checks[name] = check

    async def run(self) -> list[ComponentHealth]:
        """Execute all checks (sync or async) and collect their results."""
        results: list[ComponentHealth] = []
        for name, check in self._checks.items():
            try:
                outcome = check()
                healthy = await outcome if inspect.isawaitable(outcome) else outcome
                results.append(ComponentHealth(name=name, healthy=bool(healthy)))
            except Exception as exc:
                results.append(ComponentHealth(name=name, healthy=False, detail=str(exc)))
        return results


def build_health_router(registry: HealthRegistry, settings: BaseServiceSettings) -> APIRouter:
    """Build a FastAPI router exposing liveness/readiness probes."""
    router = APIRouter(tags=["health"])

    def _report(state: HealthState, checks: list[ComponentHealth]) -> HealthReport:
        return HealthReport(
            status=state,
            service=settings.service_name,
            version=settings.version,
            checks=checks,
        )

    @router.get("/health/live", response_model=HealthReport)
    async def live() -> HealthReport:
        return _report(HealthState.HEALTHY, [])

    @router.get("/health/ready", response_model=HealthReport)
    async def ready(response: Response) -> HealthReport:
        checks = await registry.run()
        ok = all(check.healthy for check in checks)
        if not ok:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return _report(HealthState.HEALTHY if ok else HealthState.UNHEALTHY, checks)

    @router.get("/health", response_model=HealthReport)
    async def health(response: Response) -> HealthReport:
        return await ready(response)

    return router
