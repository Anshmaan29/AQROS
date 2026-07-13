"""FastAPI dependency wiring (dependency injection).

Every route depends on abstractions (services, which depend on ports), never
on concrete adapters directly. The concrete choices — which DB session
factory, which provider — are made exactly once here, reading from
``app.state`` (set up in ``app.py``'s lifespan), so tests can override any of
these dependencies to inject fakes without touching route code.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from aqros_market_data.adapters.repository import (
    SqlAlchemyBarRepository,
    SqlAlchemyInstrumentRepository,
)
from aqros_market_data.domain.ports import BarRepository, InstrumentRepository, MarketDataProvider
from aqros_market_data.domain.services import IngestionService, MarketDataQueryService


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped DB session, committing on success."""
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_provider(request: Request) -> MarketDataProvider:
    """Return the configured market-data provider singleton."""
    provider: MarketDataProvider = request.app.state.provider
    return provider


def get_bar_repository(session: AsyncSession = Depends(get_session)) -> BarRepository:
    return SqlAlchemyBarRepository(session)


def get_instrument_repository(
    session: AsyncSession = Depends(get_session),
) -> InstrumentRepository:
    return SqlAlchemyInstrumentRepository(session)


def get_ingestion_service(
    provider: MarketDataProvider = Depends(get_provider),
    bar_repository: BarRepository = Depends(get_bar_repository),
    instrument_repository: InstrumentRepository = Depends(get_instrument_repository),
) -> IngestionService:
    return IngestionService(provider, bar_repository, instrument_repository)


def get_query_service(
    bar_repository: BarRepository = Depends(get_bar_repository),
    instrument_repository: InstrumentRepository = Depends(get_instrument_repository),
) -> MarketDataQueryService:
    return MarketDataQueryService(bar_repository, instrument_repository)
