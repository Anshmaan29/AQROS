from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from aqros_live_trading.app import app


@pytest.fixture
def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_health_live(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code in (200, 503)
    if response.status_code == 200:
        data = response.json()
        assert data["service"] == "live-trading-engine"


@pytest.mark.asyncio
async def test_health_live_endpoint(client: AsyncClient) -> None:
    response = await client.get("/health/live")
    assert response.status_code in (200, 503)


@pytest.mark.asyncio
async def test_health_ready(client: AsyncClient) -> None:
    response = await client.get("/health/ready")
    assert response.status_code in (200, 503)
