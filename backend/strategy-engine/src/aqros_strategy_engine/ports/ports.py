"""Ports (interfaces) for the Strategy Engine."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Protocol

from aqros_strategy_engine.domain.models import StrategyDecision


class Clock(ABC):
    @abstractmethod
    def now(self) -> datetime: ...


class FeatureProvider(ABC):
    @abstractmethod
    async def get_features(self, symbol: str) -> dict[str, Any]: ...

    @abstractmethod
    async def health_check(self) -> bool: ...


class InferenceClient(ABC):
    @abstractmethod
    async def predict(
        self,
        symbol: str,
        model_name: str,
        model_version: int | None = None,
        features: dict[str, Any] | None = None,
    ) -> InferenceResult: ...


class InferenceResult:
    def __init__(self, prediction: float, confidence: float) -> None:
        self.prediction = prediction
        self.confidence = confidence


class ModelRegistryClient(ABC):
    @abstractmethod
    async def get_champion_version(self, model_name: str) -> int | None: ...


class SignalPublisher(ABC):
    @abstractmethod
    async def publish(self, topic: str, payload: bytes) -> None: ...


class MetricPublisher(ABC):
    @abstractmethod
    async def record_signal(self, decision: StrategyDecision) -> None: ...

    @abstractmethod
    async def record_reload(self) -> None: ...

    @abstractmethod
    async def record_error(self) -> None: ...


class EventBusProtocol(Protocol):
    async def publish(self, envelope: object) -> None: ...
