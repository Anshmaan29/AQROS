"""Integration tests for the SQLAlchemy repositories against a real Postgres."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from aqros_dataset_builder.adapters.repository import (
    SqlAlchemyDatasetBuildRunRepository,
    SqlAlchemyDatasetDefinitionRepository,
)
from aqros_dataset_builder.domain.models import (
    BuildStatus,
    DatasetBuildRun,
    DatasetDefinition,
    DatasetQualityReport,
    FeatureStatistics,
    LabelBalance,
    LabelType,
    PredictionHorizon,
    SplitStrategy,
    WalkForwardParams,
)

pytestmark = pytest.mark.integration


def _definition(name: str = "aapl_test", version: int = 1) -> DatasetDefinition:
    return DatasetDefinition(
        name=name,
        version=version,
        symbols=("AAPL", "MSFT"),
        feature_names=("sma_20", "rsi_14"),
        label_type=LabelType.BINARY_DIRECTION,
        horizon=PredictionHorizon.FIVE_DAY,
        split_strategy=SplitStrategy.WALK_FORWARD,
        split_params=WalkForwardParams(
            train_size=100, validation_size=20, test_size=20, step_size=20
        ),
        start_date=date(2023, 1, 1),
        end_date=date(2024, 1, 1),
        created_at=datetime.now(UTC),
    )


def _quality_report() -> DatasetQualityReport:
    return DatasetQualityReport(
        total_rows=100,
        duplicate_row_count=0,
        missing_value_counts={"sma_20": 2},
        class_balance={"train_positive_fraction": 0.55},
        feature_statistics=(
            FeatureStatistics(
                feature_name="sma_20",
                count=98,
                missing_count=2,
                mean=150.0,
                std=5.0,
                minimum=140.0,
                maximum=160.0,
            ),
        ),
        validation_passed=True,
        validation_findings=[],
    )


async def test_create_and_get_definition(db_session: AsyncSession) -> None:
    repo = SqlAlchemyDatasetDefinitionRepository(db_session)
    created = await repo.create_definition(_definition())
    await db_session.commit()

    fetched = await repo.get_definition("aapl_test", 1)
    assert fetched is not None
    assert fetched.name == "aapl_test"
    assert fetched.symbols == ("AAPL", "MSFT")
    assert fetched.split_params == created.split_params


async def test_get_latest_definition_returns_highest_version(db_session: AsyncSession) -> None:
    repo = SqlAlchemyDatasetDefinitionRepository(db_session)
    await repo.create_definition(_definition(version=1))
    await repo.create_definition(_definition(version=2))
    await db_session.commit()

    latest = await repo.get_latest_definition("aapl_test")
    assert latest is not None
    assert latest.version == 2


async def test_list_definitions_returns_all(db_session: AsyncSession) -> None:
    repo = SqlAlchemyDatasetDefinitionRepository(db_session)
    await repo.create_definition(_definition(name="aapl_test"))
    await repo.create_definition(_definition(name="msft_test"))
    await db_session.commit()

    definitions = await repo.list_definitions()
    names = {d.name for d in definitions}
    assert names == {"aapl_test", "msft_test"}


async def test_get_unknown_definition_returns_none(db_session: AsyncSession) -> None:
    repo = SqlAlchemyDatasetDefinitionRepository(db_session)
    assert await repo.get_definition("nope", 1) is None


async def test_build_run_lifecycle_with_quality_report_and_manifest(
    db_session: AsyncSession,
) -> None:
    repo = SqlAlchemyDatasetBuildRunRepository(db_session)
    run = DatasetBuildRun(
        dataset_name="aapl_test",
        dataset_version=1,
        status=BuildStatus.RUNNING,
        started_at=datetime.now(UTC),
    )

    created = await repo.create_run(run)
    await db_session.commit()
    assert created.id is not None

    completed = replace(
        created,
        status=BuildStatus.SUCCEEDED,
        bars_read=200,
        rows_generated=150,
        rows_rejected=10,
        rejection_reasons=["MSFT: no bars"],
        leakage_audit_passed=True,
        leakage_audit_findings=[],
        label_balance=LabelBalance(count=150, mean=0.5, std=0.5, minimum=0.0, maximum=1.0),
        row_counts_by_role={"train": 100, "validation": 25, "test": 25},
        quality_report=_quality_report(),
        parquet_path="/data/aapl_test/v1/run_1.parquet",
        manifest_path="/data/aapl_test/v1/run_1.manifest.json",
        completed_at=datetime.now(UTC),
    )
    await repo.complete_run(completed)
    await db_session.commit()

    fetched = await repo.get_run(created.id)
    assert fetched is not None
    assert fetched.status == BuildStatus.SUCCEEDED
    assert fetched.bars_read == 200
    assert fetched.rows_generated == 150
    assert fetched.leakage_audit_passed is True
    assert fetched.label_balance is not None
    assert fetched.label_balance.mean == pytest.approx(0.5)
    assert fetched.row_counts_by_role == {"train": 100, "validation": 25, "test": 25}
    assert fetched.quality_report is not None
    assert fetched.quality_report.total_rows == 100
    assert fetched.quality_report.duplicate_row_count == 0
    assert fetched.quality_report.missing_value_counts == {"sma_20": 2}
    assert len(fetched.quality_report.feature_statistics) == 1
    assert fetched.quality_report.feature_statistics[0].feature_name == "sma_20"
    assert fetched.manifest_path == "/data/aapl_test/v1/run_1.manifest.json"


async def test_list_runs_filters_by_dataset_name_and_orders_recent_first(
    db_session: AsyncSession,
) -> None:
    repo = SqlAlchemyDatasetBuildRunRepository(db_session)
    await repo.create_run(
        DatasetBuildRun(
            dataset_name="aapl_test",
            dataset_version=1,
            status=BuildStatus.SUCCEEDED,
            started_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
    )
    await repo.create_run(
        DatasetBuildRun(
            dataset_name="aapl_test",
            dataset_version=1,
            status=BuildStatus.SUCCEEDED,
            started_at=datetime(2024, 1, 2, tzinfo=UTC),
        )
    )
    await repo.create_run(
        DatasetBuildRun(
            dataset_name="msft_test",
            dataset_version=1,
            status=BuildStatus.SUCCEEDED,
            started_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
    )
    await db_session.commit()

    aapl_runs = await repo.list_runs(dataset_name="aapl_test")
    assert len(aapl_runs) == 2
    assert aapl_runs[0].started_at > aapl_runs[1].started_at


async def test_get_unknown_run_returns_none(db_session: AsyncSession) -> None:
    repo = SqlAlchemyDatasetBuildRunRepository(db_session)
    assert await repo.get_run(999999) is None
