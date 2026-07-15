"""Ports (abstract interfaces) the Model Registry domain depends on.

These are the seams between pure business logic and I/O. ``domain/`` and
``api/`` depend only on these abstractions; concrete implementations live in
``adapters/`` and are wired in via dependency injection (``api/deps.py``) —
the same pattern ``aqros_training_pipeline.domain.ports``,
``aqros_dataset_builder.domain.ports``, ``aqros_market_data.domain.ports``,
and ``aqros_feature_store.domain.ports`` establish.

``TrainingPipelineClient`` is the sole channel to the Training Pipeline's
published REST API (CLAUDE.md §7.9, Requirements 1.2, 1.3, 2.1) — the Model
Registry never opens a database connection to any other service and never
issues any other outbound call. Downstream consumers read models only from
the Registry, never from the Training Pipeline (Requirements 1.4, 28.7).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from aqros_model_registry.domain.models import (
    Approval,
    ApprovalState,
    AuditEvent,
    LifecycleState,
    ModelVersion,
    PromotionHistoryEntry,
    PromotionRequest,
    TrainedModelRecord,
    ValidationEvidence,
)


class TrainedModelNotFoundError(RuntimeError):
    """Raised when the Training Pipeline returns 404 for a requested trained model.

    Returned by either ``get_trained_model_record`` or ``download_artifact``
    when the Training Pipeline has no record of the given
    ``(model_name, version)`` — the caller halts registration immediately on
    this error and never persists a partial ``ModelVersion``
    (Requirements 1.6, 2.3, 20.3).
    """


class UpstreamSourceError(RuntimeError):
    """Raised when the Training Pipeline is unreachable or returns a non-404 error.

    Covers connection failures, timeouts, and any HTTP error status other
    than 404. Registration fails immediately with no fallback to any other
    data source and no partial ``ModelVersion`` persisted (Requirements 1.6,
    2.3, 20.3).
    """


class TrainingPipelineClient(ABC):
    """Port for the Registry's sole upstream dependency: the Training Pipeline.

    Only the Training Pipeline's published read endpoints are ever called
    through this port — ``GET /v1/trained-models/{model_name}/versions/{version}/metadata``,
    ``/metrics``, and ``/artifact`` — never a direct database connection to
    the Training Pipeline's own store (Requirements 1.2, 1.3).
    """

    @abstractmethod
    async def get_trained_model_record(self, model_name: str, version: int) -> TrainedModelRecord:
        """Fetch the ``Trained_Model_Record`` for a ``(model_name, version)`` pair.

        Composes the Training Pipeline's metadata and metrics endpoints into a
        local, decoupled ``TrainedModelRecord``. Raises
        ``TrainedModelNotFoundError`` on a 404 response and
        ``UpstreamSourceError`` on any other error or connection failure.
        """

    @abstractmethod
    async def download_artifact(self, model_name: str, version: int) -> bytes:
        """Download the raw ``Model_Artifact`` bytes for a ``(model_name, version)`` pair.

        Raises ``TrainedModelNotFoundError`` on a 404 response and
        ``UpstreamSourceError`` on any other error or connection failure.
        """


class ArtifactAlreadyExistsError(RuntimeError):
    """Raised by ``ArtifactStore.write_artifact`` when the ``(model_name, model_version)``
    pair has already been persisted (Requirement 7.6) — artifacts are
    versioned and immutable, never overwritten.
    """


class ArtifactStore(ABC):
    """Port for versioned, immutable, swappable persistence of ``Model_Artifact`` bytes.

    Takes/returns plain ``bytes`` only, so a filesystem-backed implementation
    is a drop-in swap for an object-store-backed one (S3/MinIO/R2) with no
    change to any caller (Requirements 8.4, 25.4). Mirrors the Training
    Pipeline's ``ArtifactStore`` port verbatim.
    """

    @abstractmethod
    async def write_artifact(self, model_name: str, model_version: int, data: bytes) -> str:
        """Persist ``data`` under ``(model_name, model_version)`` and return its storage path.

        Raises ``ArtifactAlreadyExistsError`` if that pair has already been
        written, rather than overwriting it (Requirement 7.6).
        """

    @abstractmethod
    async def read_artifact(self, model_name: str, model_version: int) -> bytes:
        """Return the exact bytes previously written for ``(model_name, model_version)``."""


class ModelVersionRepository(ABC):
    """Port for persisting and retrieving ``ModelVersion`` metadata.

    ``ModelVersion`` identity fields are write-once: only ``set_lifecycle_state``
    and ``attach_validation_evidence`` ever mutate a persisted row, and each
    touches only its mutable column(s) (Requirements 3.4, 3.5, 22.2, 22.3).
    """

    @abstractmethod
    async def create_model_version(self, model_version: ModelVersion) -> ModelVersion:
        """Persist a new, immutable ``ModelVersion`` row and return it with its id."""

    @abstractmethod
    async def get(self, model_name: str, version: int) -> ModelVersion | None:
        """Fetch one ``ModelVersion`` by its composite ``(model_name, version)`` key."""

    @abstractmethod
    async def list(
        self,
        *,
        model_name: str | None = None,
        lifecycle_state: LifecycleState | None = None,
    ) -> list[ModelVersion]:
        """List ``ModelVersion`` rows, optionally filtered by name and/or lifecycle state."""

    @abstractmethod
    async def set_lifecycle_state(
        self, model_name: str, version: int, state: LifecycleState
    ) -> ModelVersion:
        """Update only the ``lifecycle_state`` column of one ``ModelVersion`` and return it."""

    @abstractmethod
    async def get_latest_version(self, model_name: str) -> int | None:
        """Return the highest ``version`` recorded for ``model_name``, or ``None`` if none."""

    @abstractmethod
    async def resolve_production(self, model_name: str) -> ModelVersion | None:
        """Return the unique ``PRODUCTION`` ``ModelVersion`` for ``model_name``, or ``None``."""

    @abstractmethod
    async def attach_validation_evidence(
        self, model_name: str, version: int, evidence: ValidationEvidence
    ) -> ModelVersion:
        """Immutably attach ``Validation_Evidence`` to one ``ModelVersion`` and return it."""


class PromotionRepository(ABC):
    """Port for persisting ``PromotionRequest`` records, approvals, and history.

    ``PromotionHistoryEntry`` rows are append-only — there is no update or
    delete code path (Requirements 17.1, 17.2, 22.4).
    """

    @abstractmethod
    async def create_request(self, request: PromotionRequest) -> PromotionRequest:
        """Persist a new ``PromotionRequest`` and return it with its id."""

    @abstractmethod
    async def add_approval(self, request_id: int, approval: Approval) -> PromotionRequest:
        """Append one approver's decision to a ``PromotionRequest`` and return the updated request."""

    @abstractmethod
    async def set_request_state(self, request_id: int, state: ApprovalState) -> PromotionRequest:
        """Update the ``approval_state`` of a ``PromotionRequest`` and return it."""

    @abstractmethod
    async def get_request(self, request_id: int) -> PromotionRequest | None:
        """Fetch one ``PromotionRequest`` by id."""

    @abstractmethod
    async def append_history(self, entry: PromotionHistoryEntry) -> PromotionHistoryEntry:
        """Append one ordered ``PromotionHistoryEntry`` (append-only) and return it."""

    @abstractmethod
    async def list_history(self, model_name: str, version: int) -> list[PromotionHistoryEntry]:
        """Return the ordered ``Promotion_History`` for a ``(model_name, version)`` pair."""


