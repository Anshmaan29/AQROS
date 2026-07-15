"""Health/readiness composition tests (task 9.5, Requirement 24.1)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def health_client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    from aqros_model_registry.app import app, health_registry

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        test_client.health_registry = health_registry  # type: ignore[attr-defined]
        yield test_client


async def test_all_checks_healthy_returns_200(health_client: AsyncClient) -> None:
    registry = health_client.health_registry  # type: ignore[attr-defined]
    registry.register("database", lambda: True)
    registry.register("artifact_store", lambda: True)
    resp = await health_client.get("/health/ready")
    assert resp.status_code == 200


async def test_one_check_failing_returns_503(health_client: AsyncClient) -> None:
    registry = health_client.health_registry  # type: ignore[attr-defined]
    registry.register("database", lambda: True)
    registry.register("artifact_store", lambda: False)
    resp = await health_client.get("/health/ready")
    assert resp.status_code == 503
