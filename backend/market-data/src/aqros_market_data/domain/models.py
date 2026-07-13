"""Core domain types for the market-data service.

Pure data structures — no I/O, no framework imports. These are the types
``domain/`` logic and repository/provider ports speak in; the API layer maps
its own Pydantic schemas to/from these, and the adapters layer maps ORM rows
to/from these. Keeping them separate from both is what lets the persistence
technology and the transport technology change independently of the business
rules (ports-and-adapters, per CLAUDE.md §5).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class BarInterval(StrEnum):
    """Supported OHLCV bar resolutions (kept intentionally small for Phase 1)."""

    DAILY = "1d"
    WEEKLY = "1wk"
    MONTHLY = "1mo"


@dataclass(frozen=True, slots=True)
class Instrument:
    """A tradable instrument, identified by its ticker symbol.

    Phase 1 keeps this minimal (no full security-master/crosswalk — that is
    explicitly out of scope; see docs/claude_ROI.md §2.1 for the eventual
    design). ``symbol`` is the natural key for the MVP's single equities use
    case.
    """

    symbol: str
    name: str | None = None
    exchange: str | None = None
    currency: str | None = None


@dataclass(frozen=True, slots=True)
class OHLCVBar:
    """A single OHLCV bar for an instrument, bitemporal per project convention.

    ``event_time`` is when the bar occurred in the market; ``knowledge_time``
    is when this system ingested it. Storing both (docs/claude_ROI.md §17)
    means a later point-in-time query can never accidentally see a bar before
    it was actually knowable, even though Phase 1 has no PIT query surface yet.
    """

    symbol: str
    event_time: datetime
    interval: BarInterval
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    source: str
    knowledge_time: datetime
    adjusted_close: Decimal | None = None
