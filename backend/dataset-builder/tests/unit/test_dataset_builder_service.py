"""Unit tests for DatasetBuilderService and DatasetQueryService.

Uses fakes for every port (MarketDataSource, FeatureSource, the three
repository/storage ports, GitInfoProvider) — no DB, no network — proving
the full pipeline (fetch -> align -> label -> split -> audit -> quality
report -> manifest -> persist) is correct independent of any concrete
adapter.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from aqros_dataset_builder.domain.models import (
    BarInterval,
    BuildStatus,
    DatasetBuildRun,
    DatasetDefinition,
    FeatureValue,
    LabelType,
    OHLCVBar,
    PredictionHorizon,
    SplitStrategy,
    WalkForwardParams,
)
from aqros_dataset_builder.domain.ports import (
    DatasetBuildRunRepository,
    DatasetDefinitionRepository,
    DatasetStorage,
    FeatureSource,
    GitInfoProvider,
    MarketDataSource,
    UpstreamSourceError,
)
from aqros_dataset_builder.domain.services import DatasetBuilderService, DatasetQueryService

FIXED_NOW = datetime(2024, 6, 1, tzinfo=UTC)
_EPOCH = datetime(2024, 1, 1, tzinfo=UTC)


def _bar(day: int, close: float) -> OHLCVBar:
    from datetime import timedelta

    event_time = _EPOCH + timedelta(days=day - 1)
    return OHLCVBar(
        symbol="AAPL",
        event_time=event_time,
        interval=BarInterval.DAILY,
        open=Decimal(str(close)),
        high=Decimal(str(close + 1)),
        low=Decimal(str(close - 1)),
        close=Decimal(str(close)),
        volume=1000,
        source="fake",
        knowledge_time=event_time,
    )


def _feature_value(day: int, value: float, feature_name: str = "sma_20") -> FeatureValue:
    from datetime import timedelta

    event_time = _EPOCH + timedelta(days=day - 1)
    return FeatureValue(
        symbol="AAPL",
        feature_name=feature_name,
        feature_version=1,
        event_time=event_time,
        value=value,
        knowledge_time=event_time,
    )


class FakeMarketDataSource(MarketDataSource):
    def __init__(self, bars: list[OHLCVBar] | None = None, raise_error: bool = False) -> None:
        self.bars = bars or []
        self.raise_error = raise_error

    async def get_bars(self, symbol, *, start=None, end=None, interval=BarInterval.DAILY):
        if self.raise_error:
            raise UpstreamSourceError("simulated market-data failure")
        return self.bars


class FakeFeatureSource(FeatureSource):
    def __init__(self, values_by_feature: dict[str, list[FeatureValue]] | None = None) -> None:
        self.values_by_feature = values_by_feature or {}

    async def get_feature_values(self, symbol, feature_name, *, start=None, end=None):
        return self.values_by_feature.get(feature_name, [])


class FakeDefinitionRepository(DatasetDefinitionRepository):
    def __init__(self) -> None:
        self.definitions: dict[tuple[str, int], DatasetDefinition] = {}

    async def create_definition(self, definition: DatasetDefinition) -> DatasetDefinition:
        self.definitions[(definition.name, definition.version)] = definition
        return definition

    async def get_definition(self, name: str, version: int) -> DatasetDefinition | None:
        return self.definitions.get((name, version))

    async def get_latest_definition(self, name: str) -> DatasetDefinition | None:
        matches = [d for (n, _v), d in self.definitions.items() if n == name]
        return max(matches, key=lambda d: d.version) if matches else None

    async def list_definitions(self) -> list[DatasetDefinition]:
        return list(self.definitions.values())


class FakeRunRepository(DatasetBuildRunRepository):
    def __init__(self) -> None:
        self.runs: dict[int, DatasetBuildRun] = {}
        self._next_id = 1

    async def create_run(self, run: DatasetBuildRun) -> DatasetBuildRun:
        created = replace(run, id=self._next_id)
        self.runs[self._next_id] = created
        self._next_id += 1
        return created

    async def complete_run(self, run: DatasetBuildRun) -> None:
        assert run.id is not None
        self.runs[run.id] = run

    async def get_run(self, run_id: int) -> DatasetBuildRun | None:
        return self.runs.get(run_id)

    async def list_runs(self, *, dataset_name=None, limit=100, offset=0):
        results = [
            r for r in self.runs.values() if dataset_name is None or r.dataset_name == dataset_name
        ]
        return sorted(results, key=lambda r: r.started_at, reverse=True)[offset : offset + limit]


class FakeDatasetStorage(DatasetStorage):
    def __init__(self) -> None:
        self.datasets: dict[str, list[dict[str, object]]] = {}
        self.manifests: dict[str, dict[str, object]] = {}
        self._counter = 0

    async def write_dataset(self, dataset_name, dataset_version, run_id, rows):
        self._counter += 1
        path = f"fake://{dataset_name}/v{dataset_version}/run_{run_id}.parquet"
        self.datasets[path] = rows
        return path

    async def read_dataset(self, path: str) -> list[dict[str, object]]:
        return self.datasets.get(path, [])

    async def compute_checksum(self, path: str) -> str:
        return f"checksum-of-{path}"

    async def write_manifest(self, dataset_name, dataset_version, run_id, manifest):
        path = f"fake://{dataset_name}/v{dataset_version}/run_{run_id}.manifest.json"
        self.manifests[path] = manifest
        return path

    async def read_manifest(self, path: str) -> dict[str, object]:
        return self.manifests.get(path, {})


class FakeGitInfoProvider(GitInfoProvider):
    def __init__(self, sha: str | None = "deadbeef") -> None:
        self.sha = sha

    async def get_commit_sha(self) -> str | None:
        return self.sha


def _make_definition(**overrides: object) -> DatasetDefinition:
    defaults: dict[str, object] = {
        "name": "aapl_test",
        "version": 1,
        "symbols": ("AAPL",),
        "feature_names": ("sma_20",),
        "label_type": LabelType.BINARY_DIRECTION,
        "horizon": PredictionHorizon.ONE_DAY,
        "split_strategy": SplitStrategy.WALK_FORWARD,
        "split_params": WalkForwardParams(
            train_size=3, validation_size=1, test_size=1, step_size=1
        ),
        "start_date": date(2024, 1, 1),
        "end_date": date(2024, 2, 1),
        "created_at": FIXED_NOW,
    }
    defaults.update(overrides)
    return DatasetDefinition(**defaults)  # type: ignore[arg-type]


def _build_service(
    market_data: FakeMarketDataSource,
    feature_source: FakeFeatureSource,
    definition_repo: FakeDefinitionRepository,
    run_repo: FakeRunRepository,
    storage: FakeDatasetStorage,
    git_provider: FakeGitInfoProvider | None = None,
) -> DatasetBuilderService:
    return DatasetBuilderService(
        market_data,
        feature_source,
        definition_repo,
        run_repo,
        storage,
        git_provider or FakeGitInfoProvider(),
        market_data_source_url="http://market-data:8002",
        feature_store_source_url="http://feature-store:8003",
    )


@pytest.mark.asyncio
async def test_build_dataset_end_to_end_succeeds() -> None:
    bars = [_bar(day, close=100.0 + day) for day in range(1, 11)]
    features = [_feature_value(day, value=float(day)) for day in range(1, 11)]

    market_data = FakeMarketDataSource(bars=bars)
    feature_source = FakeFeatureSource({"sma_20": features})
    definition_repo = FakeDefinitionRepository()
    run_repo = FakeRunRepository()
    storage = FakeDatasetStorage()
    service = _build_service(market_data, feature_source, definition_repo, run_repo, storage)

    definition = _make_definition()
    await service.create_definition(definition)

    run = await service.build_dataset("aapl_test", 1, now=FIXED_NOW)

    assert run.status == BuildStatus.SUCCEEDED
    assert run.bars_read == 10
    assert run.rows_generated > 0
    assert run.leakage_audit_passed is True
    assert run.parquet_path is not None
    assert run.manifest_path is not None
    assert run.quality_report is not None
    assert run.quality_report.total_rows == run.rows_generated


@pytest.mark.asyncio
async def test_build_dataset_writes_manifest_with_reproducibility_fields() -> None:
    bars = [_bar(day, close=100.0 + day) for day in range(1, 11)]
    features = [_feature_value(day, value=float(day)) for day in range(1, 11)]

    market_data = FakeMarketDataSource(bars=bars)
    feature_source = FakeFeatureSource({"sma_20": features})
    definition_repo = FakeDefinitionRepository()
    run_repo = FakeRunRepository()
    storage = FakeDatasetStorage()
    git_provider = FakeGitInfoProvider(sha="abc123")
    service = _build_service(
        market_data, feature_source, definition_repo, run_repo, storage, git_provider
    )
    query_service = DatasetQueryService(definition_repo, run_repo, storage)

    definition = _make_definition()
    await service.create_definition(definition)
    run = await service.build_dataset("aapl_test", 1, now=FIXED_NOW)

    manifest = await query_service.get_manifest(run.id)
    assert manifest is not None
    assert manifest["dataset_name"] == "aapl_test"
    assert manifest["dataset_version"] == 1
    assert manifest["git_commit"] == "abc123"
    assert manifest["checksum"]
    assert manifest["feature_versions"] == {"sma_20": 1}
    assert manifest["market_data_source_url"] == "http://market-data:8002"
    assert manifest["feature_store_source_url"] == "http://feature-store:8003"
    assert "quality_report" in manifest


@pytest.mark.asyncio
async def test_build_dataset_handles_missing_git_commit_gracefully() -> None:
    bars = [_bar(day, close=100.0 + day) for day in range(1, 11)]
    features = [_feature_value(day, value=float(day)) for day in range(1, 11)]

    market_data = FakeMarketDataSource(bars=bars)
    feature_source = FakeFeatureSource({"sma_20": features})
    definition_repo = FakeDefinitionRepository()
    run_repo = FakeRunRepository()
    storage = FakeDatasetStorage()
    git_provider = FakeGitInfoProvider(sha=None)
    service = _build_service(
        market_data, feature_source, definition_repo, run_repo, storage, git_provider
    )

    definition = _make_definition()
    await service.create_definition(definition)
    run = await service.build_dataset("aapl_test", 1, now=FIXED_NOW)

    assert run.status == BuildStatus.SUCCEEDED
    query_service = DatasetQueryService(definition_repo, run_repo, storage)
    manifest = await query_service.get_manifest(run.id)
    assert manifest is not None
    assert manifest["git_commit"] is None


@pytest.mark.asyncio
async def test_build_dataset_with_no_bars_succeeds_with_zero_rows() -> None:
    market_data = FakeMarketDataSource(bars=[])
    feature_source = FakeFeatureSource({})
    definition_repo = FakeDefinitionRepository()
    run_repo = FakeRunRepository()
    storage = FakeDatasetStorage()
    service = _build_service(market_data, feature_source, definition_repo, run_repo, storage)

    definition = _make_definition()
    await service.create_definition(definition)
    run = await service.build_dataset("aapl_test", 1, now=FIXED_NOW)

    assert run.status == BuildStatus.SUCCEEDED
    assert run.bars_read == 0
    assert run.rows_generated == 0
    assert run.parquet_path is None
    assert run.manifest_path is None
    assert len(run.rejection_reasons) == 1


@pytest.mark.asyncio
async def test_build_dataset_rejects_rows_missing_features() -> None:
    # Bars for 10 days, but the feature only exists for the first 3 days ->
    # rows for the other 7 days must be rejected, never fabricated.
    bars = [_bar(day, close=100.0 + day) for day in range(1, 11)]
    features = [_feature_value(day, value=float(day)) for day in range(1, 4)]

    market_data = FakeMarketDataSource(bars=bars)
    feature_source = FakeFeatureSource({"sma_20": features})
    definition_repo = FakeDefinitionRepository()
    run_repo = FakeRunRepository()
    storage = FakeDatasetStorage()
    service = _build_service(market_data, feature_source, definition_repo, run_repo, storage)

    definition = _make_definition(
        split_strategy=SplitStrategy.WALK_FORWARD,
        split_params=WalkForwardParams(train_size=1, validation_size=1, test_size=1, step_size=1),
    )
    await service.create_definition(definition)
    run = await service.build_dataset("aapl_test", 1, now=FIXED_NOW)

    assert run.status == BuildStatus.SUCCEEDED
    assert run.rows_rejected > 0


@pytest.mark.asyncio
async def test_build_dataset_propagates_upstream_error_and_records_failure() -> None:
    market_data = FakeMarketDataSource(raise_error=True)
    feature_source = FakeFeatureSource({})
    definition_repo = FakeDefinitionRepository()
    run_repo = FakeRunRepository()
    storage = FakeDatasetStorage()
    service = _build_service(market_data, feature_source, definition_repo, run_repo, storage)

    definition = _make_definition()
    await service.create_definition(definition)

    with pytest.raises(UpstreamSourceError):
        await service.build_dataset("aapl_test", 1, now=FIXED_NOW)

    runs = await run_repo.list_runs(dataset_name="aapl_test")
    assert len(runs) == 1
    assert runs[0].status == BuildStatus.FAILED
    assert runs[0].error_message is not None


@pytest.mark.asyncio
async def test_build_dataset_raises_for_unknown_definition() -> None:
    market_data = FakeMarketDataSource(bars=[])
    feature_source = FakeFeatureSource({})
    definition_repo = FakeDefinitionRepository()
    run_repo = FakeRunRepository()
    storage = FakeDatasetStorage()
    service = _build_service(market_data, feature_source, definition_repo, run_repo, storage)

    with pytest.raises(ValueError):
        await service.build_dataset("nonexistent", 1, now=FIXED_NOW)


@pytest.mark.asyncio
async def test_query_service_preview_rows_returns_persisted_data() -> None:
    bars = [_bar(day, close=100.0 + day) for day in range(1, 11)]
    features = [_feature_value(day, value=float(day)) for day in range(1, 11)]

    market_data = FakeMarketDataSource(bars=bars)
    feature_source = FakeFeatureSource({"sma_20": features})
    definition_repo = FakeDefinitionRepository()
    run_repo = FakeRunRepository()
    storage = FakeDatasetStorage()
    service = _build_service(market_data, feature_source, definition_repo, run_repo, storage)
    query_service = DatasetQueryService(definition_repo, run_repo, storage)

    definition = _make_definition()
    await service.create_definition(definition)
    run = await service.build_dataset("aapl_test", 1, now=FIXED_NOW)

    preview = await query_service.preview_rows(run.id, limit=3)
    assert len(preview) <= 3
    assert len(preview) > 0