class AuditRepository(ABC):
    """Port for the append-only audit trail of privileged actions.

    ``append`` is the only write operation — ``Audit_Event`` rows are never
    updated or deleted (Requirements 18.1, 18.2, 22.4).
    """

    @abstractmethod
    async def append(self, event: AuditEvent) -> AuditEvent:
        """Append one ``Audit_Event`` (append-only) and return it with its id."""

    @abstractmethod
    async def list(
        self,
        *,
        model_name: str | None = None,
        correlation_id: str | None = None,
    ) -> list[AuditEvent]:
        """List ``Audit_Event`` rows, optionally filtered by model name and/or correlation id."""


class SignatureVerificationError(RuntimeError):
    """Raised by ``ArtifactSigner.verify_artifact`` when an artifact is unsigned or
    its signature is invalid, so it is refused before serving (Requirement 21.3).
    """


class ArtifactSigner(ABC):
    """Port for verifying an artifact's signature before it is served (Requirement 21.3).

    Where signing is configured, ``verify_artifact`` refuses unsigned or
    invalid artifacts by raising ``SignatureVerificationError``; where signing
    is not configured, the implementation is a tolerant no-op.
    """

    @abstractmethod
    async def verify_artifact(self, model_name: str, model_version: int, data: bytes) -> None:
        """Verify the signature of ``data`` for ``(model_name, model_version)``.

        Raises ``SignatureVerificationError`` when signing is configured and
        the artifact is unsigned or its signature is invalid; returns ``None``
        (a no-op) when signing is not configured.
        """


class Clock(ABC):
    """Port for the current time, injected so domain logic and tests are deterministic."""

    @abstractmethod
    def now(self) -> datetime:
        """Return the current timezone-aware UTC timestamp."""
