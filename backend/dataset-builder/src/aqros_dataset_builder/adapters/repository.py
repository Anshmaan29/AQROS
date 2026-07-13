"""SQLAlchemy implementations of the domain repository ports.

Each repository maps ORM rows to/from the pure domain types in
``domain/models.py``. Business logic (``domain/services.py``) never sees an
ORM row or a SQLAlchemy session — only these ports. Mirrors
``aqros_market_data.adapters.repository`` and
``aqros_feature_store.adapters.repository``.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aqros_dataset_builder.adapters.orm import DatasetBuildRunORM, DatasetDefinitionORM
from aqros_dataset_builder.domain.models import (
    BuildStatus,
    DatasetBuildRun,
    DatasetDefinition,
    DatasetQualityReport,
    ExpandingWindowParams,
    FeatureStatistics,
    LabelBalance,
    LabelType,
    PredictionHorizon,
    PurgedCVParams,
    RollingWindowParams,
    SplitStrategy,
    WalkForwardParams,
)
from aqros_dataset_builder.domain.ports import (
    DatasetBuildRunRepository,
    DatasetDefinitionRepository,
)

_SPLIT_PARAM_TYPES: dict[SplitStrategy, type] = {
    SplitStrategy.WALK_FORWARD: WalkForwardParams,
    SplitStrategy.ROLLING_WINDOW: RollingWindowParams,
    SplitStrategy.EXPANDING_WINDOW: ExpandingWindowParams,
    SplitStrategy.PURGED_CV: PurgedCVParams,
}


def _to_domain_definition(row: DatasetDefinitionORM) -> DatasetDefinition:
    strategy = SplitStrategy(row.split_strategy)
    params_cls = _SPLIT_PARAM_TYPES[strategy]
    params_dict = json.loads(row.split_params_json)
    return DatasetDefinition(
        name=row.name,
        version=row.version,
        symbols=tuple(json.loads(row.symbols_json)),
        feature_names=tuple(json.loads(row.feature_names_json)),
        label_type=LabelType(row.label_type),
        horizon=PredictionHorizon(row.horizon),
        split_strategy=strategy,
        split_params=params_cls(**params_dict),
        start_date=date.fromisoformat(row.start_date),
        end_date=date.fromisoformat(row.end_date),
        created_at=row.created_at,
    )


def _to_domain_quality_report(data: dict[str, object]) -> DatasetQualityReport:
    feature_stats_data = cast(list[dict[str, object]], data.pop("feature_statistics", []))
    feature_statistics = tuple(FeatureStatistics(**fs) for fs in feature_stats_data)  # type: ignore[arg-type]
    return DatasetQualityReport(
        total_rows=cast(int, data["total_rows"]),
        duplicate_row_count=cast(int, data["duplicate_row_count"]),
        missing_value_counts=cast(dict[str, int], data["missing_value_counts"]),
        class_balance=cast(dict[str, float], data["class_balance"]),
        feature_statistics=feature_statistics,
        validation_passed=cast(bool, data["validation_passed"]),
        validation_findings=cast(list[str], data["validation_findings"]),
    )


def _to_domain_run(row: DatasetBuildRunORM) -> DatasetBuildRun:
    reasons = row.rejection_reasons_text.split("\n") if row.rejection_reasons_text else []
    findings = (
        row.leakage_audit_findings_text.split("\n") if row.leakage_audit_findings_text else []
    )
    label_balance = None
    if row.label_balance_json:
        data = json.loads(row.label_balance_json)
        label_balance = LabelBalance(**data)
    row_counts = json.loads(row.row_counts_by_role_json) if row.row_counts_by_role_json else {}
    quality_report = None
    if row.quality_report_json:
        quality_report = _to_domain_quality_report(json.loads(row.quality_report_json))
    return DatasetBuildRun(
        id=row.id,
        dataset_name=row.dataset_name,
        dataset_version=row.dataset_version,
        status=BuildStatus(row.status),
        started_at=row.started_at,
        completed_at=row.completed_at,
        bars_read=row.bars_read,
        rows_generated=row.rows_generated,
        rows_rejected=row.rows_rejected,
        rejection_reasons=reasons,
        leakage_audit_passed=row.leakage_audit_passed,
        leakage_audit_findings=findings,
        label_balance=label_balance,
        row_counts_by_role=row_counts,
        quality_report=quality_report,
        parquet_path=row.parquet_path,
        manifest_path=row.manifest_path,
        error_message=row.error_message,
    )


def _quality_report_to_json(report: DatasetQualityReport | None) -> str | None:
    if report is None:
        return None
    payload = {
        "total_rows": report.total_rows,
        "duplicate_row_count": report.duplicate_row_count,
        "missing_value_counts": report.missing_value_counts,
        "class_balance": report.class_balance,
        "feature_statistics": [asdict(fs) for fs in report.feature_statistics],
        "validation_passed": report.validation_passed,
        "validation_findings": report.validation_findings,
    }
    return json.dumps(payload)


class SqlAlchemyDatasetDefinitionRepository(DatasetDefinitionRepository):
    """Postgres-backed dataset definition (catalog) repository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_definition(self, definition: DatasetDefinition) -> DatasetDefinition:
        orm_row = DatasetDefinitionORM(
            name=definition.name,
            version=definition.version,
            symbols_json=json.dumps(list(definition.symbols)),
            feature_names_json=json.dumps(list(definition.feature_names)),
            label_type=definition.label_type.value,
            horizon=definition.horizon.value,
            split_strategy=definition.split_strategy.value,
            split_params_json=json.dumps(asdict(definition.split_params)),
            start_date=definition.start_date.isoformat(),
            end_date=definition.end_date.isoformat(),
            created_at=definition.created_at,
        )
        self._session.add(orm_row)
        await self._session.flush()
        return _to_domain_definition(orm_row)

    async def get_definition(self, name: str, version: int) -> DatasetDefinition | None:
        stmt = select(DatasetDefinitionORM).where(
            DatasetDefinitionORM.name == name, DatasetDefinitionORM.version == version
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_domain_definition(row) if row is not None else None

    async def get_latest_definition(self, name: str) -> DatasetDefinition | None:
        stmt = (
            select(DatasetDefinitionORM)
            .where(DatasetDefinitionORM.name == name)
            .order_by(DatasetDefinitionORM.version.desc())
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_domain_definition(row) if row is not None else None

    async def list_definitions(self) -> list[DatasetDefinition]:
        stmt = select(DatasetDefinitionORM).order_by(
            DatasetDefinitionORM.name.asc(), DatasetDefinitionORM.version.asc()
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_domain_definition(row) for row in rows]


class SqlAlchemyDatasetBuildRunRepository(DatasetBuildRunRepository):
    """Postgres-backed build-run audit trail repository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_run(self, run: DatasetBuildRun) -> DatasetBuildRun:
        orm_row = DatasetBuildRunORM(
            dataset_name=run.dataset_name,
            dataset_version=run.dataset_version,
            status=run.status.value,
            started_at=run.started_at,
            completed_at=run.completed_at,
            bars_read=run.bars_read,
            rows_generated=run.rows_generated,
            rows_rejected=run.rows_rejected,
            rejection_reasons_text="\n".join(run.rejection_reasons),
            leakage_audit_passed=run.leakage_audit_passed,
            leakage_audit_findings_text="\n".join(run.leakage_audit_findings),
            label_balance_json=json.dumps(asdict(run.label_balance)) if run.label_balance else None,
            row_counts_by_role_json=(
                json.dumps(run.row_counts_by_role) if run.row_counts_by_role else None
            ),
            quality_report_json=_quality_report_to_json(run.quality_report),
            parquet_path=run.parquet_path,
            manifest_path=run.manifest_path,
            error_message=run.error_message,
        )
        self._session.add(orm_row)
        await self._session.flush()
        return _to_domain_run(orm_row)

    async def complete_run(self, run: DatasetBuildRun) -> None:
        if run.id is None:
            raise ValueError("Cannot complete a run that has no id (call create_run first)")
        stmt = select(DatasetBuildRunORM).where(DatasetBuildRunORM.id == run.id)
        orm_row = (await self._session.execute(stmt)).scalar_one()
        orm_row.status = run.status.value
        orm_row.completed_at = run.completed_at
        orm_row.bars_read = run.bars_read
        orm_row.rows_generated = run.rows_generated
        orm_row.rows_rejected = run.rows_rejected
        orm_row.rejection_reasons_text = "\n".join(run.rejection_reasons)
        orm_row.leakage_audit_passed = run.leakage_audit_passed
        orm_row.leakage_audit_findings_text = "\n".join(run.leakage_audit_findings)
        orm_row.label_balance_json = (
            json.dumps(asdict(run.label_balance)) if run.label_balance else None
        )
        orm_row.row_counts_by_role_json = (
            json.dumps(run.row_counts_by_role) if run.row_counts_by_role else None
        )
        orm_row.quality_report_json = _quality_report_to_json(run.quality_report)
        orm_row.parquet_path = run.parquet_path
        orm_row.manifest_path = run.manifest_path
        orm_row.error_message = run.error_message
        await self._session.flush()

    async def get_run(self, run_id: int) -> DatasetBuildRun | None:
        stmt = select(DatasetBuildRunORM).where(DatasetBuildRunORM.id == run_id)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_domain_run(row) if row is not None else None

    async def list_runs(
        self, *, dataset_name: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[DatasetBuildRun]:
        stmt = select(DatasetBuildRunORM)
        if dataset_name is not None:
            stmt = stmt.where(DatasetBuildRunORM.dataset_name == dataset_name)
        stmt = stmt.order_by(DatasetBuildRunORM.started_at.desc()).limit(limit).offset(offset)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_domain_run(row) for row in rows]
