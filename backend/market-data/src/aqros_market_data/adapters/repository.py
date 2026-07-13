"""SQLAlchemy implementations of the domain repository ports.

Each repository maps ORM rows to/from the pure domain types in
``domain/models.py``. Business logic (``domain/services.py``) never sees an
ORM row or a SQLAlchemy session — only these ports.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import cast

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.functions import coalesce

from aqros_market_data.adapters.orm import InstrumentORM, OHLCVBarORM
from aqros_market_data.domain.models import BarInterval, Instrument, OHLCVBar
from aqros_market_data.domain.ports import BarRepository, InstrumentRepository


def _to_domain_bar(row: OHLCVBarORM) -> OHLCVBar:
    return OHLCVBar(
        symbol=row.symbol,
        event_time=row.event_time,
        interval=BarInterval(row.interval),
        open=row.open,
        high=row.high,
        low=row.low,
        close=row.close,
        adjusted_close=row.adjusted_close,
        volume=row.volume,
        source=row.source,
        knowledge_time=row.knowledge_time,
    )


def _to_domain_instrument(row: InstrumentORM) -> Instrument:
    return Instrument(
        symbol=row.symbol, name=row.name, exchange=row.exchange, currency=row.currency
    )


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=UTC)
    end = datetime.combine(day, time.max, tzinfo=UTC)
    return start, end


class SqlAlchemyBarRepository(BarRepository):
    """Postgres-backed OHLCV bar repository using an upsert on the identity key."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_bars(self, bars: list[OHLCVBar]) -> int:
        if not bars:
            return 0

        values = [
            {
                "symbol": bar.symbol,
                "event_time": bar.event_time,
                "interval": bar.interval.value,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "adjusted_close": bar.adjusted_close,
                "volume": bar.volume,
                "source": bar.source,
                "knowledge_time": bar.knowledge_time,
            }
            for bar in bars
        ]

        stmt = pg_insert(OHLCVBarORM).values(values)
        update_cols = {
            "open": stmt.excluded.open,
            "high": stmt.excluded.high,
            "low": stmt.excluded.low,
            "close": stmt.excluded.close,
            "adjusted_close": stmt.excluded.adjusted_close,
            "volume": stmt.excluded.volume,
            "knowledge_time": stmt.excluded.knowledge_time,
        }
        stmt = stmt.on_conflict_do_update(
            constraint="uq_ohlcv_bars_identity",
            set_=update_cols,
        )
        result = await self._session.execute(stmt)
        rowcount: int = cast(CursorResult[object], result).rowcount
        return rowcount or 0

    async def get_bars(
        self,
        symbol: str,
        *,
        start: date | None = None,
        end: date | None = None,
        interval: BarInterval = BarInterval.DAILY,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[OHLCVBar]:
        stmt = select(OHLCVBarORM).where(
            OHLCVBarORM.symbol == symbol.upper(), OHLCVBarORM.interval == interval.value
        )
        if start is not None:
            stmt = stmt.where(OHLCVBarORM.event_time >= _day_bounds(start)[0])
        if end is not None:
            stmt = stmt.where(OHLCVBarORM.event_time <= _day_bounds(end)[1])
        stmt = stmt.order_by(OHLCVBarORM.event_time.asc()).limit(limit).offset(offset)

        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_domain_bar(row) for row in rows]

    async def count_bars(
        self,
        symbol: str,
        *,
        start: date | None = None,
        end: date | None = None,
        interval: BarInterval = BarInterval.DAILY,
    ) -> int:
        stmt = (
            select(func.count())
            .select_from(OHLCVBarORM)
            .where(OHLCVBarORM.symbol == symbol.upper(), OHLCVBarORM.interval == interval.value)
        )
        if start is not None:
            stmt = stmt.where(OHLCVBarORM.event_time >= _day_bounds(start)[0])
        if end is not None:
            stmt = stmt.where(OHLCVBarORM.event_time <= _day_bounds(end)[1])

        return (await self._session.execute(stmt)).scalar_one()


class SqlAlchemyInstrumentRepository(InstrumentRepository):
    """Postgres-backed instrument reference-data repository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_instrument(self, instrument: Instrument) -> None:
        now = datetime.now(UTC)
        stmt = pg_insert(InstrumentORM).values(
            symbol=instrument.symbol,
            name=instrument.name,
            exchange=instrument.exchange,
            currency=instrument.currency,
            created_at=now,
            updated_at=now,
        )
        # Coalesce so an ingest that only knows the symbol (yfinance's minimal
        # metadata) never blanks out previously-enriched name/exchange/currency.
        stmt = stmt.on_conflict_do_update(
            index_elements=[InstrumentORM.symbol],
            set_={
                "name": coalesce(stmt.excluded.name, InstrumentORM.name),
                "exchange": coalesce(stmt.excluded.exchange, InstrumentORM.exchange),
                "currency": coalesce(stmt.excluded.currency, InstrumentORM.currency),
                "updated_at": now,
            },
        )
        await self._session.execute(stmt)

    async def get_instrument(self, symbol: str) -> Instrument | None:
        stmt = select(InstrumentORM).where(InstrumentORM.symbol == symbol.upper())
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_domain_instrument(row) if row is not None else None

    async def list_instruments(self, *, limit: int = 1000, offset: int = 0) -> list[Instrument]:
        stmt = (
            select(InstrumentORM).order_by(InstrumentORM.symbol.asc()).limit(limit).offset(offset)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_domain_instrument(row) for row in rows]
