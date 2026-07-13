"""Unit tests for FeatureEngineeringService and FeatureQueryService.

Uses fakes for every port (MarketDataSource, the three repositories) — no
DB, no network — proving the domain orchestration logic (fetch -> compute ->
validate -> persist, full vs incremental mode selection) is correct
independent of any concrete adapter.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from aqros_feature_store.domain.feature_definitions import FEATURE_REGISTRY
from aqros_feature_store.domain.models import (
    BarInterval,
    ComputationMode,
    ComputationStatus,
    FeatureComputationRun,
    FeatureDefinition,
    FeatureStatistics,
    FeatureValue,
    OHLCVBar,
)
from aqros_feature_store.domain.ports import (
    FeatureComputationRunRepository,
    FeatureDefinitionRepository,
    FeatureValueRepository,
    MarketDataSource,
    MarketDataSourceError,
)
from aqros_feature_store.domain.services import FeatureEngineeringService, FeatureQueryService

FIXED_NOW = datetime(2024, 6, 1, tzinfo=UTC)


_EPOCH = datetime(2024, 1, 1, tzinfo=UTC)


def _bar(day: int, close: float = 100.0, **overrides: object) -> OHLCVBar:
    event_time = _EPOCH + timedelta(days=day - 1)
    defaults: dict[str, object] = {
        "symbol": "AAPL",
        "event_time": event_time,
        "interval": BarInterval.DAILY,
        "open": Decimal(str(close)),
        "high": Decimal(str(close + 1)),
        "low": Decimal(str(close - 1)),
        "close": Decimal(str(close)),
        "volume": 1000,
        "source": "fake",
        "knowledge_time": event_time,
    }
    defaults.update(overrides)
    return OHLCVBar(**defaults)  # type: ignore[arg-type]


class FakeMarketDataSource(MarketDataSource):
    def __init__(self, bars: list[OHLCVBar] | None = None, raise_error: bool = False) -> None:
        self.bars = bars or []
        self.raise_error = raise_error
        self.last_start: date | None = None

    async def get_bars(
        self,
        symbol: str,
        *,
        start: date | None = None,
        end: date | None = None,
        interval: BarInterval = BarInterval.DAILY,
    ) -> list[OHLCVBar]:
        self.last_start = start
        if self.raise_error:
            raise MarketDataSourceError("simulated upstream failure")
        return self.bars


class FakeDefinitionRepository(FeatureDefinitionRepository):
    def __init__(self) -> None:
        self.definitions: dict[tuple[str, int], FeatureDefinition] = {}

    async def upsert_definition(self, definition: FeatureDefinition) -> None:
        self.definitions[(definition.name, definition.version)] = definition

    async def get_definition(self, name: str, version: int) -> FeatureDefinition | None:
        return self.definitions.get((name, version))

    async def get_latest_definition(self, name: str) -> FeatureDefinition | None:
        matches = [d for (n, _v), d in self.definitions.items() if n == name]
        return max(matches, key=lambda d: d.version) if matches else None

    async def list_definitions(self) -> list[FeatureDefinition]:
        return list(self.definitions.values())


class FakeValueRepository(FeatureValueRepository):
    def __init__(self) -> None:
        self.stored: list[FeatureValue] = []

    async def upsert_values(self, values: list[FeatureValue]) -> int:
        self.stored.extend(values)
        return len(values)

    async def get_values(
        self,
        symbol: str,
        feature_name: str,
        *,
        feature_version: int | None = None,
        start: date | None = None,
        end: date | None = None,
        as_of: datetime | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[FeatureValue]:
        results = [
            v
            for v in self.stored
            if v.symbol == symbol.upper()
            and v.feature_name == feature_name
            and (feature_version is None or v.feature_version == feature_version)
            and (as_of is None or v.knowledge_time <= as_of)
        ]
        return sorted(results, key=lambda v: v.event_time)[offset : offset + limit]

    async def count_values(
        self,
        symbol: str,
        feature_name: str,
        *,
        feature_version: int | None = None,
        start: date | None = None,
        end: date | None = None,
        as_of: datetime | None = None,
    ) -> int:
        values = await self.get_values(
            symbol,
            feature_name,
            feature_version=feature_version,
            start=start,
            end=end,
            as_of=as_of,
            limit=10_000,
        )
        return len(values)

    async def get_latest_event_time(
        self, symbol: str, feature_name: str, feature_version: int
    ) -> datetime | None:
        matches = [
            v.event_time
            for v in self.stored
            if v.symbol == symbol.upper()
            and v.feature_name == feature_name
            and v.feature_version == feature_version
        ]
        return max(matches) if matches else None

    async def compute_statistics(
        self,
        symbol: str,
        feature_name: str,
        feature_version: int,
        *,
        start: date | None = None,
        end: date | None = None,
    ) -> FeatureStatistics:
        values = [
            v.value
            for v in self.stored
            if v.symbol == symbol.upper()
            and v.feature_name == feature_name
            and v.feature_version == feature_version
        ]
        if not values:
            return FeatureStatistics(
                symbol=symbol.upper(),
                feature_name=feature_name,
                feature_version=feature_version,
                count=0,
                mean=None,
                std=None,
                minimum=None,
                maximum=None,
            )
        mean = sum(values) / len(values)
        return FeatureStatistics(
            symbol=symbol.upper(),
            feature_name=feature_name,
            feature_version=feature_version,
            count=len(values),
            mean=mean,
            std=None,
            minimum=min(values),
            maximum=max(values),
        )


class FakeRunRepository(FeatureComputationRunRepository):
    def __init__(self) -> None:
        self.runs: dict[int, FeatureComputationRun] = {}
        self._next_id = 1

    async def create_run(self, run: FeatureComputationRun) -> FeatureComputationRun:
        created = replace(run, id=self._next_id)
        self.runs[self._next_id] = created
        self._next_id += 1
        return created

    async def complete_run(self, run: FeatureComputationRun) -> None:
        assert run.id is not None
        self.runs[run.id] = run

    async def get_run(self, run_id: int) -> FeatureComputationRun | None:
        return self.runs.get(run_id)

    async def list_runs(
        self, *, symbol: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[FeatureComputationRun]:
        results = [r for r in self.runs.values() if symbol is None or r.symbol == symbol.upper()]
        return sorted(results, key=lambda r: r.started_at, reverse=True)[offset : offset + limit]


def _make_bars(num_bars: int) -> list[OHLCVBar]:
    return [_bar(day, close=100.0 + day) for day in range(1, num_bars + 1)]


@pytest.mark.asyncio
async def test_full_computation_persists_features_and_records_run() -> None:
    bars = _make_bars(30)
    market_data = FakeMarketDataSource(bars=bars)
    value_repo = FakeValueRepository()
    definition_repo = FakeDefinitionRepository()
    run_repo = FakeRunRepository()
    service = FeatureEngineeringService(market_data, value_repo, definition_repo, run_repo)

    run = await service.run_computation("aapl", mode=ComputationMode.FULL, now=FIXED_NOW)

    assert run.status == ComputationStatus.SUCCEEDED
    assert run.symbol == "AAPL"
    assert run.bars_read == 30
    assert run.features_persisted > 0
    assert len(value_repo.stored) == run.features_persisted
    # Every registered definition must have been registered.
    assert len(definition_repo.definitions) == len(FEATURE_REGISTRY)


@pytest.mark.asyncio
async def test_full_mode_always_fetches_from_the_beginning() -> None:
    market_data = FakeMarketDataSource(bars=_make_bars(30))
    value_repo = FakeValueRepository()
    definition_repo = FakeDefinitionRepository()
    run_repo = FakeRunRepository()
    service = FeatureEngineeringService(market_data, value_repo, definition_repo, run_repo)

    await service.run_computation("AAPL", mode=ComputationMode.FULL, now=FIXED_NOW)

    assert market_data.last_start is None


@pytest.mark.asyncio
async def test_incremental_mode_falls_back_to_full_fetch_when_no_prior_computation() -> None:
    market_data = FakeMarketDataSource(bars=_make_bars(30))
    value_repo = FakeValueRepository()
    definition_repo = FakeDefinitionRepository()
    run_repo = FakeRunRepository()
    service = FeatureEngineeringService(market_data, value_repo, definition_repo, run_repo)

    run = await service.run_computation("AAPL", mode=ComputationMode.INCREMENTAL, now=FIXED_NOW)

    assert run.status == ComputationStatus.SUCCEEDED
    assert market_data.last_start is None  # nothing computed yet -> full fetch


@pytest.mark.asyncio
async def test_incremental_mode_uses_high_water_mark_after_a_prior_full_run() -> None:
    # Enough bars (>= the largest min_bars_required, macd_signal/histogram at
    # 34) that every registered feature produces at least one value in the
    # full run — otherwise a never-computed feature's high-water mark stays
    # None and the incremental run correctly falls back to a full fetch,
    # which is a different behavior than this test targets.
    market_data = FakeMarketDataSource(bars=_make_bars(50))
    value_repo = FakeValueRepository()
    definition_repo = FakeDefinitionRepository()
    run_repo = FakeRunRepository()
    service = FeatureEngineeringService(
        market_data, value_repo, definition_repo, run_repo, lookback_buffer_days=5
    )

    await service.run_computation("AAPL", mode=ComputationMode.FULL, now=FIXED_NOW)
    await service.run_computation("AAPL", mode=ComputationMode.INCREMENTAL, now=FIXED_NOW)

    # After a full run, the high-water mark is the oldest "latest computed"
    # across features; the incremental run should request from
    # (mark - lookback_buffer_days), not from the very beginning.
    assert market_data.last_start is not None


@pytest.mark.asyncio
async def test_computation_with_no_bars_succeeds_with_zero_counts() -> None:
    market_data = FakeMarketDataSource(bars=[])
    value_repo = FakeValueRepository()
    definition_repo = FakeDefinitionRepository()
    run_repo = FakeRunRepository()
    service = FeatureEngineeringService(market_data, value_repo, definition_repo, run_repo)

    run = await service.run_computation("AAPL", mode=ComputationMode.FULL, now=FIXED_NOW)

    assert run.status == ComputationStatus.SUCCEEDED
    assert run.bars_read == 0
    assert run.features_persisted == 0
    assert value_repo.stored == []


@pytest.mark.asyncio
async def test_computation_failure_is_recorded_and_reraised() -> None:
    market_data = FakeMarketDataSource(raise_error=True)
    value_repo = FakeValueRepository()
    definition_repo = FakeDefinitionRepository()
    run_repo = FakeRunRepository()
    service = FeatureEngineeringService(market_data, value_repo, definition_repo, run_repo)

    with pytest.raises(MarketDataSourceError):
        await service.run_computation("AAPL", mode=ComputationMode.FULL, now=FIXED_NOW)

    runs = await run_repo.list_runs(symbol="AAPL")
    assert len(runs) == 1
    assert runs[0].status == ComputationStatus.FAILED
    assert runs[0].error_message is not None


@pytest.mark.asyncio
async def test_query_service_get_values_and_statistics() -> None:
    market_data = FakeMarketDataSource(bars=_make_bars(30))
    value_repo = FakeValueRepository()
    definition_repo = FakeDefinitionRepository()
    run_repo = FakeRunRepository()
    engineering = FeatureEngineeringService(market_data, value_repo, definition_repo, run_repo)
    await engineering.run_computation("AAPL", mode=ComputationMode.FULL, now=FIXED_NOW)

    query = FeatureQueryService(value_repo, definition_repo)
    values, total = await query.get_values("AAPL", "sma_20")
    assert total > 0
    assert len(values) == total

    stats = await query.get_statistics("AAPL", "sma_20", 1)
    assert stats.count == total
    assert stats.mean is not None


@pytest.mark.asyncio
async def test_query_service_as_of_excludes_values_computed_later() -> None:
    market_data = FakeMarketDataSource(bars=_make_bars(30))
    value_repo = FakeValueRepository()
    definition_repo = FakeDefinitionRepository()
    run_repo = FakeRunRepository()
    engineering = FeatureEngineeringService(market_data, value_repo, definition_repo, run_repo)
    await engineering.run_computation("AAPL", mode=ComputationMode.FULL, now=FIXED_NOW)

    query = FeatureQueryService(value_repo, definition_repo)
    before_computation = FIXED_NOW.replace(year=2023)
    values, total = await query.get_values("AAPL", "sma_20", as_of=before_computation)

    assert total == 0
    assert values == []
