"""Health endpoint tests for the market-data service."""

from __future__ import annotations

from aqros_market_data.app import app
from fastapi.testclient import TestClient

client = TestClient(app)


def test_liveness() -> None:
    resp = client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


def test_readiness() -> None:
    resp = client.get("/health/ready")
    assert resp.status_code == 200
    assert resp.json()["service"] == "market-data"
