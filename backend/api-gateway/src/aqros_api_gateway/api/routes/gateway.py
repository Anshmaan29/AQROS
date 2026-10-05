"""Gateway HTTP surface: discovery, health aggregation, and reverse proxy."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from aqros_api_gateway.adapters.proxy import GatewayProxy
from aqros_api_gateway.domain.topology import Exposure, RouteRegistry, ServiceRoute

_logger = structlog.get_logger(__name__)

router = APIRouter()


class ServiceInfo(BaseModel):
    """A routable service, as advertised by the discovery endpoint."""

    name: str
    base_url: str
    port: int
    zone: str
    exposure: str
    description: str

    @classmethod
    def from_route(cls, route: ServiceRoute) -> ServiceInfo:
        return cls(
            name=route.name,
            base_url=route.base_url,
            port=route.port,
            zone=str(route.zone),
            exposure=str(route.exposure),
            description=route.description,
        )


class TopologyResponse(BaseModel):
    """The platform topology as the gateway sees it."""

    public_services: list[ServiceInfo]
    internal_services: list[ServiceInfo]
    total: int = Field(..., description="Total number of registered services.")


def get_registry() -> RouteRegistry:
    """Return the service topology registry."""
    return RouteRegistry()


def get_proxy(request: Request) -> GatewayProxy:
    """Return the proxy bound to the app's shared HTTP client."""
    proxy: GatewayProxy = request.app.state.proxy
    return proxy


def _json_response(status_code: int, body: dict[str, Any]) -> Response:
    """Build a JSON error response.

    The correlation-ID response header is added by ``CorrelationIdMiddleware``
    on every response, so it must not also be set here or clients receive the
    header twice (which most HTTP clients join into a comma-separated value).
    """
    return JSONResponse(status_code=status_code, content=body)


@router.get("/v1/topology", response_model=TopologyResponse, tags=["discovery"])
async def topology(registry: RouteRegistry = Depends(get_registry)) -> TopologyResponse:
    """Advertise which services exist and which are publicly reachable.

    Internal services are listed for operator visibility but are explicitly
    marked and are *not* proxyable — see :func:`proxy`.
    """
    return TopologyResponse(
        public_services=[ServiceInfo.from_route(r) for r in registry.public()],
        internal_services=[ServiceInfo.from_route(r) for r in registry.internal()],
        total=len(registry.all()),
    )


@router.get("/v1/routes/{service_name}", tags=["discovery"])
async def describe_route(
    service_name: str,
    request: Request,
    registry: RouteRegistry = Depends(get_registry),
) -> Response:
    """Describe a single service, or 404 if unknown."""
    route = registry.get(service_name)
    if route is None:
        return _json_response(404, {"error": "unknown_service", "service": service_name})
    return _json_response(200, ServiceInfo.from_route(route).model_dump())


@router.api_route(
    "/v1/{service_name}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    tags=["proxy"],
)
async def proxy(
    service_name: str,
    path: str,
    request: Request,
    registry: RouteRegistry = Depends(get_registry),
    proxy_client: GatewayProxy = Depends(get_proxy),
) -> Response:
    """Reverse-proxy a request to an upstream service.

    Refuses to proxy to any service that is not ``PUBLIC``. The money-path
    services (``risk-engine``, ``oms``, ``live-trading-engine``) are reached over
    internal synchronous calls, never through this public edge — proxying them
    here would bypass auth, rate limiting, and audit.
    """
    correlation_id = str(getattr(request.state, "correlation_id", ""))
    route = registry.get(service_name)
    if route is None:
        return _json_response(404, {"error": "unknown_service", "service": service_name})

    if route.exposure is not Exposure.PUBLIC:
        # Deliberately 404, not 403: a public caller must not be able to probe
        # which internal services exist.
        _logger.info("gateway.blocked_internal_route", service=service_name, zone=str(route.zone))
        return _json_response(404, {"error": "unknown_service", "service": service_name})

    body = await request.body()
    incoming = {k.lower(): v for k, v in request.headers.items()}

    try:
        status, headers, payload = await proxy_client.forward(
            method=request.method,
            base_url=route.base_url,
            path=f"/{path}",
            query=request.url.query,
            headers=incoming,
            correlation_id=correlation_id,
            body=body or None,
        )
    except Exception as exc:
        # An unreachable upstream degrades honestly (503 + correlation id)
        # rather than hanging the caller.
        _logger.warning("gateway.upstream_unavailable", service=service_name, error=str(exc))
        return _json_response(
            503,
            {
                "error": "upstream_unavailable",
                "service": service_name,
                "correlation_id": correlation_id,
            },
        )

    # Hop-by-hop headers were already dropped in the adapter; drop the framing
    # headers too, since we are re-framing the body as a fresh response.
    response_headers = {
        k: v
        for k, v in headers.items()
        if k not in {"content-length", "transfer-encoding", "connection"}
    }
    return Response(content=payload, status_code=status, headers=response_headers)
