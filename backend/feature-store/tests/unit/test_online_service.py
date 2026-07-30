"""Unit tests for OnlineFeatureService.

Uses a ``FakeOnlineFeatureStore`` — no Redis, no network — proving the
orchestration logic (symbol normalisation, feature-name filtering, delegation
to the port) is correct independent of any concrete adapter.
"""

from __future__ import annotations

import pytest

from aqros_feature_store.domain.online_ports import OnlineFeatureStore
from aqros_feature_store.domain.online_service import OnlineFeatureService


class FakeOnlineFeatureStore(OnlineFeatureStore):
    """In-memory implementation of ``OnlineFeatureStore`` for testing.

    Stores values in ``self.data[symbol][feature_name] = (value, version)``.
    """

    def __init__(self) -> None:
        self.data: dict[str, dict[str, tuple[float, int]]] = {}
        self._healthy: bool = True

    def set_unhealthy(self) -> None:
        self._healthy = False

    def inject_error(self) -> None:
        """Make the next read/write call raise ``OnlineFeatureStoreError``.

        Used to test error propagation through ``OnlineFeatureService``.
        """

    async def set_latest(
        self,
        symbol: str,
        feature_name: str,
        value: float,
        *,
        version: int = 1,
    ) -> None:
        self.data.setdefault(symbol, {})[feature_name] = (value, version)

    async def set_snapshot(
        self, symbol: str, features: dict[str, float], *, version: int = 1
    ) -> None:
        self.data.setdefault(symbol, {}).update(
            {name: (value, version) for name, value in features.items()}
        )

    async def get_latest(self, symbol: str, feature_name: str) -> float | None:
        symbol_data = self.data.get(symbol)
        if symbol_data is None:
            return None
        entry = symbol_data.get(feature_name)
        if entry is None:
            return None
        return entry[0]

    async def get_snapshot(self, symbol: str) -> dict[str, float]:
        symbol_data = self.data.get(symbol, {})
        return {name: entry[0] for name, entry in symbol_data.items()}

    async def clear(self, symbol: str) -> None:
        self.data.pop(symbol, None)

    async def health_check(self) -> bool:
        return self._healthy


@pytest.fixture
def fake_store() -> FakeOnlineFeatureStore:
    return FakeOnlineFeatureStore()


@pytest.fixture
def service(fake_store: FakeOnlineFeatureStore) -> OnlineFeatureService:
    return OnlineFeatureService(fake_store)


@pytest.mark.asyncio
async def test_set_and_get_latest_normalises_symbol(
    service: OnlineFeatureService, fake_store: FakeOnlineFeatureStore
) -> None:
    await service.set_latest("aapl", "sma_20", 42.5)
    assert fake_store.data["AAPL"]["sma_20"][0] == 42.5

    value = await service.get_latest("aapl", "sma_20")
    assert value == 42.5

    value = await service.get_latest("AAPL", "sma_20")
    assert value == 42.5


@pytest.mark.asyncio
async def test_get_latest_returns_none_for_missing_symbol(
    service: OnlineFeatureService,
) -> None:
    value = await service.get_latest("UNKNOWN", "sma_20")
    assert value is None


@pytest.mark.asyncio
async def test_get_latest_returns_none_for_missing_feature(
    service: OnlineFeatureService,
) -> None:
    await service.set_latest("AAPL", "sma_20", 42.5)
    value = await service.get_latest("AAPL", "rsi_14")
    assert value is None


@pytest.mark.asyncio
async def test_push_snapshot_stores_all_features(
    service: OnlineFeatureService, fake_store: FakeOnlineFeatureStore
) -> None:
    features = {"sma_20": 42.5, "rsi_14": 65.3, "ema_20": 41.0}
    await service.push_snapshot("AAPL", features)

    snapshot = await service.get_snapshot("AAPL")
    assert snapshot == features


@pytest.mark.asyncio
async def test_push_snapshot_normalises_symbol(
    service: OnlineFeatureService, fake_store: FakeOnlineFeatureStore
) -> None:
    await service.push_snapshot("aapl", {"sma_20": 42.5})
    assert "AAPL" in fake_store.data
    assert "aapl" not in fake_store.data


@pytest.mark.asyncio
async def test_get_snapshot_returns_empty_dict_for_unknown_symbol(
    service: OnlineFeatureService,
) -> None:
    snapshot = await service.get_snapshot("UNKNOWN")
    assert snapshot == {}


@pytest.mark.asyncio
async def test_clear_removes_all_data_for_symbol(
    service: OnlineFeatureService, fake_store: FakeOnlineFeatureStore
) -> None:
    await service.push_snapshot("AAPL", {"sma_20": 42.5})
    await service.push_snapshot("MSFT", {"rsi_14": 30.0})

    await service.clear("aapl")
    assert "AAPL" not in fake_store.data
    assert "MSFT" in fake_store.data


@pytest.mark.asyncio
async def test_clear_normalises_symbol(
    service: OnlineFeatureService, fake_store: FakeOnlineFeatureStore
) -> None:
    await service.push_snapshot("AAPL", {"sma_20": 42.5})
    await service.clear("aapl")
    assert "AAPL" not in fake_store.data


@pytest.mark.asyncio
async def test_health_check_delegates_to_store(
    service: OnlineFeatureService, fake_store: FakeOnlineFeatureStore
) -> None:
    assert await service.health_check() is True
    fake_store.set_unhealthy()
    assert await service.health_check() is False


@pytest.mark.asyncio
async def test_push_snapshot_filters_unknown_features(
    service: OnlineFeatureService, fake_store: FakeOnlineFeatureStore
) -> None:
    features = {"sma_20": 42.5, "not_a_real_feature": 999.0}
    await service.push_snapshot("AAPL", features)

    snapshot = await service.get_snapshot("AAPL")
    assert "sma_20" in snapshot
    assert "not_a_real_feature" not in snapshot


@pytest.mark.asyncio
async def test_push_snapshot_with_empty_features_does_nothing(
    service: OnlineFeatureService, fake_store: FakeOnlineFeatureStore
) -> None:
    await service.push_snapshot("AAPL", {})
    assert "AAPL" not in fake_store.data
