"""Integration tests for the online (Redis-backed) feature store API.

Exercises the ``GET /v1/online/instruments/{symbol}/features`` and
``GET /v1/online/instruments/{symbol}/features/{feature_name}`` endpoints
against a real Redis instance (via ``testcontainers``).

Skips the module if Docker is not available.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

try:
    import docker as _docker

    _docker.from_env().ping()
except Exception:
    pytest.skip("Docker is required for integration tests", allow_module_level=True)

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from redis import asyncio as aioredis
from testcontainers.redis import RedisContainer

from aqros_feature_store.adapters.online_redis import RedisOnlineFeatureStore
from aqros_feature_store.domain.online_service import OnlineFeatureService

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def redis_container() -> AsyncIterator[RedisContainer]:
    with RedisContainer("redis:7-alpine") as container:
        yield container


@pytest_asyncio.fixture
async def online_store(
    redis_container: RedisContainer,
) -> AsyncIterator[RedisOnlineFeatureStore]:
    url = redis_container.get_connection_url()
    client = aioredis.from_url(url, max_connections=5)
    store = RedisOnlineFeatureStore(client)
    try:
        yield store
    finally:
        await client.aclose()


@pytest_asyncio.fixture
async def online_service(online_store: RedisOnlineFeatureStore) -> OnlineFeatureService:
    return OnlineFeatureService(online_store)


@pytest_asyncio.fixture
async def client(
    online_store: RedisOnlineFeatureStore,
) -> AsyncIterator[AsyncClient]:
    """Build an ASGI test client with the Redis-backed online store wired in."""
    from aqros_feature_store.app import app

    app.state.online_feature_store = online_store

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client

    app.dependency_overrides.clear()


async def test_online_snapshot_returns_stored_features(client: AsyncClient) -> None:
    # Seed values directly into the online store via the API's underlying
    # service dependency.
    from aqros_feature_store.api.deps import get_online_store

    store = get_online_store(client._transport.app)  # type: ignore[arg-type]
    await store.set_snapshot("AAPL", {"sma_20": 42.5, "rsi_14": 65.3})

    resp = await client.get("/v1/online/instruments/AAPL/features")
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "AAPL"
    assert body["feature_count"] == 2
    assert body["features"]["sma_20"] == 42.5
    assert body["features"]["rsi_14"] == 65.3


async def test_online_snapshot_normalises_symbol_to_uppercase(client: AsyncClient) -> None:
    from aqros_feature_store.api.deps import get_online_store

    store = get_online_store(client._transport.app)  # type: ignore[arg-type]
    await store.set_snapshot("AAPL", {"sma_20": 42.5})

    resp = await client.get("/v1/online/instruments/aapl/features")
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "AAPL"


async def test_online_snapshot_empty_symbol_returns_empty_features(client: AsyncClient) -> None:
    resp = await client.get("/v1/online/instruments/UNKNOWN/features")
    assert resp.status_code == 200
    body = resp.json()
    assert body["feature_count"] == 0
    assert body["features"] == {}


async def test_online_feature_returns_stored_value(client: AsyncClient) -> None:
    from aqros_feature_store.api.deps import get_online_store

    store = get_online_store(client._transport.app)  # type: ignore[arg-type]
    await store.set_latest("AAPL", "sma_20", 42.5)

    resp = await client.get("/v1/online/instruments/AAPL/features/sma_20")
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "AAPL"
    assert body["feature_name"] == "sma_20"
    assert body["value"] == 42.5


async def test_online_feature_returns_404_for_missing_value(client: AsyncClient) -> None:
    resp = await client.get("/v1/online/instruments/AAPL/features/nonexistent")
    assert resp.status_code == 404


async def test_online_feature_normalises_symbol_to_uppercase(client: AsyncClient) -> None:
    from aqros_feature_store.api.deps import get_online_store

    store = get_online_store(client._transport.app)  # type: ignore[arg-type]
    await store.set_latest("AAPL", "sma_20", 42.5)

    resp = await client.get("/v1/online/instruments/aapl/features/sma_20")
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "AAPL"


async def test_online_overwrite_snapshot_returns_new_values(client: AsyncClient) -> None:
    from aqros_feature_store.api.deps import get_online_store

    store = get_online_store(client._transport.app)  # type: ignore[arg-type]
    await store.set_snapshot("AAPL", {"sma_20": 10.0})
    await store.set_snapshot("AAPL", {"sma_20": 20.0})

    resp = await client.get("/v1/online/instruments/AAPL/features")
    assert resp.status_code == 200
    assert resp.json()["features"]["sma_20"] == 20.0
