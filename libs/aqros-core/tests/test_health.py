"""Tests for the shared health framework and app factory."""

from __future__ import annotations

from fastapi.testclient import TestClient

from aqros_core.app import create_app
from aqros_core.config import BaseServiceSettings
from aqros_core.health import HealthRegistry


def _client(registry: HealthRegistry | None = None) -> TestClient:
    settings = BaseServiceSettings(service_name="test-svc", version="9.9.9")
    return TestClient(create_app(settings, health=registry))


def test_liveness_ok() -> None:
    resp = _client().get("/health/live")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


def test_readiness_ok_when_no_checks() -> None:
    resp = _client().get("/health/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


def test_readiness_reports_service_metadata() -> None:
    body = _client().get("/health").json()
    assert body["service"] == "test-svc"
    assert body["version"] == "9.9.9"


def test_readiness_fails_when_a_check_fails() -> None:
    registry = HealthRegistry()
    registry.register("always_down", lambda: False)
    resp = _client(registry).get("/health/ready")
    assert resp.status_code == 503
    assert resp.json()["status"] == "unhealthy"


def test_root_metadata_endpoint() -> None:
    body = _client().get("/").json()
    assert body == {"service": "test-svc", "version": "9.9.9", "status": "ok"}
