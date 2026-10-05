"""InferenceBackend — abstract interface for model inference.

Each backend (DummyModelBackend, MLXBackend, ONNXBackend) implements this
interface. The backend is configuration-driven.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class InferenceBackend(ABC):
    """Port for running a model on a feature vector.

    Implementations wrap framework-specific inference (PyTorch, MLX, ONNX,
    sklearn, etc.) behind this single interface.
    """

    @abstractmethod
    async def predict(self, model: Any, features: dict[str, Any]) -> float:
        """Run inference and return a scalar prediction.

        Args:
            model: The loaded model object (framework-specific).
            features: The validated feature vector.

        Returns:
            The scalar prediction value.
        """

    @abstractmethod
    def get_model_type(self, model: Any) -> str:
        """Return the prediction type this model produces.

        One of: "regression", "binary_classification", "multi_class", "score".
        """

    @abstractmethod
    async def estimate_confidence(
        self, model: Any, prediction: float, features: dict[str, Any]
    ) -> float:
        """Return a confidence score in [0, 1] for the prediction.

        The default implementation returns 1.0. Backends may override this
        with model-specific calibration logic (e.g. Platt scaling, isotonic
        regression, ensemble variance).
        """

    async def load(self, model: Any, metadata: dict[str, Any]) -> Any:
        """Load a model binary into the backend's format.

        The default implementation returns the binary as-is. Framework-specific
        backends override this to deserialise into their native format.
        """
        return model

    @classmethod
    @abstractmethod
    def is_available(cls) -> bool:
        """Return True if this backend can run (dependencies met)."""

    async def explain(
        self, model: Any, prediction: float, features: dict[str, Any]
    ) -> dict[str, float]:
        """Return feature importance for this prediction.

        The default implementation returns an empty dict.
        """
        return {}
