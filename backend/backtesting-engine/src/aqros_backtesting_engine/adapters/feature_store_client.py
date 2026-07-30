"""HTTP adapter for the Feature Store's read-only REST API."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx

from aqros_backtesting_engine.domain.models import FeatureValue
from aqros_backtesting_engine.domain.ports import (
    FeatureStoreClient,
    FeatureStoreUnavailableError,
)


class HttpFeatureStoreClient(FeatureStoreClient):
    """Retrieve point-in-time feature values through the published REST API.

    The HTTP client is injected so transport configuration and lifecycle remain
    outside this adapter.  The adapter deliberately imports no Feature Store
    implementation types; its boundary is the published JSON representation.
    """

    def __init__(self, client: httpx.AsyncClient, *, page_size: int = 1000) -> None:
        if page_size < 1:
            raise ValueError("page_size must be positive")
        self._client = client
        self._page_size = page_size

    async def get_feature_values(
        self,
        symbol: str,
        feature_name: str,
        feature_version: int,
        start: datetime,
        end: datetime,
        as_of: datetime,
    ) -> list[FeatureValue]:
        """Fetch every page, rejecting malformed or look-ahead values."""
        values: list[FeatureValue] = []
        offset = 0
        encoded_symbol = quote(symbol, safe="")
        encoded_feature_name = quote(feature_name, safe="")

        while True:
            payload = await self._request(
                f"/v1/instruments/{encoded_symbol}/features/{encoded_feature_name}",
                params={
                    "feature_version": feature_version,
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "as_of": as_of.isoformat(),
                    "limit": self._page_size,
                    "offset": offset,
                },
            )
            page_payload, total = self._page(payload)
            page = [
                self._feature_value_from_json(item, symbol, feature_name) for item in page_payload
            ]
            for value in page:
                try:
                    if value.knowledge_time > as_of:
                        raise FeatureStoreUnavailableError(
                            "Feature Store returned a value after the as_of cutoff"
                        )
                except TypeError as exc:
                    raise FeatureStoreUnavailableError(
                        "Feature Store returned incomparable timestamps"
                    ) from exc
            values.extend(page)
            offset += len(page)
            if (
                not page
                or (isinstance(total, int) and offset >= total)
                or len(page) < self._page_size
            ):
                break

        return sorted(values, key=lambda value: value.event_time)

    async def _request(
        self,
        path: str,
        *,
        params: dict[str, str | int],
    ) -> Any:
        try:
            response = await self._client.get(path, params=params)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise FeatureStoreUnavailableError(
                f"Feature Store request failed for {path}: {exc}"
            ) from exc

    @staticmethod
    def _page(payload: Any) -> tuple[list[Any], object]:
        if isinstance(payload, list):
            return payload, None
        if isinstance(payload, dict):
            page_payload = payload.get("values", [])
            if not isinstance(page_payload, list):
                raise FeatureStoreUnavailableError(
                    "Feature Store returned an invalid feature-values payload"
                )
            return page_payload, payload.get("total")
        raise FeatureStoreUnavailableError(
            "Feature Store returned an invalid feature-values payload"
        )

    @staticmethod
    def _feature_value_from_json(
        payload: Any,
        requested_symbol: str,
        requested_feature_name: str,
    ) -> FeatureValue:
        if not isinstance(payload, dict):
            raise FeatureStoreUnavailableError("Feature Store returned an invalid feature value")
        try:
            event_time = HttpFeatureStoreClient._timestamp(
                payload.get("event_time", payload.get("eventTime"))
            )
            knowledge_time = HttpFeatureStoreClient._timestamp(
                payload.get("knowledge_time", payload.get("knowledgeTime"))
            )
            feature_version_raw = payload.get("feature_version", payload.get("featureVersion"))
            if feature_version_raw is None:
                raise FeatureStoreUnavailableError(
                    "Feature Store returned an invalid feature value"
                )
            return FeatureValue(
                symbol=str(payload.get("symbol", requested_symbol)),
                feature_name=str(
                    payload.get("feature_name", payload.get("featureName", requested_feature_name))
                ),
                feature_version=int(feature_version_raw),
                event_time=event_time,
                value=float(payload["value"]),
                knowledge_time=knowledge_time,
            )
        except (KeyError, ValueError, TypeError, OverflowError) as exc:
            raise FeatureStoreUnavailableError(
                "Feature Store returned an invalid feature value"
            ) from exc

    @staticmethod
    def _timestamp(value: Any) -> datetime:
        if isinstance(value, datetime):
            return value
        if not isinstance(value, str):
            raise FeatureStoreUnavailableError(
                "Feature Store returned a missing or invalid timestamp"
            )
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise FeatureStoreUnavailableError(
                f"Invalid Feature Store timestamp: {value!r}"
            ) from exc
