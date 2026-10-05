"""Unit tests for StrategyManager."""

from __future__ import annotations

import pytest
from aqros_strategy_engine.domain.strategies.implementations import DummyStrategy
from aqros_strategy_engine.domain.strategy_manager import (
    StrategyAlreadyRegisteredError,
    StrategyManager,
    StrategyNotFoundError,
)


class TestRegister:
    def test_register_new_strategy(self) -> None:
        mgr = StrategyManager()
        mgr.register(DummyStrategy())
        assert "dummy" in mgr.strategy_names

    def test_register_duplicate_version_raises(self) -> None:
        mgr = StrategyManager()
        mgr.register(DummyStrategy(version="1.0"))
        with pytest.raises(StrategyAlreadyRegisteredError):
            mgr.register(DummyStrategy(version="1.0"))

    def test_register_different_version(self) -> None:
        mgr = StrategyManager()
        mgr.register(DummyStrategy(version="1.0"))
        mgr.register(DummyStrategy(version="2.0"))
        assert len(mgr._strategies["dummy"]) == 2


class TestUnregister:
    def test_unregister_all_versions(self) -> None:
        mgr = StrategyManager()
        mgr.register(DummyStrategy())
        mgr.unregister("dummy")
        assert "dummy" not in mgr.strategy_names

    def test_unregister_specific_version(self) -> None:
        mgr = StrategyManager()
        mgr.register(DummyStrategy(version="1.0"))
        mgr.register(DummyStrategy(version="2.0"))
        mgr.unregister("dummy", "1.0")
        assert len(mgr._strategies["dummy"]) == 1

    def test_unregister_unknown_raises(self) -> None:
        mgr = StrategyManager()
        with pytest.raises(StrategyNotFoundError):
            mgr.unregister("nonexistent")


class TestGetStrategy:
    def test_get_active_strategy(self) -> None:
        mgr = StrategyManager()
        mgr.register(DummyStrategy())
        s = mgr.get_strategy("dummy")
        assert s.name == "dummy"

    def test_get_specific_version(self) -> None:
        mgr = StrategyManager()
        mgr.register(DummyStrategy(version="1.0"))
        s = mgr.get_strategy("dummy", "1.0")
        assert s.version == "1.0"

    def test_get_unknown_raises(self) -> None:
        mgr = StrategyManager()
        with pytest.raises(StrategyNotFoundError):
            mgr.get_strategy("nonexistent")


class TestSetActive:
    def test_set_active_version(self) -> None:
        mgr = StrategyManager()
        mgr.register(DummyStrategy(version="1.0"))
        mgr.register(DummyStrategy(version="2.0"))
        mgr.set_active("dummy", "1.0")
        assert mgr._active["dummy"].version == "1.0"

    def test_set_active_unknown_raises(self) -> None:
        mgr = StrategyManager()
        mgr.register(DummyStrategy())
        with pytest.raises(StrategyNotFoundError):
            mgr.set_active("dummy", "99.0")


class TestReload:
    def test_reload_existing(self) -> None:
        mgr = StrategyManager()
        mgr.register(DummyStrategy())
        assert mgr.reload_strategy("dummy")

    def test_reload_nonexistent(self) -> None:
        mgr = StrategyManager()
        assert not mgr.reload_strategy("nonexistent")

    def test_reload_specific_version(self) -> None:
        mgr = StrategyManager()
        mgr.register(DummyStrategy(version="1.0"))
        mgr.register(DummyStrategy(version="2.0"))
        assert mgr.reload_strategy("dummy", "1.0")


class TestListStrategies:
    def test_list_returns_all(self) -> None:
        mgr = StrategyManager()
        mgr.register(DummyStrategy(version="1.0"))
        mgr.register(DummyStrategy(version="2.0"))
        metas = mgr.list_strategies()
        assert len(metas) == 2

    def test_list_empty(self) -> None:
        mgr = StrategyManager()
        assert mgr.list_strategies() == []


class TestActiveStrategies:
    def test_active_strategies_returns_dict(self) -> None:
        mgr = StrategyManager()
        mgr.register(DummyStrategy())
        active = mgr.active_strategies
        assert "dummy" in active

    def test_active_strategies_empty(self) -> None:
        mgr = StrategyManager()
        assert mgr.active_strategies == {}
