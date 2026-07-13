"""SQLAlchemy 2.0 ORM models — the persistence schema.

Table/column naming follows CLAUDE.md §10 (snake_case, plural table names,
every time-sensitive table carries ``event_time``/``knowledge_time``). These
models are private to the adapters layer; nothing outside ``adapters/``
should import them directly — the repository maps rows to/from the domain
types in ``domain/models.py``.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, Numeric, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for all market-data ORM models."""


class InstrumentORM(Base):
    """Reference row for a tradable instrument."""

    __tablename__ = "instruments"

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    exchange: Mapped[str | None] = mapped_column(String(64), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OHLCVBarORM(Base):
    """A single OHLCV bar, bitemporal (``event_time`` + ``knowledge_time``)."""

    __tablename__ = "ohlcv_bars"
    __table_args__ = (
        UniqueConstraint(
            "symbol", "event_time", "interval", "source", name="uq_ohlcv_bars_identity"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    event_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, nullable=False
    )
    interval: Mapped[str] = mapped_column(String(8), nullable=False)
    open: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    adjusted_close: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    knowledge_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
