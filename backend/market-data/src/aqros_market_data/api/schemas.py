"""Pydantic request/response models for the market-data HTTP API.

Kept separate from ``domain/models.py`` so the wire format (camelCase-free
JSON, string enums, ISO dates) can evolve independently of the internal
domain representation.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator, model_validator

from aqros_market_data.domain.models import BarInterval, Instrument, OHLCVBar


class IngestionRequest(BaseModel):
    """Request body to trigger a historical ingestion run for one symbol."""

    symbol: str = Field(..., min_length=1, max_length=32, examples=["AAPL"])
    start: date = Field(..., examples=["2023-01-01"])
    end: date = Field(..., examples=["2023-12-31"])
    interval: BarInterval = BarInterval.DAILY

    @field_validator("symbol")
    @classmethod
    def _normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()

    @model_validator(mode="after")
    def _end_not_before_start(self) -> IngestionRequest:
        if self.end < self.start:
            raise ValueError(f"end ({self.end}) must not be before start ({self.start})")
        return self


class IngestionResponse(BaseModel):
    """Result of an ingestion run."""

    symbol: str
    fetched: int
    persisted: int
    rejected: int
    rejection_reasons: list[str] = Field(default_factory=list)


class OHLCVBarResponse(BaseModel):
    """A single OHLCV bar in API responses."""

    symbol: str
    event_time: datetime
    interval: BarInterval
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    adjusted_close: Decimal | None
    volume: int
    source: str
    knowledge_time: datetime

    @classmethod
    def from_domain(cls, bar: OHLCVBar) -> OHLCVBarResponse:
        return cls(
            symbol=bar.symbol,
            event_time=bar.event_time,
            interval=bar.interval,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            adjusted_close=bar.adjusted_close,
            volume=bar.volume,
            source=bar.source,
            knowledge_time=bar.knowledge_time,
        )


class PaginatedBarsResponse(BaseModel):
    """Paginated OHLCV bar listing."""

    symbol: str
    interval: BarInterval
    total: int
    limit: int
    offset: int
    bars: list[OHLCVBarResponse]


class InstrumentResponse(BaseModel):
    """An instrument in API responses."""

    symbol: str
    name: str | None
    exchange: str | None
    currency: str | None

    @classmethod
    def from_domain(cls, instrument: Instrument) -> InstrumentResponse:
        return cls(
            symbol=instrument.symbol,
            name=instrument.name,
            exchange=instrument.exchange,
            currency=instrument.currency,
        )


class ErrorResponse(BaseModel):
    """Typed error envelope (CLAUDE.md §5: typed, coded error responses)."""

    error: str
    detail: str
