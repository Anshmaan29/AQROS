"""Ports (interfaces) for the Inference Service.

Every external dependency behind an ABC: model loading, feature fetching,
prediction publishing, metrics, clock, and audit.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Protocol

from aqros_inference_service.domain.model_manager import LoadedModel
from aqros_inference_service.domain.models import PredictionResult


class Clock(ABC):
    """Abstract clock — injectable for deterministic testing."""

    @abstractmethod
    def now(self) -> datetime:
        """Return the current UTC datetime."""


class ModelLoader(ABC):
    """Port for loading model binaries from an artifact store."""

    @abstractmethod
    async def load(self, name: str, version: int) -> LoadedModel:
        """Fetch and deserialise a model binary, returning a ``LoadedModel``.

        Raises ``ModelNotFoundError`` if the version doesn't exist.
        Raises ``ModelCorruptError`` if checksum verification fails.
        """

    @abstractmethod
    async def get_latest_version(self, name: str) -> int | None:
        """Return the latest available version number for a model family."""


class ModelRepository(ABC):
    """Port for querying model metadata from the Model Registry."""

    @abstractmethod
    async def get_model_metadata(self, name: str, version: int) -> dict[str, Any]:
        """Return metadata for a specific model version."""

    @abstractmethod
    async def get_current_production(self, name: str) -> dict[str, Any] | None:
        """Return metadata for the current production model version."""


class FeatureProvider(ABC):
    """Port for fetching online feature values."""

    @abstractmethod
    async def get_features(self, symbol: str) -> dict[str, Any]:
        """Return online feature values for a symbol.

        Returns an empty dict if no features are available.
        """

    @abstractmethod
    async def health_check(self) -> bool:
        """Verify the feature store is reachable."""


class PredictionPublisher(ABC):
    """Port for publishing prediction events on the event bus."""

    @abstractmethod
    async def publish(self, topic: str, payload: bytes) -> None:
        """Publish a serialised event payload to the given topic."""


class MetricPublisher(ABC):
    """Port for publishing inference metrics."""

    @abstractmethod
    async def record_prediction(self, result: PredictionResult) -> None: ...

    @abstractmethod
    async def record_cache_hit(self) -> None: ...

    @abstractmethod
    async def record_cache_miss(self) -> None: ...

    @abstractmethod
    async def record_reload(self) -> None: ...

    @abstractmethod
    async def record_validation_failure(self) -> None: ...


class AuditRepository(ABC):
    """Port for persisting audit records of every prediction."""

    @abstractmethod
    async def record_prediction(self, result: PredictionResult, metadata: object) -> None: ...

    @abstractmethod
    async def record_model_event(
        self, event_type: str, model_name: str, model_version: int, detail: str | None = None
    ) -> None: ...


# --- Runtime Protocol for the actual aqros-events EventBus ---------------


class EventBusProtocol(Protocol):
    """Protocol matching ``aqros_events.EventBus``."""

    async def publish(self, envelope: object) -> None: ...


class ModelNotFoundError(RuntimeError):
    """Raised when a requested model version does not exist."""

    def __init__(self, name: str, version: int) -> None:
        self.name = name
        self.version = version
        super().__init__(f"Model '{name}' version {version} not found")


class ModelCorruptError(RuntimeError):
    """Raised when a model binary fails checksum verification."""

    def __init__(self, name: str, version: int, detail: str = "") -> None:
        self.name = name
        self.version = version
        super().__init__(f"Model '{name}' version {version} is corrupt: {detail}")
