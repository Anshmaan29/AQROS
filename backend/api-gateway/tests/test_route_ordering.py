"""Regression test for gateway route ordering.

The proxy route is a catch-all: ``/v1/{service_name}/{path:path}``. FastAPI
matches routes in registration order, so any more specific ``/v1/...`` route
must be registered *before* it or the catch-all swallows it and answers
``404 unknown_service``.

This bit us in practice: ``/v1/health/platform`` was being proxied as if
"health" were a service name, so the aggregate health endpoint was
unreachable on a running stack.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from aqros_api_gateway.app import app
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


class TestSpecificRoutesBeatTheCatchAll:
    def test_platform_health_is_not_proxied(self, client: TestClient) -> None:
        """The regression: this returned 404 unknown_service before the fix."""
        response = client.get("/v1/health/platform")
        assert response.status_code == 200
        assert response.json()["status"] in {"healthy", "degraded", "unhealthy"}
        # Must not be mistaken for a service named "health".
        assert "services" in response.json()

    def test_topology_still_resolves(self, client: TestClient) -> None:
        response = client.get("/v1/topology")
        assert response.status_code == 200
        assert response.json()["total"] > 0

    def test_describe_route_still_resolves(self, client: TestClient) -> None:
        assert client.get("/v1/routes/market-data").status_code == 200

    def test_health_route_is_not_in_the_topology(self, client: TestClient) -> None:
        """Guards against 'health' being registered as a service."""
        body = client.get("/v1/topology").json()
        names = {s["name"] for s in body["public_services"] + body["internal_services"]}
        assert "health" not in names

    def test_platform_health_response_shape(self, client: TestClient) -> None:
        body = client.get("/v1/health/platform").json()
        for key in ("status", "healthy", "unhealthy", "total", "services"):
            assert key in body
