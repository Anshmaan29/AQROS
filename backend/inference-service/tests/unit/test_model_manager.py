"""Unit tests for ModelManager (domain model lifecycle)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from aqros_inference_service.domain.model_manager import LoadedModel, ModelManager
from aqros_inference_service.domain.models import PredictionType


def _make_loaded(name: str = "default", version: int = 1) -> LoadedModel:
    return LoadedModel(
        name=name,
        version=version,
        model_type=PredictionType.REGRESSION,
        backend=MagicMock(),
        checksum=f"checksum-{name}-{version}",
        metadata={"feature_version": version},
    )


@pytest.fixture
def loader() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def manager(loader: AsyncMock) -> ModelManager:
    return ModelManager(loader=loader, max_loaded_models=3, cache_ttl_seconds=3600)


class TestLoadModel:
    async def test_load_new_model(self, manager: ModelManager, loader: AsyncMock) -> None:
        loader.load.return_value = _make_loaded("test", 1)
        loaded = await manager.load_model("test", 1)
        assert loaded.name == "test"
        assert loaded.version == 1
        assert ("test", 1) in manager._cache

    async def test_load_cached_model(self, manager: ModelManager, loader: AsyncMock) -> None:
        loader.load.return_value = _make_loaded("test", 1)
        await manager.load_model("test", 1)
        loader.load.assert_awaited_once()
        loaded2 = await manager.load_model("test", 1)
        assert loaded2.name == "test"


class TestGetModel:
    async def test_get_production_model(self, manager: ModelManager, loader: AsyncMock) -> None:
        loader.load.return_value = _make_loaded("test", 1)
        await manager.set_production("test", 1)
        model = await manager.get_model("test")
        assert model.version == 1

    async def test_get_production_not_set(self, manager: ModelManager) -> None:
        with pytest.raises(ValueError, match="No production version"):
            await manager.get_model("nonexistent")

    async def test_get_specific_version(self, manager: ModelManager, loader: AsyncMock) -> None:
        loader.load.return_value = _make_loaded("test", 2)
        model = await manager.get_model("test", 2)
        assert model.version == 2


class TestSetProduction:
    async def test_set_production_switches_version(
        self, manager: ModelManager, loader: AsyncMock
    ) -> None:
        loader.load.return_value = _make_loaded("test", 1)
        await manager.set_production("test", 1)
        assert manager._production["test"] == 1

        loader.load.return_value = _make_loaded("test", 2)
        await manager.set_production("test", 2)
        assert manager._production["test"] == 2
        assert manager._previous["test"] == 1

    async def test_set_production_loads_model(
        self, manager: ModelManager, loader: AsyncMock
    ) -> None:
        loader.load.return_value = _make_loaded("test", 1)
        await manager.set_production("test", 1)
        loader.load.assert_awaited_once()


class TestRollback:
    async def test_rollback_to_previous(self, manager: ModelManager, loader: AsyncMock) -> None:
        loader.load.return_value = _make_loaded("test", 1)
        await manager.set_production("test", 1)
        loader.load.return_value = _make_loaded("test", 2)
        await manager.set_production("test", 2)

        loader.load.return_value = _make_loaded("test", 1)
        await manager.rollback("test")
        assert manager._production["test"] == 1

    async def test_rollback_no_previous(self, manager: ModelManager) -> None:
        result = await manager.rollback("test")
        assert result is None


class TestReload:
    async def test_reload_production(self, manager: ModelManager, loader: AsyncMock) -> None:
        loader.load.return_value = _make_loaded("test", 1)
        await manager.set_production("test", 1)
        assert manager.loaded_count == 1

        loader.load.return_value = _make_loaded("test", 1)
        await manager.reload_production("test")
        assert manager.reload_count == 1

    async def test_reload_production_no_version(self, manager: ModelManager) -> None:
        result = await manager.reload_production("nonexistent")
        assert result is None


class TestUnload:
    async def test_unload_specific_version(self, manager: ModelManager, loader: AsyncMock) -> None:
        loader.load.return_value = _make_loaded("test", 1)
        await manager.load_model("test", 1)
        assert manager.loaded_count == 1
        await manager.unload("test", 1)
        assert manager.loaded_count == 0

    async def test_unload_all(self, manager: ModelManager, loader: AsyncMock) -> None:
        loader.load.return_value = _make_loaded("test", 1)
        await manager.load_model("test", 1)
        loader.load.return_value = _make_loaded("test", 2)
        await manager.load_model("test", 2)
        assert manager.loaded_count == 2
        await manager.unload_all("test")
        assert manager.loaded_count == 0


class TestLRUEviction:
    async def test_eviction_when_full(self, manager: ModelManager, loader: AsyncMock) -> None:
        for i in range(1, 5):
            loader.load.return_value = _make_loaded("test", i)
            await manager.load_model("test", i)
        # max_loaded_models=3, so after 4 loads, 1 should be evicted
        assert manager.loaded_count == 3
        assert ("test", 1) not in manager._cache
        assert ("test", 4) in manager._cache


class TestLoadedVersions:
    async def test_get_loaded_versions(self, manager: ModelManager, loader: AsyncMock) -> None:
        for v in [1, 2, 3]:
            loader.load.return_value = _make_loaded("test", v)
            await manager.load_model("test", v)
        versions = manager.get_loaded_versions("test")
        assert versions == [1, 2, 3]

    async def test_get_loaded_versions_empty(self, manager: ModelManager) -> None:
        assert manager.get_loaded_versions("unknown") == []


class TestLoadedModel:
    def test_touch_updates_last_used(self) -> None:
        model = _make_loaded("test", 1)
        old = model.last_used_at
        model.touch()
        assert model.last_used_at >= old

    def test_loaded_at_set_on_init(self) -> None:
        before = datetime.now(UTC)
        model = _make_loaded("test", 1)
        after = datetime.now(UTC)
        assert before <= model.loaded_at <= after
