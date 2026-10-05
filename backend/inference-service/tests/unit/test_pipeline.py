"""Unit tests for PredictionPipeline (orchestration flow).

Uses fakes for every port — no network, no Redis — proving the orchestration
logic (resolve model → fetch features → validate → infer → emit) is correct.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
from aqros_inference_service.adapters.dummy_backend import DummyModelBackend
from aqros_inference_service.application.pipeline import PredictionPipeline
from aqros_inference_service.domain.model_manager import LoadedModel, ModelManager
from aqros_inference_service.domain.models import (
    PredictionRequest,
    PredictionResult,
    PredictionStatus,
    PredictionType,
)
from aqros_inference_service.domain.validation import FeatureValidator
from aqros_inference_service.ports.ports import (
    AuditRepository,
    Clock,
    FeatureProvider,
    MetricPublisher,
    ModelLoader,
    PredictionPublisher,
)


class FakeClock(Clock):
    def __init__(self, dt: datetime | None = None) -> None:
        self._dt = dt or datetime(2024, 6, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self._dt


class FakeFeatureProvider(FeatureProvider):
    def __init__(self) -> None:
        self.data: dict[str, dict[str, Any]] = {}
        self._healthy = True

    async def get_features(self, symbol: str) -> dict[str, Any]:
        return self.data.get(symbol.upper(), {})

    async def health_check(self) -> bool:
        return self._healthy


class FakePredictionPublisher(PredictionPublisher):
    def __init__(self) -> None:
        self.events: list[tuple[str, bytes]] = []

    async def publish(self, topic: str, payload: bytes) -> None:
        self.events.append((topic, payload))


class FakeMetricPublisher(MetricPublisher):
    def __init__(self) -> None:
        self.predictions: list[PredictionResult] = []
        self.cache_hits = 0
        self.cache_misses = 0
        self.reloads = 0
        self.validation_failures = 0

    async def record_prediction(self, result: PredictionResult) -> None:
        self.predictions.append(result)

    async def record_cache_hit(self) -> None:
        self.cache_hits += 1

    async def record_cache_miss(self) -> None:
        self.cache_misses += 1

    async def record_reload(self) -> None:
        self.reloads += 1

    async def record_validation_failure(self) -> None:
        self.validation_failures += 1


class FakeAuditRepository(AuditRepository):
    def __init__(self) -> None:
        self.records: list[tuple[PredictionResult, object]] = []

    async def record_prediction(self, result: PredictionResult, metadata: object) -> None:
        self.records.append((result, metadata))

    async def record_model_event(
        self,
        event_type: str,
        model_name: str,
        model_version: int,
        detail: str | None = None,
    ) -> None:
        pass


class FakeModelLoader(ModelLoader):
    def __init__(self) -> None:
        self.models: dict[tuple[str, int], LoadedModel] = {}

    async def load(self, name: str, version: int) -> LoadedModel:
        key = (name, version)
        if key in self.models:
            return self.models[key]
        raise ValueError(f"Model '{name}' version {version} not found in fake")

    async def get_latest_version(self, name: str) -> int | None:
        versions = [v for (n, v) in self.models if n == name]
        return max(versions) if versions else None


@pytest.fixture
def model_loader() -> FakeModelLoader:
    return FakeModelLoader()


@pytest.fixture
async def model_manager(model_loader: FakeModelLoader) -> ModelManager:
    mgr = ModelManager(loader=model_loader, max_loaded_models=10)
    loaded = LoadedModel(
        name="default",
        version=1,
        model_type=PredictionType.REGRESSION,
        backend=MagicMock(),
        checksum="abc123",
        metadata={"feature_version": 1},
    )
    model_loader.models[("default", 1)] = loaded
    await mgr.set_production("default", 1)
    return mgr


@pytest.fixture
def feature_provider() -> FakeFeatureProvider:
    return FakeFeatureProvider()


@pytest.fixture
def backend() -> DummyModelBackend:
    return DummyModelBackend()


@pytest.fixture
def validator() -> FeatureValidator:
    return FeatureValidator()


@pytest.fixture
def publisher() -> FakePredictionPublisher:
    return FakePredictionPublisher()


@pytest.fixture
def metrics() -> FakeMetricPublisher:
    return FakeMetricPublisher()


@pytest.fixture
def audit() -> FakeAuditRepository:
    return FakeAuditRepository()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
async def pipeline(
    model_manager: ModelManager,
    feature_provider: FakeFeatureProvider,
    backend: DummyModelBackend,
    validator: FeatureValidator,
    model_loader: FakeModelLoader,
    publisher: FakePredictionPublisher,
    metrics: FakeMetricPublisher,
    audit: FakeAuditRepository,
    clock: FakeClock,
) -> PredictionPipeline:
    return PredictionPipeline(
        model_manager=model_manager,
        feature_provider=feature_provider,
        backend=backend,
        validator=validator,
        model_loader=model_loader,
        prediction_publisher=publisher,
        metric_publisher=metrics,
        audit_repository=audit,
        clock=clock,
        events_enabled=True,
        metrics_enabled=True,
        audit_enabled=True,
    )


class TestPredict:
    async def test_successful_prediction(
        self,
        pipeline: PredictionPipeline,
        feature_provider: FakeFeatureProvider,
    ) -> None:
        feature_provider.data["AAPL"] = {"sma_20": 42.5, "rsi_14": 65.0}
        result = await pipeline.predict("AAPL", "default")
        assert result.status == PredictionStatus.SUCCESS
        assert result.symbol == "AAPL"
        assert isinstance(result.value, float)
        assert result.model_name == "default"
        assert result.model_version == 1

    async def test_with_explicit_features(
        self,
        pipeline: PredictionPipeline,
    ) -> None:
        result = await pipeline.predict(
            "AAPL", "default", features={"sma_20": 100.0, "rsi_14": 50.0}
        )
        assert result.status == PredictionStatus.SUCCESS
        assert result.value == 75.0  # average of 100 and 50

    async def test_model_not_found(
        self,
        pipeline: PredictionPipeline,
    ) -> None:
        result = await pipeline.predict("AAPL", "nonexistent")
        assert result.status == PredictionStatus.FAILED
        assert result.error is not None
        assert "MODEL_NOT_FOUND" in result.error.code

    async def test_feature_validation_failure(
        self,
        pipeline: PredictionPipeline,
        feature_provider: FakeFeatureProvider,
    ) -> None:
        feature_provider.data["AAPL"] = {"feat": float("nan")}
        result = await pipeline.predict("AAPL", "default")
        assert result.status == PredictionStatus.FAILED
        assert result.error is not None
        assert "FEATURE_VALIDATION_FAILED" in result.error.code

    async def test_events_emitted_on_success(
        self,
        pipeline: PredictionPipeline,
        feature_provider: FakeFeatureProvider,
        publisher: FakePredictionPublisher,
    ) -> None:
        feature_provider.data["AAPL"] = {"sma_20": 42.5}
        await pipeline.predict("AAPL", "default")
        topics = [e[0] for e in publisher.events]
        assert "inference.prediction.requested" in topics
        assert "inference.prediction.started" in topics
        assert "inference.prediction.completed" in topics

    async def test_events_emitted_on_failure(
        self,
        pipeline: PredictionPipeline,
        publisher: FakePredictionPublisher,
    ) -> None:
        await pipeline.predict("AAPL", "nonexistent")
        topics = [e[0] for e in publisher.events]
        assert "inference.prediction.requested" in topics
        assert "inference.prediction.failed" in topics

    async def test_metrics_recorded(
        self,
        pipeline: PredictionPipeline,
        feature_provider: FakeFeatureProvider,
        metrics: FakeMetricPublisher,
    ) -> None:
        feature_provider.data["AAPL"] = {"sma_20": 42.5}
        await pipeline.predict("AAPL", "default")
        assert len(metrics.predictions) == 1

    async def test_audit_recorded(
        self,
        pipeline: PredictionPipeline,
        feature_provider: FakeFeatureProvider,
        audit: FakeAuditRepository,
    ) -> None:
        feature_provider.data["AAPL"] = {"sma_20": 42.5}
        await pipeline.predict("AAPL", "default")
        assert len(audit.records) == 1

    async def test_cache_miss_on_feature_fetch(
        self,
        pipeline: PredictionPipeline,
        feature_provider: FakeFeatureProvider,
        metrics: FakeMetricPublisher,
    ) -> None:
        feature_provider.data["AAPL"] = {"sma_20": 42.5}
        await pipeline.predict("AAPL", "default")
        assert metrics.cache_misses >= 1

    async def test_prediction_with_correlation_id(
        self,
        pipeline: PredictionPipeline,
        feature_provider: FakeFeatureProvider,
    ) -> None:
        feature_provider.data["AAPL"] = {"sma_20": 42.5}
        result = await pipeline.predict("AAPL", "default", correlation_id="test-corr")
        assert result.status == PredictionStatus.SUCCESS

    async def test_symbol_uppercased(
        self,
        pipeline: PredictionPipeline,
        feature_provider: FakeFeatureProvider,
    ) -> None:
        feature_provider.data["AAPL"] = {"sma_20": 42.5}
        result = await pipeline.predict("aapl", "default")
        assert result.symbol == "AAPL"


class TestPredictBatch:
    async def test_batch_all_success(
        self,
        pipeline: PredictionPipeline,
        feature_provider: FakeFeatureProvider,
    ) -> None:
        feature_provider.data["AAPL"] = {"sma_20": 1.0}
        feature_provider.data["MSFT"] = {"rsi_14": 2.0}
        requests = [
            PredictionRequest(symbol="AAPL", model_name="default"),
            PredictionRequest(symbol="MSFT", model_name="default"),
        ]
        results = await pipeline.predict_batch(requests)
        assert len(results) == 2
        assert all(r.status == PredictionStatus.SUCCESS for r in results)

    async def test_batch_mixed_results(
        self,
        pipeline: PredictionPipeline,
        feature_provider: FakeFeatureProvider,
    ) -> None:
        feature_provider.data["AAPL"] = {"sma_20": 1.0}
        requests = [
            PredictionRequest(symbol="AAPL", model_name="default"),
            PredictionRequest(symbol="UNKNOWN", model_name="nonexistent"),
        ]
        results = await pipeline.predict_batch(requests)
        assert len(results) == 2
        statuses = [r.status for r in results]
        assert PredictionStatus.SUCCESS in statuses
        assert PredictionStatus.FAILED in statuses


class TestReloadModel:
    async def test_reload_specific_version(
        self,
        pipeline: PredictionPipeline,
        model_loader: FakeModelLoader,
    ) -> None:
        loaded = LoadedModel(
            name="default",
            version=2,
            model_type=PredictionType.REGRESSION,
            backend=MagicMock(),
            checksum="def456",
        )
        model_loader.models[("default", 2)] = loaded
        success = await pipeline.reload_model("default", 2)
        assert success

    async def test_reload_current_production(
        self,
        pipeline: PredictionPipeline,
        model_manager: ModelManager,
        model_loader: FakeModelLoader,
    ) -> None:
        await model_manager.set_production("default", 1)
        success = await pipeline.reload_model("default")
        assert success

    async def test_reload_nonexistent_production(
        self,
        pipeline: PredictionPipeline,
    ) -> None:
        success = await pipeline.reload_model("nonexistent")
        assert not success


class TestRollbackModel:
    async def test_rollback_success(
        self,
        pipeline: PredictionPipeline,
        model_manager: ModelManager,
        model_loader: FakeModelLoader,
    ) -> None:
        loaded2 = LoadedModel(
            name="default",
            version=2,
            model_type=PredictionType.REGRESSION,
            backend=MagicMock(),
            checksum="def456",
        )
        model_loader.models[("default", 2)] = loaded2
        await model_manager.set_production("default", 2)
        # Now we have previous=1, current=2 → rollback should go back to 1
        success = await pipeline.rollback_model("default")
        assert success
        assert model_manager.get_production_version("default") == 1

    async def test_rollback_no_previous(
        self,
        pipeline: PredictionPipeline,
    ) -> None:
        success = await pipeline.rollback_model("default")
        assert not success


class TestStatistics:
    async def test_statistics_tracked(
        self,
        pipeline: PredictionPipeline,
        feature_provider: FakeFeatureProvider,
    ) -> None:
        feature_provider.data["AAPL"] = {"sma_20": 42.5}
        await pipeline.predict("AAPL", "default")
        stats = pipeline.statistics
        assert stats["total_requests"] >= 1
        assert stats["total_success"] >= 1
        assert stats["total_latency_ms"] >= 0

    async def test_failure_tracked(
        self,
        pipeline: PredictionPipeline,
    ) -> None:
        await pipeline.predict("AAPL", "nonexistent")
        stats = pipeline.statistics
        assert stats["total_failures"] >= 1


class TestEventsDisabled:
    async def test_no_events_when_disabled(
        self,
        model_manager: ModelManager,
        feature_provider: FakeFeatureProvider,
        backend: DummyModelBackend,
        validator: FeatureValidator,
        model_loader: FakeModelLoader,
        publisher: FakePredictionPublisher,
        metrics: FakeMetricPublisher,
        audit: FakeAuditRepository,
        clock: FakeClock,
    ) -> None:
        pipe = PredictionPipeline(
            model_manager=model_manager,
            feature_provider=feature_provider,
            backend=backend,
            validator=validator,
            model_loader=model_loader,
            prediction_publisher=publisher,
            metric_publisher=metrics,
            audit_repository=audit,
            clock=clock,
            events_enabled=False,
            metrics_enabled=True,
            audit_enabled=True,
        )
        feature_provider.data["AAPL"] = {"sma_20": 42.5}
        await pipe.predict("AAPL", "default")
        assert len(publisher.events) == 0
