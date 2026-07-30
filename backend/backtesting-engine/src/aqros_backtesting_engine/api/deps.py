from __future__ import annotations

from collections.abc import AsyncIterator, Callable

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from aqros_backtesting_engine.adapters.repository import SqlAlchemyBacktestRunRepository
from aqros_backtesting_engine.adapters.risk_check_factory import default_risk_check_factory
from aqros_backtesting_engine.adapters.strategy_factory import default_strategy_factory
from aqros_backtesting_engine.domain.models import BacktestConfiguration, ResolvedModel
from aqros_backtesting_engine.domain.ports import (
    CalendarProvider,
    FeatureStoreClient,
    MarketDataClient,
    ModelRegistryClient,
)
from aqros_backtesting_engine.domain.services import (
    BacktestQueryService,
    BacktestService,
)
from aqros_strategy_core import RiskCheck, Strategy


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_market_data_client(request: Request) -> MarketDataClient:
    client: MarketDataClient = request.app.state.market_data_client
    return client


def get_model_registry_client(request: Request) -> ModelRegistryClient:
    client: ModelRegistryClient = request.app.state.model_registry_client
    return client


def get_feature_store_client(request: Request) -> FeatureStoreClient:
    client: FeatureStoreClient = request.app.state.feature_store_client
    return client


def get_backtest_repository(
    session: AsyncSession = Depends(get_session),
) -> SqlAlchemyBacktestRunRepository:
    return SqlAlchemyBacktestRunRepository(session)


def get_calendar_provider(request: Request) -> CalendarProvider:
    provider: CalendarProvider = request.app.state.calendar_provider
    return provider


def get_strategy_factory() -> Callable[[BacktestConfiguration, ResolvedModel, bytes], Strategy]:
    return default_strategy_factory


def get_risk_check_factory() -> Callable[[BacktestConfiguration], RiskCheck]:
    return default_risk_check_factory


def get_backtest_service(
    repository: SqlAlchemyBacktestRunRepository = Depends(get_backtest_repository),
    market_data: MarketDataClient = Depends(get_market_data_client),
    model_registry: ModelRegistryClient = Depends(get_model_registry_client),
    feature_store: FeatureStoreClient = Depends(get_feature_store_client),
    calendar_provider: CalendarProvider = Depends(get_calendar_provider),
    strategy_factory: Callable[[BacktestConfiguration, ResolvedModel, bytes], Strategy] = Depends(
        get_strategy_factory
    ),
    risk_check_factory: Callable[[BacktestConfiguration], RiskCheck] = Depends(
        get_risk_check_factory
    ),
) -> BacktestService:
    return BacktestService(
        repository=repository,
        market_data=market_data,
        model_registry=model_registry,
        feature_store=feature_store,
        calendar_provider=calendar_provider,
        strategy_factory=strategy_factory,
        risk_check_factory=risk_check_factory,
    )


def get_backtest_query_service(
    repository: SqlAlchemyBacktestRunRepository = Depends(get_backtest_repository),
) -> BacktestQueryService:
    return BacktestQueryService(repository=repository)
