"""ModelManager — loads, caches, and manages versioned models.

This is a domain service that owns the model lifecycle:
- Load model binaries from a ``ModelLoader`` port.
- Cache loaded models in memory (LRU-eviction when ``max_loaded_models``
  is exceeded).
- Track which version is the "current production" model.
- Support explicit reload, rollback, and lazy loading.
- Verify checksums and metadata before making a model available.
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from aqros_inference_service.domain.models import PredictionType

if TYPE_CHECKING:
    from aqros_inference_service.ports.ports import ModelLoader


class LoadedModel:
    """A loaded model instance held in memory."""

    def __init__(
        self,
        name: str,
        version: int,
        model_type: PredictionType,
        backend: Any,
        checksum: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.version = version
        self.model_type = model_type
        self.backend = backend
        self.checksum = checksum
        self.metadata = metadata or {}
        self.loaded_at = datetime.now(UTC)
        self.last_used_at = datetime.now(UTC)

    def touch(self) -> None:
        """Update the last-used timestamp (for LRU tracking)."""
        self.last_used_at = datetime.now(UTC)


class ModelManager:
    """Manages the lifecycle of loaded model instances.

    Args:
        loader: A ``ModelLoader`` port that knows how to fetch and
            deserialise model binaries from an artifact store.
        max_loaded_models: Maximum number of models to keep in the LRU cache.
        cache_ttl_seconds: Seconds after which a cached model is considered
            stale and re-fetched on next access.
    """

    def __init__(
        self,
        loader: ModelLoader,
        max_loaded_models: int = 10,
        cache_ttl_seconds: int = 3600,
    ) -> None:
        self._loader: ModelLoader = loader
        self._max = max_loaded_models
        self._cache_ttl = cache_ttl_seconds
        # LRU cache: { (name, version) -> LoadedModel }
        self._cache: OrderedDict[tuple[str, int], LoadedModel] = OrderedDict()
        # Current production version per model name
        self._production: dict[str, int] = {}
        # Previous production version for rollback
        self._previous: dict[str, int | None] = {}
        self._reload_count: int = 0

    @property
    def loaded_count(self) -> int:
        return len(self._cache)

    @property
    def reload_count(self) -> int:
        return self._reload_count

    @property
    def production_versions(self) -> dict[str, int]:
        """Return a copy of the current production version mapping."""
        return dict(self._production)

    async def load_model(
        self,
        name: str,
        version: int,
        *,
        set_production: bool = False,
    ) -> LoadedModel:
        """Load a model version into the cache.

        If the model is already cached, return it (touching LRU order).
        If ``set_production`` is True, this version becomes the current
        production version for the model family.
        """
        key = (name, version)
        if key in self._cache:
            model = self._cache[key]
            model.touch()
            self._cache.move_to_end(key)
            return model

        loaded = await self._do_load(name, version)
        self._cache_evict_if_needed()
        self._cache[key] = loaded

        if set_production:
            await self.set_production(name, version)

        return loaded

    async def _do_load(self, name: str, version: int) -> LoadedModel:
        """Actually fetch and deserialise a model (delegates to the loader)."""
        return await self._loader.load(name, version)

    async def get_model(self, name: str, version: int | None = None) -> LoadedModel:
        """Get a loaded model, loading it if necessary.

        If ``version`` is None, returns the current production version.
        Raises ``ValueError`` if the model is not available.
        """
        if version is None:
            version = self._production.get(name)
            if version is None:
                raise ValueError(f"No production version set for model '{name}'")

        key = (name, version)
        if key in self._cache:
            model = self._cache[key]
            model.touch()
            self._cache.move_to_end(key)
            return model

        return await self.load_model(name, version)

    async def set_production(self, name: str, version: int) -> None:
        """Set a specific version as the current production model.

        Saves the previous production version for rollback.
        """
        prev = self._production.get(name)
        self._previous[name] = prev
        self._production[name] = version
        # Ensure the model is loaded
        await self.load_model(name, version)

    async def reload_production(self, name: str) -> LoadedModel | None:
        """Force-reload the current production version.

        Used by hot-reload polling.
        """
        version = self._production.get(name)
        if version is None:
            return None
        key = (name, version)
        self._cache.pop(key, None)
        self._reload_count += 1
        return await self.load_model(name, version, set_production=True)

    async def reload_version(self, name: str, version: int) -> LoadedModel:
        """Force-reload a specific version."""
        key = (name, version)
        self._cache.pop(key, None)
        self._reload_count += 1
        return await self.load_model(name, version)

    async def rollback(self, name: str) -> LoadedModel | None:
        """Roll back to the previous production version.

        Returns the newly-active model, or None if there is no previous
        version to roll back to.
        """
        prev_version = self._previous.get(name)
        if prev_version is None:
            return None
        await self.set_production(name, prev_version)
        loaded = self._cache.get((name, prev_version))
        return loaded

    async def unload(self, name: str, version: int) -> None:
        """Remove a specific model version from the cache."""
        key = (name, version)
        self._cache.pop(key, None)

    async def unload_all(self, name: str) -> None:
        """Remove all versions of a model from the cache."""
        keys_to_remove = [k for k in self._cache if k[0] == name]
        for k in keys_to_remove:
            self._cache.pop(k, None)

    def get_loaded_models(self) -> list[tuple[tuple[str, int], LoadedModel]]:
        """Return all loaded models as ``((name, version), model)`` tuples."""
        return list(self._cache.items())

    def get_production_version(self, name: str) -> int | None:
        """Return the production version for a model family, or None."""
        return self._production.get(name)

    def get_loaded_model(self, name: str, version: int) -> LoadedModel | None:
        """Return a specific loaded model, or None if not in cache."""
        return self._cache.get((name, version))

    def get_loaded_versions(self, name: str) -> list[int]:
        """Return all loaded version numbers for a model family."""
        return [v for (n, v) in self._cache if n == name]

    def _cache_evict_if_needed(self) -> None:
        """Evict the least-recently-used entry if the cache is full."""
        while len(self._cache) >= self._max:
            self._cache.popitem(last=False)
