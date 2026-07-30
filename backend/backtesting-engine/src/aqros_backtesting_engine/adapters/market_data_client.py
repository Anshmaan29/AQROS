"""HTTP adapter for the Market Data Service's read-only REST API."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx

from aqros_backtesting_engine.domain.models import (
    AssetClass,
    Bar,
    CorporateAction,
    CorporateActionType,
    Instrument,
)
from aqros_backtesting_engine.domain.ports import (
    MarketDataClient,
    MarketDataUnavailableError,
)


class HttpMarketDataClient(MarketDataClient):
    """Retrieve market data through an injected :class:`httpx.AsyncClient`.

    The client is deliberately unaware of the Market Data Service's Python
    package. Its only contract with that service is the published JSON API.
    The injected client may be configured with a base URL by application
    wiring, which keeps this adapter straightforward to test.
    """

    def __init__(self, client: httpx.AsyncClient, *, page_size: int = 1000) -> None:
        if page_size < 1:
            raise ValueError("page_size must be positive")
        self._client = client
        self._page_size = page_size

    async def get_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: str,
    ) -> list[Bar]:
        """Fetch all pages of bars, returning them in event-time order."""
        bars: list[Bar] = []
        offset = 0
        while True:
            payload = await self._get(
                f"/v1/instruments/{symbol}/bars",
                params={
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "interval": interval,
                    "limit": self._page_size,
                    "offset": offset,
                },
            )
            if isinstance(payload, list):
                page_payload = payload
                total: object = None
            elif isinstance(payload, dict):
                page_payload = payload.get("bars", [])
                total = payload.get("total")
            else:
                raise MarketDataUnavailableError("Market Data returned an invalid bars payload")
            if not isinstance(page_payload, list):
                raise MarketDataUnavailableError("Market Data returned an invalid bars payload")

            page = [self._bar_from_json(item, symbol) for item in page_payload]
            bars.extend(page)
            offset += len(page)
            if (
                not page
                or (isinstance(total, int) and offset >= total)
                or len(page) < self._page_size
            ):
                break
        return sorted(bars, key=lambda bar: bar.event_time)

    async def get_instrument(self, symbol: str) -> Instrument:
        """Fetch and translate one instrument."""
        payload = await self._get(f"/v1/instruments/{symbol}")
        if not isinstance(payload, dict):
            raise MarketDataUnavailableError("Market Data returned an invalid instrument payload")
        return self._instrument_from_json(payload, symbol)

    async def get_corporate_actions(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        as_of: datetime,
    ) -> list[CorporateAction]:
        """Fetch corporate actions, or fail explicitly when no feed exists.

        No action is inferred from prices. A missing endpoint is treated as an
        unavailable feed, while all other HTTP/network failures retain the
        same typed error.
        """
        try:
            payload = await self._request(
                f"/v1/instruments/{symbol}/corporate-actions",
                params={
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "as_of": as_of.isoformat(),
                    "limit": self._page_size,
                    "offset": 0,
                },
            )
        except MarketDataUnavailableError as exc:
            raise MarketDataUnavailableError(
                f"Corporate-actions feed unavailable for {symbol}: {exc}"
            ) from exc
        if isinstance(payload, list):
            values = payload
        elif isinstance(payload, dict):
            values = payload.get("actions", [])
        else:
            raise MarketDataUnavailableError(
                "Market Data returned an invalid corporate-actions payload"
            )
        if not isinstance(values, list):
            raise MarketDataUnavailableError(
                "Market Data returned an invalid corporate-actions payload"
            )
        return [self._corporate_action_from_json(item, symbol) for item in values]

    async def _get(self, path: str, *, params: dict[str, str | int] | None = None) -> Any:
        return await self._request(path, params=params)

    async def _request(self, path: str, *, params: dict[str, str | int] | None = None) -> Any:
        try:
            response = await self._client.get(path, params=params)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise MarketDataUnavailableError(
                f"Market Data request failed for {path}: {exc}"
            ) from exc

    @staticmethod
    def _instrument_from_json(payload: dict[str, Any], requested_symbol: str) -> Instrument:
        asset_class = str(payload.get("asset_class", payload.get("assetClass", "equity"))).lower()
        try:
            parsed_asset_class = AssetClass(asset_class)
        except ValueError:
            parsed_asset_class = AssetClass.EQUITY
        return Instrument(
            symbol=str(payload.get("symbol", requested_symbol)).upper(),
            asset_class=parsed_asset_class,
            exchange=str(payload.get("exchange") or ""),
        )

    @staticmethod
    def _bar_from_json(payload: Any, requested_symbol: str) -> Bar:
        if not isinstance(payload, dict):
            raise MarketDataUnavailableError("Market Data returned an invalid bar")
        event_time = HttpMarketDataClient._timestamp(
            payload.get("event_time", payload.get("timestamp"))
        )
        knowledge_time = HttpMarketDataClient._timestamp(
            payload.get("knowledge_time", payload.get("knowledgeTime", event_time))
        )
        try:
            return Bar(
                symbol=str(payload.get("symbol", requested_symbol)).upper(),
                event_time=event_time,
                knowledge_time=knowledge_time,
                open=Decimal(str(payload["open"])),
                high=Decimal(str(payload["high"])),
                low=Decimal(str(payload["low"])),
                close=Decimal(str(payload["close"])),
                volume=Decimal(str(payload.get("volume", "0"))),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise MarketDataUnavailableError("Market Data returned an invalid bar") from exc

    @staticmethod
    def _corporate_action_from_json(payload: Any, requested_symbol: str) -> CorporateAction:
        if not isinstance(payload, dict):
            raise MarketDataUnavailableError("Market Data returned an invalid corporate action")
        raw_type = str(
            payload.get("action_type", payload.get("actionType", payload.get("type", "")))
        ).lower()
        aliases = {
            "dividend": "cash_dividend",
            "cash-dividend": "cash_dividend",
            "stock-dividend": "stock_dividend",
            "reverse-split": "reverse_split",
            "symbol-change": "symbol_change",
        }
        raw_type = aliases.get(raw_type, raw_type)
        try:
            action_type = CorporateActionType(raw_type)
        except ValueError as exc:
            raise MarketDataUnavailableError(
                f"Unknown corporate-action type: {raw_type!r}"
            ) from exc
        event_time = HttpMarketDataClient._timestamp(
            payload.get("event_time", payload.get("timestamp"))
        )
        knowledge_time = HttpMarketDataClient._timestamp(
            payload.get("knowledge_time", payload.get("knowledgeTime", event_time))
        )
        successor_symbol = payload.get("successor_symbol", payload.get("successorSymbol"))
        return CorporateAction(
            symbol=str(payload.get("symbol", requested_symbol)).upper(),
            action_type=action_type,
            event_time=event_time,
            knowledge_time=knowledge_time,
            ratio=HttpMarketDataClient._decimal_or_none(payload.get("ratio")),
            cash_amount=HttpMarketDataClient._decimal_or_none(
                payload.get("cash_amount", payload.get("cashAmount"))
            ),
            successor_symbol=(
                str(successor_symbol).upper() if successor_symbol is not None else None
            ),
            source="market-data",
        )

    @staticmethod
    def _timestamp(value: Any) -> datetime:
        if isinstance(value, datetime):
            return value
        if not isinstance(value, str):
            raise MarketDataUnavailableError("Market Data returned a missing or invalid timestamp")
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise MarketDataUnavailableError(f"Invalid Market Data timestamp: {value!r}") from exc

    @staticmethod
    def _decimal_or_none(value: Any) -> Decimal | None:
        try:
            return None if value is None else Decimal(str(value))
        except (ValueError, TypeError) as exc:
            raise MarketDataUnavailableError("Market Data returned an invalid decimal") from exc
