"""Health endpoint tests for the market-data service.

Liveness never depends on the database (it must report healthy even if the
DB is down, per aqros_core.health's liveness/readiness split), so it is safe
to test here without a real Postgres. Readiness *does* depend on the
database and is covered end-to-end in tests/integration/test_api.py, where a
real Postgres is available via testcontainers.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from aqros_market_data.app import app

client = TestClient(app)


def test_liveness() -> None:
    resp = client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


def test_readiness_reports_service_metadata() -> None:
    # The DB is very likely unreachable in a plain unit-test run (no
    # container), so readiness may legitimately report unhealthy (503) here —
    # this test only asserts the response shape/service metadata, not health.
    resp = client.get("/health/ready")
    assert resp.status_code in (200, 503)
    assert resp.json()["service"] == "market-data"
