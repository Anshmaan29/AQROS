"""RedisOnlineFeatureProvider — reads online feature values from Redis.

This adapter reads from the same Redis instance that the Feature Store's
online store writes to. It uses the same key schema
(``feature:snapshot:{symbol}``) defined in the ``RedisOnlineFeatureStore``
adapter.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from aqros_parity_monitor.ports.ports import OnlineFeatureProvider

_SNAPSHOT_KEY_PREFIX = "feature:snapshot"


class RedisOnlineFeatureProvider(OnlineFeatureProvider):
    """Reads online feature values from Redis.

    Args:
        redis_client: An async ``redis.Redis`` instance.
    """

    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client

    @staticmethod
    def _snapshot_key(symbol: str) -> str:
        return f"{_SNAPSHOT_KEY_PREFIX}:{symbol}"

    @staticmethod
    def _decode_value(raw: str) -> float | None:
        try:
            obj = json.loads(raw)
            return float(obj["v"])
        except (ValueError, TypeError, KeyError):
            return None

    async def get_snapshot(self, symbol: str) -> Mapping[str, Any]:
        raw = await self._redis.hgetall(self._snapshot_key(symbol.upper()))
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

    async def get_feature_value(self, symbol: str, feature_name: str) -> Any | None:
        raw = await self._redis.hget(self._snapshot_key(symbol.upper()), feature_name)
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return self._decode_value(raw)

    async def get_feature_timestamp(self, symbol: str, feature_name: str) -> datetime | None:
        # Redis hashes don't natively track per-field timestamps; return
        # the current time as a best-effort approximation. For production
        # use, consider a sorted-set-based design or a separate TTL set.
        raw = await self._redis.hget(self._snapshot_key(symbol.upper()), feature_name)
        if raw is None:
            return None
        return datetime.now(UTC)

    async def health_check(self) -> bool:
        try:
            await self._redis.ping()
            return True
        except Exception:
            return False
