"""Unit tests for DummyModelBackend (inference backend)."""

from __future__ import annotations

import pytest
from aqros_inference_service.adapters.dummy_backend import DummyModelBackend


@pytest.fixture
def backend() -> DummyModelBackend:
    return DummyModelBackend()


class TestPredict:
    async def test_average_of_features(self, backend: DummyModelBackend) -> None:
        result = await backend.predict(None, {"a": 10.0, "b": 20.0, "c": 30.0})
        assert result == 20.0

    async def test_single_feature(self, backend: DummyModelBackend) -> None:
        result = await backend.predict(None, {"a": 42.5})
        assert result == 42.5

    async def test_no_numeric_features(self, backend: DummyModelBackend) -> None:
        result = await backend.predict(None, {"sector": "tech"})
        assert result == 0.0

    async def test_empty_features(self, backend: DummyModelBackend) -> None:
        result = await backend.predict(None, {})
        assert result == 0.0

    async def test_mixed_features(self, backend: DummyModelBackend) -> None:
        result = await backend.predict(None, {"a": 10.0, "sector": "tech", "b": 30.0})
        assert result == 20.0

    async def test_integer_features(self, backend: DummyModelBackend) -> None:
        result = await backend.predict(None, {"a": 1, "b": 2, "c": 3})
        assert result == 2.0


class TestGetModelType:
    def test_returns_regression(self, backend: DummyModelBackend) -> None:
        assert backend.get_model_type(None) == "regression"


class TestEstimateConfidence:
    async def test_all_numeric(self, backend: DummyModelBackend) -> None:
        conf = await backend.estimate_confidence(None, 42.0, {"a": 1.0, "b": 2.0})
        assert conf == 1.0

    async def test_half_numeric(self, backend: DummyModelBackend) -> None:
        conf = await backend.estimate_confidence(None, 42.0, {"a": 1.0, "sector": "tech"})
        assert conf == 0.5

    async def test_no_numeric(self, backend: DummyModelBackend) -> None:
        conf = await backend.estimate_confidence(None, 42.0, {"sector": "tech", "name": "test"})
        assert conf == 0.0

    async def test_empty_features(self, backend: DummyModelBackend) -> None:
        conf = await backend.estimate_confidence(None, 42.0, {})
        assert conf == 0.0


class TestExplain:
    async def test_all_features_contribute(self, backend: DummyModelBackend) -> None:
        expl = await backend.explain(None, 30.0, {"a": 10.0, "b": 20.0, "c": 30.0})
        assert expl == {"a": 10.0 / 3, "b": 20.0 / 3, "c": 30.0 / 3}

    async def test_mixed_features(self, backend: DummyModelBackend) -> None:
        expl = await backend.explain(None, 10.0, {"a": 10.0, "sector": "tech"})
        assert "a" in expl
        assert "sector" not in expl

    async def test_empty_features(self, backend: DummyModelBackend) -> None:
        expl = await backend.explain(None, 0.0, {})
        assert expl == {}


class TestIsAvailable:
    def test_always_available(self, backend: DummyModelBackend) -> None:
        assert DummyModelBackend.is_available()
