"""Unit tests for StrategyPipeline (orchestration flow)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from aqros_strategy_engine.application.pipeline import StrategyPipeline
from aqros_strategy_engine.domain.models import (
    StrategyDecision,
    StrategyEvaluationRequest,
)
from aqros_strategy_engine.domain.strategies.implementations import DummyStrategy
from aqros_strategy_engine.domain.strategy_manager import StrategyManager
from aqros_strategy_engine.ports.ports import (
    Clock,
    FeatureProvider,
    InferenceClient,
    InferenceResult,
    MetricPublisher,
    SignalPublisher,
)


class FakeClock(Clock):
    def __init__(self) -> None:
        self._dt = datetime(2024, 6, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return datetime(2024, 6, 1, tzinfo=UTC)


class FakeFeatureProvider(FeatureProvider):
    def __init__(self) -> None:
        self.data: dict[str, dict[str, Any]] = {}
        self._healthy = True

    async def get_features(self, symbol: str) -> dict[str, Any]:
        return self.data.get(symbol.upper(), {})

    async def health_check(self) -> bool:
        return self._healthy


class FakeInferenceClient(InferenceClient):
    def __init__(self) -> None:
        self.predictions: dict[str, tuple[float, float]] = {}

    async def predict(
        self,
        symbol: str,
        model_name: str,
        model_version: int | None = None,
        features: dict[str, Any] | None = None,
    ) -> InferenceResult:
        key = symbol.upper()
        if key in self.predictions:
            pred, conf = self.predictions[key]
            return InferenceResult(prediction=pred, confidence=conf)
        raise ValueError(f"No prediction for {key}")

    async def health_check(self) -> bool:
        return True


class FakeSignalPublisher(SignalPublisher):
    def __init__(self) -> None:
        self.events: list[tuple[str, bytes]] = []

    async def publish(self, topic: str, payload: bytes) -> None:
        self.events.append((topic, payload))


class FakeMetricPublisher(MetricPublisher):
    def __init__(self) -> None:
        self.signals: list[StrategyDecision] = []
        self.reloads = 0
        self.errors = 0

    async def record_signal(self, decision: StrategyDecision) -> None:
        self.signals.append(decision)

    async def record_reload(self) -> None:
        self.reloads += 1

    async def record_error(self) -> None:
        self.errors += 1


@pytest.fixture
def strategy_manager() -> StrategyManager:
    mgr = StrategyManager()
    mgr.register(DummyStrategy())
    return mgr


@pytest.fixture
def feature_provider() -> FakeFeatureProvider:
    return FakeFeatureProvider()


@pytest.fixture
def inference_client() -> FakeInferenceClient:
    return FakeInferenceClient()


@pytest.fixture
def publisher() -> FakeSignalPublisher:
    return FakeSignalPublisher()


@pytest.fixture
def metrics() -> FakeMetricPublisher:
    return FakeMetricPublisher()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def pipeline(
    strategy_manager: StrategyManager,
    feature_provider: FakeFeatureProvider,
    inference_client: FakeInferenceClient,
    publisher: FakeSignalPublisher,
    metrics: FakeMetricPublisher,
    clock: FakeClock,
) -> StrategyPipeline:
    return StrategyPipeline(
        strategy_manager=strategy_manager,
        feature_provider=feature_provider,
        inference_client=inference_client,
        metric_publisher=metrics,
        signal_publisher=publisher,
        clock=clock,
        risk_precheck_enabled=True,
        events_enabled=True,
        metrics_enabled=True,
    )


class TestEvaluate:
    async def test_with_explicit_prediction(self, pipeline: StrategyPipeline) -> None:
        request = StrategyEvaluationRequest(
            symbol="AAPL",
            model_name="default",
            strategy_name="dummy",
            prediction=0.85,
            prediction_confidence=0.9,
        )
        result = await pipeline.evaluate(request)
        assert result.decision.signal is not None

    async def test_with_features_and_prediction(self, pipeline: StrategyPipeline) -> None:
        request = StrategyEvaluationRequest(
            symbol="AAPL",
            model_name="default",
            strategy_name="dummy",
            prediction=0.7,
            prediction_confidence=0.8,
            features={"sma_20": 42.5},
        )
        result = await pipeline.evaluate(request)
        assert result.errors == []

    async def test_inference_client_used(
        self, pipeline: StrategyPipeline, inference_client: FakeInferenceClient
    ) -> None:
        inference_client.predictions["AAPL"] = (0.9, 0.85)
        request = StrategyEvaluationRequest(
            symbol="AAPL",
            model_name="default",
            strategy_name="dummy",
        )
        result = await pipeline.evaluate(request)
        assert result.decision.signal is not None

    async def test_events_emitted(
        self, pipeline: StrategyPipeline, publisher: FakeSignalPublisher
    ) -> None:
        request = StrategyEvaluationRequest(
            symbol="AAPL",
            model_name="default",
            strategy_name="dummy",
            prediction=0.5,
            prediction_confidence=0.7,
        )
        await pipeline.evaluate(request)
        topics = [e[0] for e in publisher.events]
        assert "strategy.evaluated" in topics
        assert "signals.generated" in topics

    async def test_metrics_recorded(
        self, pipeline: StrategyPipeline, metrics: FakeMetricPublisher
    ) -> None:
        request = StrategyEvaluationRequest(
            symbol="AAPL",
            model_name="default",
            strategy_name="dummy",
            prediction=0.5,
            prediction_confidence=0.7,
        )
        await pipeline.evaluate(request)
        assert len(metrics.signals) >= 1


class TestEvaluateBatch:
    async def test_batch_all_success(self, pipeline: StrategyPipeline) -> None:
        requests = [
            StrategyEvaluationRequest(symbol="AAPL", prediction=0.5, prediction_confidence=0.7),
            StrategyEvaluationRequest(symbol="MSFT", prediction=0.6, prediction_confidence=0.8),
        ]
        results = await pipeline.evaluate_batch(requests)
        assert len(results) == 2


class TestReloadStrategy:
    async def test_reload_existing(self, pipeline: StrategyPipeline) -> None:
        ok = await pipeline.reload_strategy("dummy")
        assert ok

    async def test_reload_nonexistent(self, pipeline: StrategyPipeline) -> None:
        ok = await pipeline.reload_strategy("nonexistent")
        assert not ok


class TestStatistics:
    async def test_statistics_tracked(self, pipeline: StrategyPipeline) -> None:
        request = StrategyEvaluationRequest(
            symbol="AAPL",
            model_name="default",
            strategy_name="dummy",
            prediction=0.5,
            prediction_confidence=0.7,
        )
        await pipeline.evaluate(request)
        stats = pipeline.statistics
        assert stats["total_evaluations"] >= 1
        assert stats["total_signals"] >= 1

    async def test_reload_count_increments(self, pipeline: StrategyPipeline) -> None:
        await pipeline.reload_strategy("dummy")
        assert pipeline.strategy_manager.reload_count >= 1
