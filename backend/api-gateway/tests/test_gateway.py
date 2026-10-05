"""Tests for the api-gateway.

The load-bearing tests are the *security* ones: the money-path services must be
unreachable through the public edge. A regression there would let an external
caller bypass auth, rate limiting, and audit (CLAUDE.md §7.7).
"""

from __future__ import annotations

import pytest
from aqros_api_gateway.adapters.proxy import build_upstream_headers
from aqros_api_gateway.app import app
from aqros_api_gateway.domain.topology import Exposure, RouteRegistry, Zone
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


class TestTopologyDomain:
    def test_registry_resolves_known_service(self) -> None:
        registry = RouteRegistry()
        route = registry.get("market-data")
        assert route is not None
        assert route.port == 8002

    def test_unknown_service_is_none(self) -> None:
        assert RouteRegistry().get("does-not-exist") is None

    def test_every_service_has_unique_name(self) -> None:
        registry = RouteRegistry()
        names = [r.name for r in registry.all()]
        assert len(names) == len(set(names))

    def test_every_service_has_unique_port(self) -> None:
        """Two services on one port would silently break routing in compose."""
        ports = [r.port for r in RouteRegistry().all()]
        assert len(ports) == len(set(ports))

    def test_public_and_internal_partition_everything(self) -> None:
        registry = RouteRegistry()
        total = len(registry.all())
        assert len(registry.public()) + len(registry.internal()) == total


class TestMoneyPathIsInternal:
    """CLAUDE.md: the trading hot path is never exposed through the gateway."""

    @pytest.mark.parametrize(
        "service",
        ["risk-engine", "portfolio", "oms", "live-trading-engine", "audit-ledger"],
    )
    def test_money_path_service_is_not_public(self, service: str) -> None:
        route = RouteRegistry().get(service)
        assert route is not None
        assert (
            route.exposure is Exposure.INTERNAL
        ), f"{service} must not be publicly exposed — it carries money-path state"

    @pytest.mark.parametrize("service", ["risk-engine", "oms", "live-trading-engine"])
    def test_money_path_routes_are_404_not_403(self, client: TestClient, service: str) -> None:
        """404 (not 403) so the public edge cannot be used to enumerate internals."""
        response = client.get(f"/v1/{service}/v1/anything")
        assert response.status_code == 404
        assert response.json()["error"] == "unknown_service"


class TestDiscovery:
    def test_topology_lists_services(self, client: TestClient) -> None:
        response = client.get("/v1/topology")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == len(body["public_services"]) + len(body["internal_services"])
        assert body["total"] > 0

    def test_topology_marks_exposure(self, client: TestClient) -> None:
        body = client.get("/v1/topology").json()
        for service in body["internal_services"]:
            assert service["exposure"] == "internal"

    def test_describe_known_service(self, client: TestClient) -> None:
        response = client.get("/v1/routes/market-data")
        assert response.status_code == 200
        assert response.json()["port"] == 8002

    def test_describe_unknown_service_404(self, client: TestClient) -> None:
        assert client.get("/v1/routes/nope").status_code == 404


class TestCorrelation:
    def test_echoes_generated_id(self, client: TestClient) -> None:
        response = client.get("/v1/topology")
        assert response.headers.get("X-Correlation-ID")

    def test_echoes_supplied_id(self, client: TestClient) -> None:
        response = client.get("/v1/topology", headers={"X-Correlation-ID": "trace-42"})
        assert response.headers["X-Correlation-ID"] == "trace-42"

    def test_404_still_carries_correlation_id(self, client: TestClient) -> None:
        """A caller debugging a failure needs the id even on an error path."""
        response = client.get("/v1/routes/nope")
        assert response.headers.get("X-Correlation-ID")


class TestMetrics:
    def test_metrics_endpoint_serves_prometheus_text(self, client: TestClient) -> None:
        response = client.get("/metrics")
        assert response.status_code == 200
        assert "aqros_http_requests_total" in response.text

    def test_metrics_records_request(self, client: TestClient) -> None:
        client.get("/v1/topology")
        body = client.get("/metrics").text
        assert "aqros_http_request_duration_seconds_count" in body


class TestHeaderForwarding:
    def test_strips_hop_by_hop_headers(self) -> None:
        """Forwarding Connection/Transfer-Encoding corrupts the upstream."""
        out = build_upstream_headers(
            {"connection": "keep-alive", "transfer-encoding": "chunked", "x-keep": "yes"},
            "cid",
        )
        assert "connection" not in out
        assert "transfer-encoding" not in out
        assert out["x-keep"] == "yes"

    def test_injects_correlation_id(self) -> None:
        out = build_upstream_headers({}, "cid-123")
        assert out["x-correlation-id"] == "cid-123"

    def test_caller_cannot_spoof_correlation_id(self) -> None:
        """Our resolved id must win over anything the client sent."""
        out = build_upstream_headers({"x-correlation-id": "attacker-supplied"}, "cid-real")
        assert out["x-correlation-id"] == "cid-real"


class TestProxyFailureModes:
    def test_unreachable_upstream_degrades_to_503(self) -> None:
        """A dead upstream must degrade fast and honestly, not hang or 500.

        The gateway cannot reach ``market-data`` in a unit test (no docker
        network), which is exactly the condition this asserts on.
        """

        with TestClient(app) as c:
            response = c.get("/v1/market-data/health/live")

        assert response.status_code == 503
        body = response.json()
        assert body["error"] == "upstream_unavailable"
        assert body["service"] == "market-data"
        # The correlation id must survive the failure so the caller can trace it.
        assert body["correlation_id"]
        assert response.headers.get("X-Correlation-ID") == body["correlation_id"]

    def test_proxy_to_unknown_service_never_reaches_upstream(self, client: TestClient) -> None:
        assert client.get("/v1/not-a-service/x").status_code == 404


class TestZoneAssignment:
    def test_money_path_services_are_in_execution_or_decision_zone(self) -> None:
        registry = RouteRegistry()
        for name in ("oms", "live-trading-engine"):
            assert registry.get(name).zone in (Zone.EXECUTION, Zone.DECISION)  # type: ignore[union-attr]

    def test_research_services_are_public(self) -> None:
        registry = RouteRegistry()
        for name in ("market-data", "feature-store"):
            assert registry.get(name).exposure is Exposure.PUBLIC  # type: ignore[union-attr]
