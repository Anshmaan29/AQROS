"""Aggregate readiness across every service in the platform.

The design doc calls for ``GET /v1/health/platform`` on the monitoring gateway.
It lives here because the gateway is the only component that knows the whole
topology and is reachable from outside the cluster — which makes it the natural
place to answer "is the platform up?" in one call.

Each probe is independent and bounded by a short timeout: one dead service must
not turn the aggregate into a hang. A slow service degrades that one entry to
``unreachable`` rather than failing the whole check.
"""

from __future__ import annotations

import asyncio

import structlog
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from aqros_api_gateway.domain.topology import RouteRegistry
from aqros_core.http import ResilientClient

_logger = structlog.get_logger(__name__)

router = APIRouter(tags=["health"])


class ServiceHealth(BaseModel):
    """Health of a single upstream service."""

    name: str
    healthy: bool
    status_code: int | None = None
    latency_ms: float | None = None
    error: str | None = None


class PlatformHealth(BaseModel):
    """Aggregate platform health.

    ``status`` is ``healthy`` only when *every* service is reachable, so a
    single green service can never mask a dead money-path service.
    """

    status: str
    healthy: int = 0
    unhealthy: int = 0
    total: int = 0
    services: list[ServiceHealth] = Field(default_factory=list)


async def _probe(client: ResilientClient, base_url: str, timeout: float) -> ServiceHealth:
    """Probe one service's liveness endpoint."""
    loop = asyncio.get_running_loop()
    started = loop.time()
    try:
        response = await asyncio.wait_for(
            client.get(f"{base_url.rstrip('/')}/health/live"),
            timeout=timeout,
        )
        latency = round((loop.time() - started) * 1000, 2)
        return ServiceHealth(
            name="",
            healthy=response.status_code == 200,
            status_code=response.status_code,
            latency_ms=latency,
        )
    except TimeoutError:
        return ServiceHealth(
            name="",
            healthy=False,
            latency_ms=round((loop.time() - started) * 1000, 2),
            error="timeout",
        )
    except Exception as exc:
        return ServiceHealth(
            name="",
            healthy=False,
            latency_ms=round((loop.time() - started) * 1000, 2),
            error=type(exc).__name__,
        )


def get_registry() -> RouteRegistry:
    return RouteRegistry()


@router.get("/v1/health/platform", response_model=PlatformHealth, tags=["health"])
async def platform_health(
    request: Request,
    registry: RouteRegistry = Depends(get_registry),
) -> PlatformHealth:
    """Probe every service concurrently and report aggregate health.

    Healthy means *all* services answered 200 on ``/health/live``. Money-path
    services are included on purpose: a platform whose OMS is down is not
    healthy even if research services are fine.
    """
    client: ResilientClient = request.app.state.http_client
    timeout: float = request.app.state.probe_timeout_seconds

    routes = registry.all()
    results = await asyncio.gather(*(_probe(client, route.base_url, timeout) for route in routes))

    services: list[ServiceHealth] = []
    for route, result in zip(routes, results, strict=True):
        result.name = route.name
        services.append(result)

    healthy = sum(1 for s in services if s.healthy)
    unhealthy = len(services) - healthy
    status = "healthy" if unhealthy == 0 else "degraded"

    # An unhealthy money-path service is a distinct, louder condition than a
    # degraded research service.
    execution_down = [s.name for s in services if not s.healthy and s.name in _MONEY_PATH_SERVICES]
    if execution_down:
        status = "unhealthy"

    return PlatformHealth(
        status=status,
        healthy=healthy,
        unhealthy=unhealthy,
        total=len(services),
        services=services,
    )


_MONEY_PATH_SERVICES = frozenset(
    {"risk-engine", "portfolio", "oms", "live-trading-engine", "audit-ledger"}
)
