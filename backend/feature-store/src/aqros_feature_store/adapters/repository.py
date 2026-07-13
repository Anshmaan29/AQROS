"""SQLAlchemy implementations of the domain repository ports.

Each repository maps ORM rows to/from the pure domain types in
``domain/models.py``. Business logic (``domain/services.py``) never sees an
ORM row or a SQLAlchemy session — only these ports. Mirrors
``aqros_market_data.adapters.repository``'s upsert-on-conflict pattern.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime, time
from typing import Any, TypeVar, cast

from sqlalchemy import Select, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from aqros_feature_store.adapters.orm import (
    FeatureComputationRunORM,
    FeatureDefinitionORM,
    FeatureValueORM,
)
from aqros_feature_store.domain.models import (
    ComputationMode,
    ComputationStatus,
    FeatureCategory,
    FeatureComputationRun,
    FeatureDefinition,
    FeatureStatistics,
    FeatureValue,
)
from aqros_feature_store.domain.ports import (
    FeatureComputationRunRepository,
    FeatureDefinitionRepository,
    FeatureValueRepository,
)

_SelectRowT = TypeVar("_SelectRowT", bound=tuple[Any, ...])


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=UTC)
    end = datetime.combine(day, time.max, tzinfo=UTC)
    return start, end


def _to_domain_definition(row: FeatureDefinitionORM) -> FeatureDefinition:
    return FeatureDefinition(
        name=row.name,
        version=row.version,
        category=FeatureCategory(row.category),
        description=row.description,
        parameters=json.loads(row.parameters_json),
        min_bars_required=row.min_bars_required,
    )


def _to_domain_value(row: FeatureValueORM) -> FeatureValue:
    return FeatureValue(
        symbol=row.symbol,
        feature_name=row.feature_name,
        feature_version=row.feature_version,
        event_time=row.event_time,
        value=row.value,
        knowledge_time=row.knowledge_time,
        computation_run_id=row.computation_run_id,
    )


def _to_domain_run(row: FeatureComputationRunORM) -> FeatureComputationRun:
    reasons = row.rejection_reasons_text.split("\n") if row.rejection_reasons_text else []
    return FeatureComputationRun(
        id=row.id,
        symbol=row.symbol,
        mode=ComputationMode(row.mode),
        status=ComputationStatus(row.status),
        started_at=row.started_at,
        completed_at=row.completed_at,
        bars_read=row.bars_read,
        features_computed=row.features_computed,
        features_persisted=row.features_persisted,
        features_rejected=row.features_rejected,
        rejection_reasons=reasons,
        error_message=row.error_message,
    )


class SqlAlchemyFeatureDefinitionRepository(FeatureDefinitionRepository):
    """Postgres-backed feature definition (catalog) repository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_definition(self, definition: FeatureDefinition) -> None:
        now = datetime.now(UTC)
        stmt = pg_insert(FeatureDefinitionORM).values(
            name=definition.name,
            version=definition.version,
            category=definition.category.value,
            description=definition.description,
            parameters_json=json.dumps(definition.parameters),
            min_bars_required=definition.min_bars_required,
            created_at=now,
            updated_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_feature_definitions_name_version",
            set_={
                "category": stmt.excluded.category,
                "description": stmt.excluded.description,
                "parameters_json": stmt.excluded.parameters_json,
                "min_bars_required": stmt.excluded.min_bars_required,
                "updated_at": now,
            },
        )
        await self._session.execute(stmt)

    async def get_definition(self, name: str, version: int) -> FeatureDefinition | None:
        stmt = select(FeatureDefinitionORM).where(
            FeatureDefinitionORM.name == name, FeatureDefinitionORM.version == version
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_domain_definition(row) if row is not None else None

    async def get_latest_definition(self, name: str) -> FeatureDefinition | None:
        stmt = (
            select(FeatureDefinitionORM)
            .where(FeatureDefinitionORM.name == name)
            .order_by(FeatureDefinitionORM.version.desc())
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_domain_definition(row) if row is not None else None

    async def list_definitions(self) -> list[FeatureDefinition]:
        stmt = select(FeatureDefinitionORM).order_by(
            FeatureDefinitionORM.name.asc(), FeatureDefinitionORM.version.asc()
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_domain_definition(row) for row in rows]


class SqlAlchemyFeatureValueRepository(FeatureValueRepository):
    """Postgres-backed feature value repository using an upsert on the identity key."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_values(self, values: list[FeatureValue]) -> int:
        if not values:
            return 0

        rows = [
            {
                "symbol": v.symbol,
                "feature_name": v.feature_name,
                "feature_version": v.feature_version,
                "event_time": v.event_time,
                "value": v.value,
                "knowledge_time": v.knowledge_time,
                "computation_run_id": v.computation_run_id,
            }
            for v in values
        ]

        stmt = pg_insert(FeatureValueORM).values(rows)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_feature_values_identity",
            set_={
                "value": stmt.excluded.value,
                "knowledge_time": stmt.excluded.knowledge_time,
                "computation_run_id": stmt.excluded.computation_run_id,
            },
        )
        result = await self._session.execute(stmt)
        rowcount: int = cast(CursorResult[object], result).rowcount
        return rowcount or 0

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
        stmt = select(FeatureValueORM).where(
            FeatureValueORM.symbol == symbol.upper(),
            FeatureValueORM.feature_name == feature_name,
        )
        stmt = self._apply_filters(stmt, feature_version, start, end, as_of)
        stmt = stmt.order_by(FeatureValueORM.event_time.asc()).limit(limit).offset(offset)

        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_domain_value(row) for row in rows]

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
        stmt = (
            select(func.count())
            .select_from(FeatureValueORM)
            .where(
                FeatureValueORM.symbol == symbol.upper(),
                FeatureValueORM.feature_name == feature_name,
            )
        )
        stmt = self._apply_filters(stmt, feature_version, start, end, as_of)
        return (await self._session.execute(stmt)).scalar_one()

    async def get_latest_event_time(
        self, symbol: str, feature_name: str, feature_version: int
    ) -> datetime | None:
        stmt = select(func.max(FeatureValueORM.event_time)).where(
            FeatureValueORM.symbol == symbol.upper(),
            FeatureValueORM.feature_name == feature_name,
            FeatureValueORM.feature_version == feature_version,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def compute_statistics(
        self,
        symbol: str,
        feature_name: str,
        feature_version: int,
        *,
        start: date | None = None,
        end: date | None = None,
    ) -> FeatureStatistics:
        stmt = select(
            func.count(FeatureValueORM.id),
            func.avg(FeatureValueORM.value),
            func.stddev_pop(FeatureValueORM.value),
            func.min(FeatureValueORM.value),
            func.max(FeatureValueORM.value),
        ).where(
            FeatureValueORM.symbol == symbol.upper(),
            FeatureValueORM.feature_name == feature_name,
            FeatureValueORM.feature_version == feature_version,
        )
        if start is not None:
            stmt = stmt.where(FeatureValueORM.event_time >= _day_bounds(start)[0])
        if end is not None:
            stmt = stmt.where(FeatureValueORM.event_time <= _day_bounds(end)[1])

        count, mean, std, minimum, maximum = (await self._session.execute(stmt)).one()
        return FeatureStatistics(
            symbol=symbol.upper(),
            feature_name=feature_name,
            feature_version=feature_version,
            count=count,
            mean=float(mean) if mean is not None and not math.isnan(float(mean)) else None,
            std=float(std) if std is not None and not math.isnan(float(std)) else None,
            minimum=float(minimum) if minimum is not None else None,
            maximum=float(maximum) if maximum is not None else None,
        )

    @staticmethod
    def _apply_filters(
        stmt: Select[_SelectRowT],
        feature_version: int | None,
        start: date | None,
        end: date | None,
        as_of: datetime | None,
    ) -> Select[_SelectRowT]:
        if feature_version is not None:
            stmt = stmt.where(FeatureValueORM.feature_version == feature_version)
        if start is not None:
            stmt = stmt.where(FeatureValueORM.event_time >= _day_bounds(start)[0])
        if end is not None:
            stmt = stmt.where(FeatureValueORM.event_time <= _day_bounds(end)[1])
        if as_of is not None:
            # The point-in-time gate (docs/claude_ROI.md §17): only values
            # knowable as of this instant are visible to the query.
            stmt = stmt.where(FeatureValueORM.knowledge_time <= as_of)
        return stmt


class SqlAlchemyFeatureComputationRunRepository(FeatureComputationRunRepository):
    """Postgres-backed computation-run audit trail repository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_run(self, run: FeatureComputationRun) -> FeatureComputationRun:
        orm_row = FeatureComputationRunORM(
            symbol=run.symbol,
            mode=run.mode.value,
            status=run.status.value,
            started_at=run.started_at,
            completed_at=run.completed_at,
            bars_read=run.bars_read,
            features_computed=run.features_computed,
            features_persisted=run.features_persisted,
            features_rejected=run.features_rejected,
            rejection_reasons_text="\n".join(run.rejection_reasons),
            error_message=run.error_message,
        )
        self._session.add(orm_row)
        await self._session.flush()
        return _to_domain_run(orm_row)

    async def complete_run(self, run: FeatureComputationRun) -> None:
        if run.id is None:
            raise ValueError("Cannot complete a run that has no id (call create_run first)")
        stmt = select(FeatureComputationRunORM).where(FeatureComputationRunORM.id == run.id)
        orm_row = (await self._session.execute(stmt)).scalar_one()
        orm_row.status = run.status.value
        orm_row.completed_at = run.completed_at
        orm_row.bars_read = run.bars_read
        orm_row.features_computed = run.features_computed
        orm_row.features_persisted = run.features_persisted
        orm_row.features_rejected = run.features_rejected
        orm_row.rejection_reasons_text = "\n".join(run.rejection_reasons)
        orm_row.error_message = run.error_message
        await self._session.flush()

    async def get_run(self, run_id: int) -> FeatureComputationRun | None:
        stmt = select(FeatureComputationRunORM).where(FeatureComputationRunORM.id == run_id)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_domain_run(row) if row is not None else None

    async def list_runs(
        self, *, symbol: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[FeatureComputationRun]:
        stmt = select(FeatureComputationRunORM)
        if symbol is not None:
            stmt = stmt.where(FeatureComputationRunORM.symbol == symbol.upper())
        stmt = stmt.order_by(FeatureComputationRunORM.started_at.desc()).limit(limit).offset(offset)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_domain_run(row) for row in rows]
