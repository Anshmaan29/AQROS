"""Inference backend implementations.

Provides framework-specific backends (DummyModelBackend for testing, with
stubs ready for MLX and ONNX).
"""

from __future__ import annotations

from typing import Any

from aqros_inference_service.adapters.backends import InferenceBackend


class DummyModelBackend(InferenceBackend):
    """A no-op backend for testing and development.

    Returns a deterministic prediction based on a simple average of feature
    values. Confidence is derived from feature count.
    """

    async def predict(self, model: Any, features: dict[str, Any]) -> float:
        numeric_values = [v for v in features.values() if isinstance(v, (int, float))]
        if not numeric_values:
            return 0.0
        return sum(numeric_values) / len(numeric_values)

    def get_model_type(self, model: Any) -> str:
        return "regression"

    async def estimate_confidence(
        self, model: Any, prediction: float, features: dict[str, Any]
    ) -> float:
        numeric_count = sum(1 for v in features.values() if isinstance(v, (int, float)))
        total = len(features) if features else 1
        ratio = numeric_count / max(total, 1)
        return min(ratio, 1.0)

    async def explain(
        self, model: Any, prediction: float, features: dict[str, Any]
    ) -> dict[str, float]:
        contributions: dict[str, float] = {}
        n = max(len(features), 1)
        for name, value in features.items():
            if isinstance(value, (int, float)):
                contributions[name] = value / n
        return contributions

    @classmethod
    def is_available(cls) -> bool:
        return True
