"""Abstract Strategy interface — each strategy converts predictions into signals."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from aqros_strategy_engine.domain.models import (
    StrategyDecision,
    StrategyMetadata,
)


class Strategy(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def version(self) -> str: ...

    @abstractmethod
    async def evaluate(
        self,
        symbol: str,
        prediction: float | None,
        prediction_confidence: float | None,
        features: dict[str, Any],
        model_version: int | None = None,
    ) -> StrategyDecision: ...

    @abstractmethod
    def metadata(self) -> StrategyMetadata: ...
