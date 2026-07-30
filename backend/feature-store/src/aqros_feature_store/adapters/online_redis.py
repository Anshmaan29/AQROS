"""RedisOnlineFeatureStore — a Redis-backed ``OnlineFeatureStore`` adapter.

Uses the ``redis`` (redis-py) async client. Feature snapshots are stored as
Redis hashes keyed by ``feature:snapshot:{symbol}`` — each hash field is a
feature name, each field value is the JSON-encoded feature data.

Key design decisions:
- One hash per symbol enables O(1) snapshot reads (``HGETALL``) and atomic
  partial updates (``HSET``) in a single round-trip.
- Metadata (version, timestamps) is kept alongside the value in a compact JSON
  payload so a single ``HGETALL`` returns everything a consumer needs without
  a second lookup.
- No TTLs are set by default — feature values persist until explicitly
  overwritten or cleared. Consumers/applications may set a TTL policy at a
  higher layer if needed (e.g. via ``EXPIRE`` after ``SET``).
"""

from __future__ import annotations

import json
from typing import Any

from aqros_feature_store.domain.online_ports import (
    OnlineFeatureStore,
    OnlineFeatureStoreError,
)

_SNAPSHOT_KEY_PREFIX = "feature:snapshot"
_REDIS_TIMEOUT_S = 5.0


class RedisOnlineFeatureStore(OnlineFeatureStore):
    """Redis-backed ``OnlineFeatureStore``.

    Args:
        redis_client: An async ``redis.Redis`` instance (``redis.asyncio``).
    """

    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client

    @staticmethod
    def _snapshot_key(symbol: str) -> str:
        return f"{_SNAPSHOT_KEY_PREFIX}:{symbol}"

    @staticmethod
    def _encode_value(value: float, version: int) -> str:
        return json.dumps({"v": value, "version": version}, separators=(",", ":"))

    @staticmethod
    def _decode_value(raw: str) -> float | None:
        try:
            obj = json.loads(raw)
            return float(obj["v"])
        except (ValueError, TypeError, KeyError):
            return None

    async def set_latest(
        self,
        symbol: str,
        feature_name: str,
        value: float,
        *,
        version: int = 1,
    ) -> None:
        try:
            encoded = self._encode_value(value, version)
            await self._redis.hset(
                self._snapshot_key(symbol),
                feature_name,
                encoded,
            )
        except Exception as exc:
            raise OnlineFeatureStoreError(
                f"set_latest({symbol}, {feature_name}) failed: {exc}"
            ) from exc

    async def set_snapshot(
        self, symbol: str, features: dict[str, float], *, version: int = 1
    ) -> None:
        try:
            mapping = {name: self._encode_value(value, version) for name, value in features.items()}
            if mapping:
                await self._redis.hset(
                    self._snapshot_key(symbol),
                    mapping=mapping,
                )
        except Exception as exc:
            raise OnlineFeatureStoreError(
                f"set_snapshot({symbol}, {len(features)} features) failed: {exc}"
            ) from exc

    async def get_latest(self, symbol: str, feature_name: str) -> float | None:
        try:
            raw = await self._redis.hget(self._snapshot_key(symbol), feature_name)
            if raw is None:
                return None
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            return self._decode_value(raw)
        except Exception as exc:
            raise OnlineFeatureStoreError(
                f"get_latest({symbol}, {feature_name}) failed: {exc}"
            ) from exc

    async def get_snapshot(self, symbol: str) -> dict[str, float]:
        try:
            raw = await self._redis.hgetall(self._snapshot_key(symbol))
            result: dict[str, float] = {}
            for key_bytes, value_bytes in raw.items():
                key = key_bytes.decode("utf-8") if isinstance(key_bytes, bytes) else key_bytes
                value_str = (
                    value_bytes.decode("utf-8") if isinstance(value_bytes, bytes) else value_bytes
                )
                decoded = self._decode_value(value_str)
                if decoded is not None:
                    result[key] = decoded
            return result
        except Exception as exc:
            raise OnlineFeatureStoreError(f"get_snapshot({symbol}) failed: {exc}") from exc

    async def clear(self, symbol: str) -> None:
        try:
            await self._redis.delete(self._snapshot_key(symbol))
        except Exception as exc:
            raise OnlineFeatureStoreError(f"clear({symbol}) failed: {exc}") from exc

    async def health_check(self) -> bool:
        try:
            await self._redis.ping()
            return True
        except Exception:
            return False
