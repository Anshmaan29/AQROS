"""FastAPI dependency wiring (dependency injection).

Every route depends on abstractions (services, which depend on ports), never
on concrete adapters directly. The concrete choices — which DB session
factory, which HTTP clients, which storage backend — are made exactly once
here, reading from ``app.state`` (set up in ``app.py``'s lifespan), so tests
can override any of these dependencies to inject fakes without touching
route code. Mirrors ``aqros_feature_store.api.deps``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from aqros_dataset_builder.adapters.repository import (
    SqlAlchemyDatasetBuildRunRepository,
    SqlAlchemyDatasetDefinitionRepository,
)
from aqros_dataset_builder.domain.ports import (
    DatasetBuildRunRepository,
    DatasetDefinitionRepository,
    DatasetStorage,
    FeatureSource,
    GitInfoProvider,
    MarketDataSource,
)
from aqros_dataset_builder.domain.services import DatasetBuilderService, DatasetQueryService


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
    market_data_source: MarketDataSource = request.app.state.market_data_source
    return market_data_source


def get_feature_source(request: Request) -> FeatureSource:
    feature_source: FeatureSource = request.app.state.feature_source
    return feature_source


def get_dataset_storage(request: Request) -> DatasetStorage:
    storage: DatasetStorage = request.app.state.dataset_storage
    return storage


def get_git_info_provider(request: Request) -> GitInfoProvider:
    provider: GitInfoProvider = request.app.state.git_info_provider
    return provider


def get_definition_repository(
    session: AsyncSession = Depends(get_session),
) -> DatasetDefinitionRepository:
    return SqlAlchemyDatasetDefinitionRepository(session)


def get_run_repository(
    session: AsyncSession = Depends(get_session),
) -> DatasetBuildRunRepository:
    return SqlAlchemyDatasetBuildRunRepository(session)


def get_builder_service(
    request: Request,
    market_data_source: MarketDataSource = Depends(get_market_data_source),
    feature_source: FeatureSource = Depends(get_feature_source),
    definition_repository: DatasetDefinitionRepository = Depends(get_definition_repository),
    run_repository: DatasetBuildRunRepository = Depends(get_run_repository),
    storage: DatasetStorage = Depends(get_dataset_storage),
    git_info_provider: GitInfoProvider = Depends(get_git_info_provider),
) -> DatasetBuilderService:
    settings = request.app.state.settings
    return DatasetBuilderService(
        market_data_source,
        feature_source,
        definition_repository,
        run_repository,
        storage,
        git_info_provider,
        market_data_source_url=str(settings.market_data_base_url),
        feature_store_source_url=str(settings.feature_store_base_url),
    )


def get_query_service(
    definition_repository: DatasetDefinitionRepository = Depends(get_definition_repository),
    run_repository: DatasetBuildRunRepository = Depends(get_run_repository),
    storage: DatasetStorage = Depends(get_dataset_storage),
) -> DatasetQueryService:
    return DatasetQueryService(definition_repository, run_repository, storage)
