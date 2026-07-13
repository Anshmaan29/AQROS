"""FastAPI dependency wiring (dependency injection).

Routes depend on services (which depend on ports), never on concrete
adapters. The concrete choices are made once here, reading from
``app.state`` (set in ``app.py``'s lifespan), so tests can override any
dependency to inject fakes. Mirrors ``aqros_dataset_builder.api.deps``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from aqros_training_pipeline.adapters.repository import (
    SqlAlchemyTrainedModelRepository,
    SqlAlchemyTrainingRunRepository,
)
from aqros_training_pipeline.domain.ports import (
    ArtifactStore,
    DatasetBuilderClient,
    GitInfoProvider,
    TrainedModelRepository,
    TrainingRunRepository,
)
from aqros_training_pipeline.domain.services import (
    TrainingPipelineService,
    TrainingQueryService,
)


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


def get_dataset_builder_client(request: Request) -> DatasetBuilderClient:
    client: DatasetBuilderClient = request.app.state.dataset_builder_client
    return client


def get_artifact_store(request: Request) -> ArtifactStore:
    store: ArtifactStore = request.app.state.artifact_store
    return store


def get_git_info_provider(request: Request) -> GitInfoProvider:
    provider: GitInfoProvider = request.app.state.git_info_provider
    return provider


def get_run_repository(
    session: AsyncSession = Depends(get_session),
) -> TrainingRunRepository:
    return SqlAlchemyTrainingRunRepository(session)


def get_trained_model_repository(
    session: AsyncSession = Depends(get_session),
) -> TrainedModelRepository:
    return SqlAlchemyTrainedModelRepository(session)


def get_training_pipeline_service(
    dataset_builder_client: DatasetBuilderClient = Depends(get_dataset_builder_client),
    artifact_store: ArtifactStore = Depends(get_artifact_store),
    run_repository: TrainingRunRepository = Depends(get_run_repository),
    trained_model_repository: TrainedModelRepository = Depends(get_trained_model_repository),
    git_info_provider: GitInfoProvider = Depends(get_git_info_provider),
) -> TrainingPipelineService:
    return TrainingPipelineService(
        dataset_builder_client=dataset_builder_client,
        artifact_store=artifact_store,
        training_run_repository=run_repository,
        trained_model_repository=trained_model_repository,
        git_info_provider=git_info_provider,
    )


def get_training_query_service(
    run_repository: TrainingRunRepository = Depends(get_run_repository),
    trained_model_repository: TrainedModelRepository = Depends(get_trained_model_repository),
) -> TrainingQueryService:
    return TrainingQueryService(
        training_run_repository=run_repository,
        trained_model_repository=trained_model_repository,
    )
