"""HttpFeatureProvider — fetches online features from the Feature Store REST API.

Implements the ``FeatureProvider`` port. Talks to the Feature Store Service's
online REST API (``GET /v1/online/instruments/{symbol}/features``).
"""

from __future__ import annotations

from typing import Any

import httpx

from aqros_inference_service.ports.ports import FeatureProvider


class HttpFeatureProvider(FeatureProvider):
    """Fetches online feature values from the Feature Store HTTP API.

    Args:
        client: An ``httpx.AsyncClient`` pointed at the Feature Store.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def get_features(self, symbol: str) -> dict[str, Any]:
        try:
            resp = await self._client.get(
                f"/v1/online/instruments/{symbol.upper()}/features",
            )
            if resp.status_code == 404:
                return {}
            resp.raise_for_status()
            body = resp.json()
            features = body.get("features", {})
            return dict(features)
        except httpx.HTTPError:
            return {}

    async def health_check(self) -> bool:
        try:
            resp = await self._client.get("/health/live")
            return resp.is_success
        except httpx.HTTPError:
            return False
