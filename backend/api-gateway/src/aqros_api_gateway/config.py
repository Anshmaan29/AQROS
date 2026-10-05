"""Configuration for the api-gateway service."""

from __future__ import annotations

from pydantic import Field

from aqros_core.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    """api-gateway settings (override defaults via AQROS_* env vars)."""

    service_name: str = "api-gateway"
    port: int = 8000

    # Total budget for one proxied request. Kept short because the gateway is
    # on the user's critical path; a slow upstream should surface as 503 quickly
    # rather than hold a client connection open for a minute.
    proxy_timeout_seconds: float = 30.0
    proxy_connect_timeout_seconds: float = 3.0
    proxy_max_connections: int = 100

    # Per-service liveness probe budget for the aggregate health endpoint.
    # Deliberately much shorter than proxy_timeout: /v1/health/platform fans out
    # to every service, so a slow one must not make the aggregate hang.
    probe_timeout_seconds: float = 3.0

    # Where the Auth service lives, for the gateway's auth handoff.
    auth_base_url: str = Field(default="http://auth:8001")
    auth_request_timeout_seconds: float = 5.0
