"""OnlineFeatureStore — port for low-latency, Redis-backed feature serving.

The online feature store holds only the latest computed value per
(symbol, feature_name), indexed for fast point lookups and symbol-level
snapshots. It is populated after each computation run (offline → online sync)
so that online/offline parity is guaranteed by construction: the same
feature definitions, the same computation code, the same values.

This port intentionally mirrors ``FeatureValueRepository``'s write surface
but provides a *different* read surface optimised for hot-path lookups:
single-feature or whole-snapshot, always latest-known, never paginated,
never point-in-time (PIT queries use the offline store).
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class OnlineFeatureStore(ABC):
    """Port for reading and writing the latest feature values in Redis.

    Every call is async. Implementations must be thread-safe (Redis is
    single-threaded but connection pools require care). The implementation
    is expected to raise ``OnlineFeatureStoreError`` on connection or
    serialisation failures — never return a silently stale or empty result.
    """

    @abstractmethod
    async def set_latest(
        self,
        symbol: str,
        feature_name: str,
        value: float,
        *,
        version: int = 1,
    ) -> None:
        """Store the latest computed value for ``(symbol, feature_name)``.

        This overwrites any previously-stored latest value for the same key.
        Callers are responsible for ensuring ``value`` is finite (not NaN/Inf)
        — the online store trusts its inputs.
        """

    @abstractmethod
    async def set_snapshot(
        self, symbol: str, features: dict[str, float], *, version: int = 1
    ) -> None:
        """Atomically store a complete set of latest values for ``symbol``.

        ``features`` is a ``{feature_name: value}`` mapping. This is typically
        called after a computation run to push every computed value to the
        online store in a single round-trip.
        """

    @abstractmethod
    async def get_latest(self, symbol: str, feature_name: str) -> float | None:
        """Return the latest stored value for ``(symbol, feature_name)``.

        Returns ``None`` if no value has ever been stored for this key.
        """

    @abstractmethod
    async def get_snapshot(self, symbol: str) -> dict[str, float]:
        """Return all stored latest feature values for ``symbol``.

        Returns an empty dict if no features have been stored for this symbol.
        """

    @abstractmethod
    async def clear(self, symbol: str) -> None:
        """Remove all stored values for ``symbol``.

        Useful in tests and for resetting state.
        """

    @abstractmethod
    async def health_check(self) -> bool:
        """Return ``True`` if the backing store is reachable and responsive."""


class OnlineFeatureStoreError(RuntimeError):
    """Raised when an ``OnlineFeatureStore`` operation fails."""
