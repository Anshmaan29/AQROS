"""HttpOfflineFeatureProvider — reads offline features from the Feature Store REST API.

Uses ``httpx.AsyncClient`` (the same pattern as ``market_data_client.py`` in
the feature store) to fetch feature values from the offline (Postgres-backed)
Feature Store Service.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

import httpx

from aqros_parity_monitor.ports.ports import OfflineFeatureProvider


class HttpOfflineFeatureProvider(OfflineFeatureProvider):
    """Reads offline feature values from the Feature Store HTTP API.

    Args:
        client: An ``httpx.AsyncClient`` pointed at the Feature Store.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def get_snapshot(
        self, symbol: str, *, as_of: datetime | None = None
    ) -> Mapping[str, Any]:
        params: dict[str, str] = {}
        if as_of is not None:
            params["as_of"] = as_of.isoformat()
        try:
            resp = await self._client.get(
                f"/v1/instruments/{symbol.upper()}/features",
                params=params,
            )
            resp.raise_for_status()
            body = resp.json()
            # Feature Store returns {"values": [...], "total": N}
            raw = body.get("values", [])
            return {
                item.get("feature_name", ""): item.get("value")
                for item in raw
                if item.get("feature_name")
            }
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return {}
            raise
        except httpx.HTTPError:
            return {}

    async def get_feature_value(
        self, symbol: str, feature_name: str, *, as_of: datetime | None = None
    ) -> Any | None:
        params: dict[str, str] = {}
        if as_of is not None:
            params["as_of"] = as_of.isoformat()
        try:
            resp = await self._client.get(
                f"/v1/instruments/{symbol.upper()}/features/{feature_name}",
                params=params,
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            body = resp.json()
            return body.get("value")
        except httpx.HTTPError:
            return None

    async def get_feature_version(self, symbol: str, feature_name: str) -> int | None:
        try:
            resp = await self._client.get(
                f"/v1/instruments/{symbol.upper()}/features/{feature_name}",
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            body = resp.json()
            version = body.get("feature_version")
            return int(version) if version is not None else None
        except httpx.HTTPError:
            return None
