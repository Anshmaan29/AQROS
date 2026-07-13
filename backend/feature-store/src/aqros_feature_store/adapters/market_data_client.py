"""HTTP client implementation of the ``MarketDataSource`` port.

This is the boundary-respecting way the Feature Store reads OHLCV data: it
calls the Market Data Service's *published* REST API
(``GET /v1/instruments/{symbol}/bars``) over HTTP, exactly as any other
external client would. It never imports market-data's Python packages,
opens a connection to market-data's Postgres database, or otherwise reaches
into that service's internals — CLAUDE.md §7.9 and
``docs/AI_QUANT_PLATFORM_BLUEPRINT.md`` §2 both forbid that class of
coupling ("a service importing another service's `domain/` fails the build").

Paginates through market-data's ``limit``/``offset`` API and retries
transient failures with backoff, mirroring the retry discipline already
established in ``aqros_market_data``'s own provider adapter.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import httpx
import structlog

from aqros_feature_store.config import Settings
from aqros_feature_store.domain.models import BarInterval, OHLCVBar
from aqros_feature_store.domain.ports import MarketDataSource, MarketDataSourceError

logger = structlog.get_logger(__name__)


class HttpMarketDataSource(MarketDataSource):
    """Reads OHLCV bars from the Market Data Service's REST API."""

    def __init__(self, client: httpx.AsyncClient, settings: Settings) -> None:
        self._client = client
        self._page_size = settings.market_data_page_size
        self._max_retries = settings.market_data_max_retries
        self._retry_backoff = settings.market_data_retry_backoff_seconds

    async def get_bars(
        self,
        symbol: str,
        *,
        start: date | None = None,
        end: date | None = None,
        interval: BarInterval = BarInterval.DAILY,
    ) -> list[OHLCVBar]:
        bars: list[OHLCVBar] = []
        offset = 0

        while True:
            page = await self._fetch_page(
                symbol, start=start, end=end, interval=interval, offset=offset
            )
            page_bars = [self._to_domain_bar(item) for item in page["bars"]]
            bars.extend(page_bars)

            if len(page_bars) < self._page_size or len(bars) >= page["total"]:
                break
            offset += self._page_size

        return bars

    async def _fetch_page(
        self,
        symbol: str,
        *,
        start: date | None,
        end: date | None,
        interval: BarInterval,
        offset: int,
    ) -> dict[str, Any]:
        params: dict[str, str | int] = {
            "interval": interval.value,
            "limit": self._page_size,
            "offset": offset,
        }
        if start is not None:
            params["start"] = start.isoformat()
        if end is not None:
            params["end"] = end.isoformat()

        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                response = await self._client.get(
                    f"/v1/instruments/{symbol.upper()}/bars", params=params
                )
                response.raise_for_status()
                data: dict[str, Any] = response.json()
                return data
            except httpx.HTTPStatusError as exc:
                # A 404 means market-data has never heard of this symbol —
                # that's a legitimate "no data" outcome, not a transient
                # failure, so it is not retried.
                if exc.response.status_code == 404:
                    return {"bars": [], "total": 0}
                last_error = exc
            except httpx.HTTPError as exc:
                last_error = exc

            logger.warning(
                "market_data_client.fetch_failed",
                symbol=symbol,
                attempt=attempt,
                max_retries=self._max_retries,
                error=str(last_error),
            )
            if attempt < self._max_retries:
                await asyncio.sleep(self._retry_backoff * attempt)

        raise MarketDataSourceError(
            f"Failed to fetch bars for {symbol} from the Market Data Service "
            f"after {self._max_retries} attempts"
        ) from last_error

    @staticmethod
    def _to_domain_bar(item: dict[str, Any]) -> OHLCVBar:
        adjusted_close = item.get("adjusted_close")
        return OHLCVBar(
            symbol=item["symbol"],
            event_time=_parse_datetime(item["event_time"]),
            interval=BarInterval(item["interval"]),
            open=Decimal(str(item["open"])),
            high=Decimal(str(item["high"])),
            low=Decimal(str(item["low"])),
            close=Decimal(str(item["close"])),
            adjusted_close=Decimal(str(adjusted_close)) if adjusted_close is not None else None,
            volume=int(item["volume"]),
            source=item["source"],
            knowledge_time=_parse_datetime(item["knowledge_time"]),
        )


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)
