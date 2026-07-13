"""Health endpoint tests for the dataset-builder service.

Liveness never depends on the database or either upstream service (it must
report healthy even if any of them is down), so it is safe to test here
without any being reachable. Readiness *does* depend on all three and is
covered end-to-end in tests/integration/test_api.py, where a real Postgres
and faked upstreams are available.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from aqros_dataset_builder.app import app

client = TestClient(app)


def test_liveness() -> None:
    resp = client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


def test_readiness_reports_service_metadata() -> None:
    # The DB and both upstream services are very likely unreachable in a
    # plain unit-test run (no container, no other service running), so
    # readiness may legitimately report unhealthy (503) here — this test
    # only asserts the response shape/service metadata, not health.
    resp = client.get("/health/ready")
    assert resp.status_code in (200, 503)
    assert resp.json()["service"] == "dataset-builder"
