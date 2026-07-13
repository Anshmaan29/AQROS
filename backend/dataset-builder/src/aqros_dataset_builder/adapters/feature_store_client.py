"""HTTP client implementation of the ``FeatureSource`` port.

Reads engineered feature values from the Feature Store Service's
*published* REST API (``GET /v1/instruments/{symbol}/features/{name}``) over
HTTP — never that service's Python packages or database (CLAUDE.md §7.9).
These values become the X matrix; unlike the OHLCV bars read via
``market_data_client.py``, they must stay strictly causal (Feature Store's
own indicators are already computed this way, so no additional PIT
enforcement is needed here beyond trusting that boundary).
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from typing import Any

import httpx
import structlog

from aqros_dataset_builder.config import Settings
from aqros_dataset_builder.domain.models import FeatureValue
from aqros_dataset_builder.domain.ports import FeatureSource, UpstreamSourceError

logger = structlog.get_logger(__name__)


class HttpFeatureSource(FeatureSource):
    """Reads engineered feature values from the Feature Store Service's REST API."""

    def __init__(self, client: httpx.AsyncClient, settings: Settings) -> None:
        self._client = client
        self._page_size = settings.upstream_page_size
        self._max_retries = settings.upstream_max_retries
        self._retry_backoff = settings.upstream_retry_backoff_seconds

    async def get_feature_values(
        self,
        symbol: str,
        feature_name: str,
        *,
        start: date | None = None,
        end: date | None = None,
    ) -> list[FeatureValue]:
        values: list[FeatureValue] = []
        offset = 0

        while True:
            page = await self._fetch_page(symbol, feature_name, start=start, end=end, offset=offset)
            page_values = [self._to_domain_value(item) for item in page["values"]]
            values.extend(page_values)

            if len(page_values) < self._page_size or len(values) >= page["total"]:
                break
            offset += self._page_size

        return values

    async def _fetch_page(
        self,
        symbol: str,
        feature_name: str,
        *,
        start: date | None,
        end: date | None,
        offset: int,
    ) -> dict[str, Any]:
        params: dict[str, str | int] = {"limit": self._page_size, "offset": offset}
        if start is not None:
            params["start"] = start.isoformat()
        if end is not None:
            params["end"] = end.isoformat()

        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                response = await self._client.get(
                    f"/v1/instruments/{symbol.upper()}/features/{feature_name}", params=params
                )
                response.raise_for_status()
                data: dict[str, Any] = response.json()
                return data
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    return {"values": [], "total": 0}
                last_error = exc
            except httpx.HTTPError as exc:
                last_error = exc

            logger.warning(
                "feature_store_client.fetch_failed",
                symbol=symbol,
                feature_name=feature_name,
                attempt=attempt,
                max_retries=self._max_retries,
                error=str(last_error),
            )
            if attempt < self._max_retries:
                await asyncio.sleep(self._retry_backoff * attempt)

        raise UpstreamSourceError(
            f"Failed to fetch feature '{feature_name}' for {symbol} from the Feature "
            f"Store Service after {self._max_retries} attempts"
        ) from last_error

    @staticmethod
    def _to_domain_value(item: dict[str, Any]) -> FeatureValue:
        return FeatureValue(
            symbol=item["symbol"],
            feature_name=item["feature_name"],
            feature_version=item["feature_version"],
            event_time=datetime.fromisoformat(item["event_time"]),
            value=float(item["value"]),
            knowledge_time=datetime.fromisoformat(item["knowledge_time"]),
        )
