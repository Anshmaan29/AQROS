"""StrategyManager — registers, versions, and manages active strategies."""

from __future__ import annotations

from aqros_strategy_engine.domain.models import StrategyMetadata
from aqros_strategy_engine.domain.strategy import Strategy


class StrategyAlreadyRegisteredError(ValueError): ...


class StrategyNotFoundError(ValueError): ...


class StrategyManager:
    """Manages the lifecycle of strategy instances.

    Supports register, unregister, reload, versioning, and active-strategy
    tracking.
    """

    def __init__(self) -> None:
        self._strategies: dict[str, list[Strategy]] = {}
        self._active: dict[str, Strategy] = {}
        self._reload_count: int = 0

    @property
    def reload_count(self) -> int:
        return self._reload_count

    @property
    def strategy_names(self) -> list[str]:
        return list(self._strategies.keys())

    @property
    def active_strategies(self) -> dict[str, Strategy]:
        return dict(self._active)

    def register(self, strategy: Strategy) -> None:
        name = strategy.name
        if name not in self._strategies:
            self._strategies[name] = []
        existing_versions = {s.version for s in self._strategies[name]}
        if strategy.version in existing_versions:
            raise StrategyAlreadyRegisteredError(
                f"Strategy '{name}' version '{strategy.version}' already registered"
            )
        self._strategies[name].append(strategy)
        if name not in self._active:
            self._active[name] = strategy

    def unregister(self, name: str, version: str | None = None) -> None:
        if name not in self._strategies:
            raise StrategyNotFoundError(f"Strategy '{name}' not found")
        if version is None:
            del self._strategies[name]
            self._active.pop(name, None)
        else:
            self._strategies[name] = [s for s in self._strategies[name] if s.version != version]
            if not self._strategies[name]:
                del self._strategies[name]
                self._active.pop(name, None)

    def get_strategy(self, name: str, version: str | None = None) -> Strategy:
        if name not in self._strategies:
            raise StrategyNotFoundError(f"Strategy '{name}' not found")
        if version is not None:
            for s in self._strategies[name]:
                if s.version == version:
                    return s
            raise StrategyNotFoundError(f"Strategy '{name}' version '{version}' not found")
        active = self._active.get(name)
        if active is not None:
            return active
        if self._strategies[name]:
            self._active[name] = self._strategies[name][-1]
            return self._active[name]
        raise StrategyNotFoundError(f"Strategy '{name}' has no instances")

    def set_active(self, name: str, version: str) -> Strategy:
        for s in self._strategies.get(name, []):
            if s.version == version:
                self._active[name] = s
                return s
        raise StrategyNotFoundError(f"Strategy '{name}' version '{version}' not found")

    def reload_strategy(self, name: str, version: str | None = None) -> bool:
        if name not in self._strategies:
            return False
        self._reload_count += 1
        if version is not None:
            for s in self._strategies[name]:
                if s.version == version:
                    self._active[name] = s
                    return True
            return False
        if self._active.get(name) is not None:
            return True
        if self._strategies[name]:
            self._active[name] = self._strategies[name][-1]
            return True
        return False

    def list_strategies(self) -> list[StrategyMetadata]:
        result: list[StrategyMetadata] = []
        for _name, versions in self._strategies.items():
            for s in versions:
                meta = s.metadata()
                result.append(meta)
        return sorted(result, key=lambda m: (m.name, m.version))

    def get_strategy_metadata(self, name: str) -> StrategyMetadata | None:
        strategy = self._active.get(name)
        if strategy is not None:
            return strategy.metadata()
        versions = self._strategies.get(name, [])
        if versions:
            return versions[-1].metadata()
        return None
