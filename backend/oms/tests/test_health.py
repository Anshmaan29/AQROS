from __future__ import annotations

from fastapi.testclient import TestClient

from aqros_oms.app import app

client = TestClient(app)


def test_liveness() -> None:
    response = client.get("/health/live")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"


def test_readiness() -> None:
    response = client.get("/health/ready")
    assert response.status_code == 503


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 503
