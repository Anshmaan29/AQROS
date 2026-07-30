"""OnlineFeatureService — orchestrates online feature storage and retrieval.

This service wraps ``OnlineFeatureStore`` with lightweight business logic:
symbol normalisation, input validation, and batch snapshotting. It is the
read/write surface for the online (Redis) feature path, mirroring the offline
``FeatureQueryService`` for the offline (Postgres) path.

The service is pure orchestration — all I/O is delegated to the
``OnlineFeatureStore`` port, making it testable with a fake.
"""

from __future__ import annotations

from aqros_feature_store.domain.feature_definitions import FEATURE_REGISTRY
from aqros_feature_store.domain.online_ports import OnlineFeatureStore


class OnlineFeatureService:
    """Read/write service for the online (Redis) feature store.

    Usage::

        service = OnlineFeatureService(online_store)
        await service.push_snapshot("AAPL", {"sma_20": 42.5, "rsi_14": 65.3})
        value = await service.get_latest("AAPL", "sma_20")
        snapshot = await service.get_snapshot("AAPL")
    """

    def __init__(self, online_store: OnlineFeatureStore) -> None:
        self._store = online_store

    async def set_latest(
        self,
        symbol: str,
        feature_name: str,
        value: float,
        *,
        version: int = 1,
    ) -> None:
        """Store a single latest feature value.

        The ``symbol`` is normalised to uppercase.
        """
        await self._store.set_latest(symbol.upper(), feature_name, value, version=version)

    async def push_snapshot(
        self,
        symbol: str,
        features: dict[str, float],
        *,
        version: int = 1,
    ) -> None:
        """Atomically store all feature values for a symbol.

        ``symbol`` is normalised to uppercase. Only feature names that exist
        in the registry are stored (unknown names are silently skipped).
        """
        symbol = symbol.upper()
        known_names: set[str] = set()
        for reg in FEATURE_REGISTRY:
            known_names.add(reg.definition.name)
        filtered = {k: v for k, v in features.items() if k in known_names}
        if filtered:
            await self._store.set_snapshot(symbol, filtered, version=version)

    async def get_latest(self, symbol: str, feature_name: str) -> float | None:
        """Return the most recently stored value, or ``None``."""
        return await self._store.get_latest(symbol.upper(), feature_name)

    async def get_snapshot(self, symbol: str) -> dict[str, float]:
        """Return all stored latest feature values for ``symbol``."""
        return await self._store.get_snapshot(symbol.upper())

    async def clear(self, symbol: str) -> None:
        """Remove all stored values for ``symbol``."""
        await self._store.clear(symbol.upper())

    async def health_check(self) -> bool:
        """Verify the backing store is reachable."""
        return await self._store.health_check()
