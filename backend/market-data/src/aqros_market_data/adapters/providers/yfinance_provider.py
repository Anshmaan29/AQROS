"""yfinance-backed historical OHLCV provider.

``yfinance`` is a synchronous, blocking library (it shells out to
``requests``). To keep the service's async architecture honest, every call
into it is pushed to a worker thread via ``asyncio.to_thread`` rather than
blocking the event loop. This is the *only* provider implementation in
Phase 1; adding Alpaca/Polygon/TwelveData later means adding a sibling module
here that implements the same :class:`MarketDataProvider` port — nothing in
``domain/`` or ``api/`` changes.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import cast

import pandas as pd
import structlog
import yfinance as yf

from aqros_market_data.domain.models import BarInterval, OHLCVBar
from aqros_market_data.domain.ports import (
    MarketDataProvider,
    MarketDataProviderError,
    SymbolNotFoundError,
)

logger = structlog.get_logger(__name__)

_YFINANCE_INTERVAL: dict[BarInterval, str] = {
    BarInterval.DAILY: "1d",
    BarInterval.WEEKLY: "1wk",
    BarInterval.MONTHLY: "1mo",
}


class YFinanceProvider(MarketDataProvider):
    """Historical OHLCV provider backed by Yahoo Finance via ``yfinance``.

    No API key is required, which is why this is the Phase 1 default
    (docs/Execution_Blueprint.md §7.3 lists the market-data vendor as
    something to mock/simplify behind a real interface for the MVP).
    """

    def __init__(
        self,
        *,
        request_timeout_seconds: float = 30.0,
        max_retries: int = 3,
        retry_backoff_seconds: float = 1.0,
    ) -> None:
        self._timeout = request_timeout_seconds
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff_seconds

    @property
    def name(self) -> str:
        return "yfinance"

    async def fetch_history(
        self,
        symbol: str,
        *,
        start: date,
        end: date,
        interval: BarInterval = BarInterval.DAILY,
    ) -> list[OHLCVBar]:
        if start > end:
            raise ValueError(f"start ({start}) must not be after end ({end})")

        yf_interval = _YFINANCE_INTERVAL[interval]
        # yfinance's `end` is exclusive; callers of this port expect an
        # inclusive range, so extend by one day.
        exclusive_end = end + timedelta(days=1)

        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                frame = await asyncio.to_thread(
                    self._download_sync, symbol, start, exclusive_end, yf_interval
                )
                return self._to_domain_bars(symbol, frame, interval)
            except SymbolNotFoundError:
                raise
            except (
                Exception
            ) as exc:  # retried below; re-raised as MarketDataProviderError after exhaustion
                last_error = exc
                logger.warning(
                    "yfinance.fetch_failed",
                    symbol=symbol,
                    attempt=attempt,
                    max_retries=self._max_retries,
                    error=str(exc),
                )
                if attempt < self._max_retries:
                    await asyncio.sleep(self._retry_backoff * attempt)

        raise MarketDataProviderError(
            f"Failed to fetch history for {symbol} after {self._max_retries} attempts"
        ) from last_error

    def _download_sync(
        self, symbol: str, start: date, exclusive_end: date, yf_interval: str
    ) -> pd.DataFrame:
        ticker = yf.Ticker(symbol)
        frame: pd.DataFrame = ticker.history(
            start=start.isoformat(),
            end=exclusive_end.isoformat(),
            interval=yf_interval,
            auto_adjust=False,
            actions=False,
            timeout=self._timeout,
        )
        if frame is None or frame.empty:
            raise SymbolNotFoundError(f"No data returned for symbol '{symbol}'")
        return frame

    def _to_domain_bars(
        self, symbol: str, frame: pd.DataFrame, interval: BarInterval
    ) -> list[OHLCVBar]:
        now = datetime.now(UTC)
        bars: list[OHLCVBar] = []
        for index, row in frame.iterrows():
            # yfinance always returns a DatetimeIndex; pandas-stubs types the
            # iterrows() index as the more general `Hashable`.
            event_time = self._to_utc_datetime(cast(pd.Timestamp, index))
            close = self._to_decimal(row["Close"])
            adjusted_close = self._to_decimal(row["Adj Close"]) if "Adj Close" in row else None
            bars.append(
                OHLCVBar(
                    symbol=symbol.upper(),
                    event_time=event_time,
                    interval=interval,
                    open=self._to_decimal(row["Open"]),
                    high=self._to_decimal(row["High"]),
                    low=self._to_decimal(row["Low"]),
                    close=close,
                    adjusted_close=adjusted_close,
                    volume=int(row["Volume"]),
                    source=self.name,
                    knowledge_time=now,
                )
            )
        return bars

    @staticmethod
    def _to_utc_datetime(index: pd.Timestamp) -> datetime:
        ts = pd.Timestamp(index)
        ts = ts.tz_localize(UTC) if ts.tzinfo is None else ts.tz_convert(UTC)
        result: datetime = ts.to_pydatetime()
        return result

    @staticmethod
    def _to_decimal(value: float) -> Decimal:
        return Decimal(str(round(float(value), 6)))
