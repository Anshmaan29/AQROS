"""FastAPI dependency wiring (dependency injection).

Routes depend on services (which depend on ports), never on concrete
adapters. The concrete choices are made once here, reading from
``app.state`` (set in ``app.py``'s lifespan), so tests can override any
dependency to inject fakes. Mirrors ``aqros_training_pipeline.api.deps``.

The Registry's outbound dependencies that live for the lifetime of the process
— the ``TrainingPipelineClient`` (its single upstream channel), the
``ArtifactStore``, and the ``ArtifactSigner`` — are attached to ``app.state``
at startup and read back here. The per-request ``AsyncSession`` (and therefore
the SQLAlchemy repositories built on it) is created fresh for each request and
owns the transaction: ``get_session`` commits on success and rolls back on any
exception, so the repositories never commit themselves (design.md Section 5.1).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from aqros_model_registry.adapters.clock import SystemClock
from aqros_model_registry.adapters.repository import (
    SqlAlchemyAuditRepository,
    SqlAlchemyModelVersionRepository,
    SqlAlchemyPromotionRepository,
)
from aqros_model_registry.domain.ports import (
    ArtifactSigner,
    ArtifactStore,
    AuditRepository,
    Clock,
    ModelVersionRepository,
    PromotionRepository,
    TrainingPipelineClient,
)
from aqros_model_registry.domain.services import (
    ModelRegistryService,
    RegistryQueryService,
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


def get_training_pipeline_client(request: Request) -> TrainingPipelineClient:
    client: TrainingPipelineClient = request.app.state.training_pipeline_client
    return client


def get_artifact_store(request: Request) -> ArtifactStore:
    store: ArtifactStore = request.app.state.artifact_store
    return store


def get_artifact_signer(request: Request) -> ArtifactSigner:
    signer: ArtifactSigner = request.app.state.artifact_signer
    return signer


def get_clock() -> Clock:
    return SystemClock()


def get_model_version_repository(
    session: AsyncSession = Depends(get_session),
) -> ModelVersionRepository:
    return SqlAlchemyModelVersionRepository(session)


def get_promotion_repository(
    session: AsyncSession = Depends(get_session),
) -> PromotionRepository:
    return SqlAlchemyPromotionRepository(session)


def get_audit_repository(
    session: AsyncSession = Depends(get_session),
) -> AuditRepository:
    return SqlAlchemyAuditRepository(session)


def get_model_registry_service(
    training_pipeline_client: TrainingPipelineClient = Depends(get_training_pipeline_client),
    artifact_store: ArtifactStore = Depends(get_artifact_store),
    model_version_repository: ModelVersionRepository = Depends(get_model_version_repository),
    promotion_repository: PromotionRepository = Depends(get_promotion_repository),
    audit_repository: AuditRepository = Depends(get_audit_repository),
    artifact_signer: ArtifactSigner = Depends(get_artifact_signer),
    clock: Clock = Depends(get_clock),
) -> ModelRegistryService:
    return ModelRegistryService(
        training_pipeline_client=training_pipeline_client,
        artifact_store=artifact_store,
        model_version_repository=model_version_repository,
        promotion_repository=promotion_repository,
        audit_repository=audit_repository,
        artifact_signer=artifact_signer,
        clock=clock,
    )


def get_registry_query_service(
    model_version_repository: ModelVersionRepository = Depends(get_model_version_repository),
    promotion_repository: PromotionRepository = Depends(get_promotion_repository),
    audit_repository: AuditRepository = Depends(get_audit_repository),
    artifact_store: ArtifactStore = Depends(get_artifact_store),
    artifact_signer: ArtifactSigner = Depends(get_artifact_signer),
) -> RegistryQueryService:
    return RegistryQueryService(
        model_version_repository=model_version_repository,
        promotion_repository=promotion_repository,
        audit_repository=audit_repository,
        artifact_store=artifact_store,
        artifact_signer=artifact_signer,
    )
