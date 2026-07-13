"""Integration tests for the SQLAlchemy repositories against a real Postgres.

These prove the upsert-on-conflict logic, PIT ``as_of`` filtering, and
SQL-level statistics aggregation actually work against Postgres semantics
that a fake/in-memory repository can't verify.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from aqros_feature_store.adapters.repository import (
    SqlAlchemyFeatureComputationRunRepository,
    SqlAlchemyFeatureDefinitionRepository,
    SqlAlchemyFeatureValueRepository,
)
from aqros_feature_store.domain.models import (
    ComputationMode,
    ComputationStatus,
    FeatureCategory,
    FeatureComputationRun,
    FeatureDefinition,
    FeatureValue,
)

pytestmark = pytest.mark.integration


def _definition(name: str = "sma_20", version: int = 1) -> FeatureDefinition:
    return FeatureDefinition(
        name=name,
        version=version,
        category=FeatureCategory.TREND,
        description="20-period SMA",
        parameters={"window": 20},
        min_bars_required=20,
    )


def _value(day: int, value: float = 150.0, **overrides: object) -> FeatureValue:
    event_time = datetime(2024, 1, day, tzinfo=UTC)
    defaults: dict[str, object] = {
        "symbol": "AAPL",
        "feature_name": "sma_20",
        "feature_version": 1,
        "event_time": event_time,
        "value": value,
        "knowledge_time": event_time,
    }
    defaults.update(overrides)
    return FeatureValue(**defaults)  # type: ignore[arg-type]


async def test_upsert_definition_then_get(db_session: AsyncSession) -> None:
    repo = SqlAlchemyFeatureDefinitionRepository(db_session)
    await repo.upsert_definition(_definition())
    await db_session.commit()

    fetched = await repo.get_definition("sma_20", 1)
    assert fetched is not None
    assert fetched.name == "sma_20"
    assert fetched.parameters == {"window": 20}


async def test_upsert_definition_updates_on_conflict(db_session: AsyncSession) -> None:
    repo = SqlAlchemyFeatureDefinitionRepository(db_session)
    await repo.upsert_definition(_definition())
    await db_session.commit()

    updated = FeatureDefinition(
        name="sma_20",
        version=1,
        category=FeatureCategory.TREND,
        description="Updated description",
        parameters={"window": 30},
        min_bars_required=30,
    )
    await repo.upsert_definition(updated)
    await db_session.commit()

    fetched = await repo.get_definition("sma_20", 1)
    assert fetched is not None
    assert fetched.description == "Updated description"
    assert fetched.parameters == {"window": 30}


async def test_get_latest_definition_returns_highest_version(db_session: AsyncSession) -> None:
    repo = SqlAlchemyFeatureDefinitionRepository(db_session)
    await repo.upsert_definition(_definition(version=1))
    await repo.upsert_definition(_definition(version=2))
    await db_session.commit()

    latest = await repo.get_latest_definition("sma_20")
    assert latest is not None
    assert latest.version == 2


async def test_list_definitions_returns_all(db_session: AsyncSession) -> None:
    repo = SqlAlchemyFeatureDefinitionRepository(db_session)
    await repo.upsert_definition(_definition(name="sma_20"))
    await repo.upsert_definition(_definition(name="ema_20"))
    await db_session.commit()

    definitions = await repo.list_definitions()
    names = {d.name for d in definitions}
    assert names == {"sma_20", "ema_20"}


async def test_upsert_values_inserts_new_rows(db_session: AsyncSession) -> None:
    repo = SqlAlchemyFeatureValueRepository(db_session)
    values = [_value(10), _value(11), _value(12)]

    affected = await repo.upsert_values(values)
    await db_session.commit()

    assert affected == 3
    stored = await repo.get_values("AAPL", "sma_20")
    assert len(stored) == 3
    assert stored[0].event_time < stored[1].event_time < stored[2].event_time


async def test_upsert_values_updates_on_conflict(db_session: AsyncSession) -> None:
    repo = SqlAlchemyFeatureValueRepository(db_session)
    await repo.upsert_values([_value(10, value=100.0)])
    await db_session.commit()

    # Re-computing the same (symbol, feature_name, feature_version, event_time)
    # with a revised value must update in place, not duplicate — recomputation
    # (full or incremental) is a normal operation.
    await repo.upsert_values([_value(10, value=111.11)])
    await db_session.commit()

    stored = await repo.get_values("AAPL", "sma_20")
    assert len(stored) == 1
    assert stored[0].value == pytest.approx(111.11)


async def test_get_values_filters_by_date_range(db_session: AsyncSession) -> None:
    repo = SqlAlchemyFeatureValueRepository(db_session)
    await repo.upsert_values([_value(1), _value(15), _value(31)])
    await db_session.commit()

    stored = await repo.get_values("AAPL", "sma_20", start=date(2024, 1, 10), end=date(2024, 1, 20))

    assert len(stored) == 1
    assert stored[0].event_time.day == 15


async def test_get_values_as_of_excludes_later_knowledge_time(db_session: AsyncSession) -> None:
    repo = SqlAlchemyFeatureValueRepository(db_session)
    early = _value(
        10,
        value=100.0,
        knowledge_time=datetime(2024, 1, 10, tzinfo=UTC),
    )
    late = _value(
        11,
        value=200.0,
        knowledge_time=datetime(2024, 2, 1, tzinfo=UTC),
    )
    await repo.upsert_values([early, late])
    await db_session.commit()

    # A point-in-time query as of Jan 15 must never see the Feb 1 value —
    # this is the structural PIT guarantee (docs/claude_ROI.md §17).
    as_of_jan_15 = datetime(2024, 1, 15, tzinfo=UTC)
    stored = await repo.get_values("AAPL", "sma_20", as_of=as_of_jan_15)

    assert len(stored) == 1
    assert stored[0].value == pytest.approx(100.0)


async def test_get_latest_event_time_returns_max(db_session: AsyncSession) -> None:
    repo = SqlAlchemyFeatureValueRepository(db_session)
    await repo.upsert_values([_value(5), _value(20), _value(10)])
    await db_session.commit()

    latest = await repo.get_latest_event_time("AAPL", "sma_20", 1)
    assert latest is not None
    assert latest.day == 20


async def test_get_latest_event_time_returns_none_when_absent(db_session: AsyncSession) -> None:
    repo = SqlAlchemyFeatureValueRepository(db_session)
    latest = await repo.get_latest_event_time("NOPE", "sma_20", 1)
    assert latest is None


async def test_compute_statistics_aggregates_correctly(db_session: AsyncSession) -> None:
    repo = SqlAlchemyFeatureValueRepository(db_session)
    await repo.upsert_values([_value(1, value=10.0), _value(2, value=20.0), _value(3, value=30.0)])
    await db_session.commit()

    stats = await repo.compute_statistics("AAPL", "sma_20", 1)

    assert stats.count == 3
    assert stats.mean == pytest.approx(20.0)
    assert stats.minimum == pytest.approx(10.0)
    assert stats.maximum == pytest.approx(30.0)


async def test_compute_statistics_with_no_data_returns_none_fields(
    db_session: AsyncSession,
) -> None:
    repo = SqlAlchemyFeatureValueRepository(db_session)
    stats = await repo.compute_statistics("NOPE", "sma_20", 1)

    assert stats.count == 0
    assert stats.mean is None
    assert stats.minimum is None
    assert stats.maximum is None


async def test_computation_run_lifecycle(db_session: AsyncSession) -> None:
    repo = SqlAlchemyFeatureComputationRunRepository(db_session)
    run = FeatureComputationRun(
        symbol="AAPL",
        mode=ComputationMode.FULL,
        status=ComputationStatus.RUNNING,
        started_at=datetime.now(UTC),
    )

    created = await repo.create_run(run)
    await db_session.commit()
    assert created.id is not None

    completed = replace(
        created,
        status=ComputationStatus.SUCCEEDED,
        bars_read=100,
        features_computed=50,
        features_persisted=48,
        features_rejected=2,
        rejection_reasons=["bad value"],
        completed_at=datetime.now(UTC),
    )
    await repo.complete_run(completed)
    await db_session.commit()

    fetched = await repo.get_run(created.id)
    assert fetched is not None
    assert fetched.status == ComputationStatus.SUCCEEDED
    assert fetched.bars_read == 100
    assert fetched.features_persisted == 48
    assert fetched.rejection_reasons == ["bad value"]


async def test_list_runs_filters_by_symbol_and_orders_recent_first(
    db_session: AsyncSession,
) -> None:
    repo = SqlAlchemyFeatureComputationRunRepository(db_session)
    await repo.create_run(
        FeatureComputationRun(
            symbol="AAPL",
            mode=ComputationMode.FULL,
            status=ComputationStatus.SUCCEEDED,
            started_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
    )
    await repo.create_run(
        FeatureComputationRun(
            symbol="AAPL",
            mode=ComputationMode.INCREMENTAL,
            status=ComputationStatus.SUCCEEDED,
            started_at=datetime(2024, 1, 2, tzinfo=UTC),
        )
    )
    await repo.create_run(
        FeatureComputationRun(
            symbol="MSFT",
            mode=ComputationMode.FULL,
            status=ComputationStatus.SUCCEEDED,
            started_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
    )
    await db_session.commit()

    aapl_runs = await repo.list_runs(symbol="AAPL")
    assert len(aapl_runs) == 2
    assert aapl_runs[0].started_at > aapl_runs[1].started_at
