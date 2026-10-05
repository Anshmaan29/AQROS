from __future__ import annotations

from fastapi.testclient import TestClient

from aqros_portfolio.app import app

client = TestClient(app)


def test_liveness() -> None:
    resp = client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


def test_readiness_reports_database_down() -> None:
    # No Postgres in a unit test, so readiness must fail closed (503) rather
    # than claim the service can serve traffic it cannot reach a DB for.
    resp = client.get("/health/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unhealthy"
    assert any(check["name"] == "database" and not check["healthy"] for check in body["checks"])


def test_health_alias() -> None:
    resp = client.get("/health")
    assert resp.status_code == 503


def test_root_metadata() -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["service"] == "portfolio-engine"
