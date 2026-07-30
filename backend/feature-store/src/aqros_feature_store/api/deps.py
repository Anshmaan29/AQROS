"""FastAPI dependency wiring (dependency injection).

Every route depends on abstractions (services, which depend on ports), never
on concrete adapters directly. The concrete choices — which DB session
factory, which HTTP client, which Redis client — are made exactly once here,
reading from ``app.state`` (set up in ``app.py``'s lifespan), so tests can
override any of these dependencies to inject fakes without touching route
code. Mirrors ``aqros_market_data.api.deps``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from aqros_feature_store.adapters.repository import (
    SqlAlchemyFeatureComputationRunRepository,
    SqlAlchemyFeatureDefinitionRepository,
    SqlAlchemyFeatureValueRepository,
)
from aqros_feature_store.domain.online_ports import OnlineFeatureStore
from aqros_feature_store.domain.online_service import OnlineFeatureService
from aqros_feature_store.domain.ports import (
    FeatureComputationRunRepository,
    FeatureDefinitionRepository,
    FeatureValueRepository,
    MarketDataSource,
)
from aqros_feature_store.domain.services import FeatureEngineeringService, FeatureQueryService


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


def get_market_data_source(request: Request) -> MarketDataSource:
    """Return the configured Market Data Service client."""
    market_data_source: MarketDataSource = request.app.state.market_data_source
    return market_data_source


def get_http_client(request: Request) -> httpx.AsyncClient:
    client: httpx.AsyncClient = request.app.state.http_client
    return client


def get_definition_repository(
    session: AsyncSession = Depends(get_session),
) -> FeatureDefinitionRepository:
    return SqlAlchemyFeatureDefinitionRepository(session)


def get_value_repository(session: AsyncSession = Depends(get_session)) -> FeatureValueRepository:
    return SqlAlchemyFeatureValueRepository(session)


def get_run_repository(
    session: AsyncSession = Depends(get_session),
) -> FeatureComputationRunRepository:
    return SqlAlchemyFeatureComputationRunRepository(session)


def get_engineering_service(
    request: Request,
    market_data_source: MarketDataSource = Depends(get_market_data_source),
    value_repository: FeatureValueRepository = Depends(get_value_repository),
    definition_repository: FeatureDefinitionRepository = Depends(get_definition_repository),
    run_repository: FeatureComputationRunRepository = Depends(get_run_repository),
) -> FeatureEngineeringService:
    settings = request.app.state.settings
    return FeatureEngineeringService(
        market_data_source,
        value_repository,
        definition_repository,
        run_repository,
        lookback_buffer_days=settings.feature_lookback_buffer_days,
    )


def get_query_service(
    value_repository: FeatureValueRepository = Depends(get_value_repository),
    definition_repository: FeatureDefinitionRepository = Depends(get_definition_repository),
) -> FeatureQueryService:
    return FeatureQueryService(value_repository, definition_repository)


def get_online_store(request: Request) -> OnlineFeatureStore:
    """Return the configured online feature store adapter (Redis-backed).

    Raises ``503`` if the online store was not initialised (Redis unavailable
    at startup), so callers don't silently serve stale or empty results.
    """
    store: OnlineFeatureStore | None = getattr(request.app.state, "online_feature_store", None)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Online feature store not available (Redis not configured)",
        )
    return store


def get_online_service(
    online_store: OnlineFeatureStore = Depends(get_online_store),
) -> OnlineFeatureService:
    return OnlineFeatureService(online_store)
