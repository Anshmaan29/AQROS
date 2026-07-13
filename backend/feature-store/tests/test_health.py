"""Health endpoint tests for the feature-store service.

Liveness never depends on the database or the Market Data Service (it must
report healthy even if either is down), so it is safe to test here without
either being reachable. Readiness *does* depend on both and is covered
end-to-end in tests/integration/test_api.py, where a real Postgres and a
faked Market Data Service are available.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from aqros_feature_store.app import app

client = TestClient(app)


def test_liveness() -> None:
    resp = client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


def test_readiness_reports_service_metadata() -> None:
    # The DB and the Market Data Service are very likely unreachable in a
    # plain unit-test run (no container, no other service running), so
    # readiness may legitimately report unhealthy (503) here — this test only
    # asserts the response shape/service metadata, not health.
    resp = client.get("/health/ready")
    assert resp.status_code in (200, 503)
    assert resp.json()["service"] == "feature-store"
