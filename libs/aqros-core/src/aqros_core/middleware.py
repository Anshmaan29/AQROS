"""Cross-cutting ASGI middleware shared by every AQROS service.

CLAUDE.md §5 mandates structured logging with a correlation ID threaded
through every request and decision, and the architecture review requires a
Prometheus ``/metrics`` endpoint on every service. Both are implemented once
here so services cannot diverge.

``install_observability`` is idempotent, so ``create_app`` can call it
unconditionally.
"""

from __future__ import annotations

import time
from typing import Any

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from aqros_core.correlation import CORRELATION_HEADER, resolve
from aqros_core.metrics import REGISTRY, MetricsRegistry, observe_request

_logger = structlog.get_logger(__name__)


class CorrelationIdMiddleware:
    """Resolve a correlation ID per request, bind it to logs, echo it back.

    The ID is placed in ``request.state`` and bound into structlog's
    contextvars, so every log line emitted while handling the request — including
    those from deeper layers — carries it. The response echoes the ID so a
    caller can quote it when reporting a problem.

    Implemented at the ASGI layer rather than via ``BaseHTTPMiddleware`` so the
    header is injected without buffering or re-materialising the response body.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        correlation_id = resolve(headers.get(CORRELATION_HEADER.lower()))

        state = scope.setdefault("state", {})
        state["correlation_id"] = correlation_id

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                # Echo on every response, including error responses, so a client
                # can always quote the id it was given.
                raw_headers = list(message.get("headers", []))
                raw_headers.append(
                    (CORRELATION_HEADER.lower().encode("latin-1"), correlation_id.encode("latin-1"))
                )
                message["headers"] = raw_headers
            await send(message)

        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            structlog.contextvars.unbind_contextvars("correlation_id")


class HttpMetricsMiddleware:
    """Record request counts, latency, and in-flight depth.

    Pure ASGI (not ``BaseHTTPMiddleware``) so it measures true wall-clock service
    time and adds no response-buffering layer.
    """

    def __init__(self, app: ASGIApp, registry: MetricsRegistry | None = None) -> None:
        self.app = app
        self.registry = registry if registry is not None else REGISTRY
        self.in_flight = self.registry.gauge(
            "aqros_http_requests_in_flight", "HTTP requests currently being served."
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET")
        # Assume 5xx until the app proves otherwise, so an unhandled exception
        # is still counted as a server error rather than silently dropped.
        status_code = 500
        start = time.monotonic()
        self.in_flight.inc()

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message.get("status", 500))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration = time.monotonic() - start
            self.in_flight.dec()
            observe_request(method, status_code, duration, self.registry)
            _logger.debug(
                "http.request",
                method=method,
                path=scope.get("path", ""),
                status_code=status_code,
                duration_ms=round(duration * 1000, 2),
            )


def install_observability(app: Any, registry: MetricsRegistry | None = None) -> None:
    """Install correlation-ID + metrics middleware and mount ``/metrics``.

    Idempotent: safe to call more than once without double-registering.
    """
    from fastapi import FastAPI
    from fastapi.responses import PlainTextResponse

    if not isinstance(app, FastAPI):
        raise TypeError("install_observability expects a FastAPI application")
    reg = registry if registry is not None else REGISTRY

    installed = {getattr(m, "cls", None) for m in app.user_middleware}
    if HttpMetricsMiddleware not in installed:
        app.add_middleware(HttpMetricsMiddleware, registry=reg)
    if CorrelationIdMiddleware not in installed:
        app.add_middleware(CorrelationIdMiddleware)

    if not any(getattr(route, "path", None) == "/metrics" for route in app.routes):

        @app.get("/metrics", include_in_schema=False)
        async def metrics() -> Any:
            return PlainTextResponse(
                reg.render(), media_type="text/plain; version=0.0.4; charset=utf-8"
            )
