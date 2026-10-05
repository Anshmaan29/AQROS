from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aqros_risk_engine.adapters.orm import (
    RiskDecisionORM,
    RiskLimitORM,
)
from aqros_risk_engine.domain.models import (
    RiskDecision,
    RiskLevel,
)


@dataclass
class RiskDecisionRecord:
    signal_id: str
    symbol: str
    side: str
    strategy: str
    decision: RiskDecision
    risk_level: RiskLevel
    reasons: tuple[str, ...]
    message: str
    requested_quantity: Decimal
    approved_quantity: Decimal
    stop_loss: Decimal | None
    confidence: float
    portfolio_equity_at_check: Decimal
    latency_ms: float
    correlation_id: str
    created_at: datetime


class SqlAlchemyRiskDecisionRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save(self, record: RiskDecisionRecord) -> int:
        async with self._session_factory() as session:
            orm = RiskDecisionORM(
                signal_id=record.signal_id,
                symbol=record.symbol,
                side=record.side,
                strategy=record.strategy,
                decision=record.decision.value,
                risk_level=record.risk_level.value,
                reasons_json=json.dumps(list(record.reasons)),
                message=record.message,
                requested_quantity=str(record.requested_quantity),
                approved_quantity=str(record.approved_quantity),
                stop_loss=str(record.stop_loss) if record.stop_loss is not None else None,
                confidence=record.confidence,
                portfolio_equity_at_check=str(record.portfolio_equity_at_check),
                latency_ms=record.latency_ms,
                correlation_id=record.correlation_id,
                created_at=record.created_at,
            )
            session.add(orm)
            await session.flush()
            result_id = orm.id
            await session.commit()
            return result_id

    async def find_by_signal_id(self, signal_id: str) -> RiskDecisionRecord | None:
        async with self._session_factory() as session:
            stmt = select(RiskDecisionORM).where(RiskDecisionORM.signal_id == signal_id)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return self._to_record(row)

    async def list_recent(self, limit: int = 100) -> list[RiskDecisionRecord]:
        async with self._session_factory() as session:
            stmt = select(RiskDecisionORM).order_by(RiskDecisionORM.created_at.desc()).limit(limit)
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [self._to_record(row) for row in rows]

    def _to_record(self, row: RiskDecisionORM) -> RiskDecisionRecord:
        return RiskDecisionRecord(
            signal_id=row.signal_id,
            symbol=row.symbol,
            side=row.side,
            strategy=row.strategy,
            decision=RiskDecision(row.decision),
            risk_level=RiskLevel(row.risk_level),
            reasons=tuple(json.loads(row.reasons_json)),
            message=row.message,
            requested_quantity=Decimal(row.requested_quantity),
            approved_quantity=Decimal(row.approved_quantity),
            stop_loss=Decimal(row.stop_loss) if row.stop_loss else None,
            confidence=row.confidence,
            portfolio_equity_at_check=Decimal(row.portfolio_equity_at_check),
            latency_ms=row.latency_ms,
            correlation_id=row.correlation_id,
            created_at=row.created_at,
        )


class SqlAlchemyRiskLimitRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def upsert_limit(
        self,
        limit_type: str,
        limit_value: float,
        scope: str = "global",
        scope_ref: str | None = None,
        created_by: str = "system",
        is_kernel: bool = True,
    ) -> int:
        async with self._session_factory() as session:
            orm = RiskLimitORM(
                scope=scope,
                scope_ref=scope_ref,
                limit_type=limit_type,
                limit_value=limit_value,
                is_kernel=is_kernel,
                created_by=created_by,
                approved_by=None,
                created_at=datetime.utcnow(),
            )
            session.add(orm)
            await session.flush()
            result_id = orm.id
            await session.commit()
            return result_id

    async def get_limits(self, scope: str = "global") -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            stmt = select(RiskLimitORM).where(RiskLimitORM.scope == scope)
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [
                {
                    "id": row.id,
                    "limit_type": row.limit_type,
                    "limit_value": row.limit_value,
                    "is_kernel": row.is_kernel,
                    "scope": row.scope,
                    "scope_ref": row.scope_ref,
                    "created_by": row.created_by,
                    "approved_by": row.approved_by,
                    "created_at": row.created_at.isoformat(),
                }
                for row in rows
            ]
