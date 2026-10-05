from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aqros_paper_trading.adapters.orm import MarketDataSnapshotORM, PaperFillORM, PaperOrderORM
from aqros_paper_trading.domain.models import (
    FillResult,
    OrderSide,
    OrderStatus,
    OrderType,
    RejectReason,
    SimulatedOrder,
)


class PaperOrderRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save(self, order: SimulatedOrder) -> str:
        async with self._session_factory() as session:
            existing = await self._find_orm(session, order.order_id)
            if existing is not None:
                await self._update_orm(session, existing, order)
            else:
                orm = self._to_orm(order)
                session.add(orm)
            await session.flush()
            await session.commit()
            return order.order_id

    async def find_by_id(self, order_id: str) -> SimulatedOrder | None:
        async with self._session_factory() as session:
            orm = await self._find_orm(session, order_id)
            if orm is None:
                return None
            return await self._to_domain(session, orm)

    async def find_by_client_order_id(self, client_order_id: str) -> SimulatedOrder | None:
        async with self._session_factory() as session:
            stmt = select(PaperOrderORM).where(PaperOrderORM.client_order_id == client_order_id)
            result = await session.execute(stmt)
            orm = result.scalar_one_or_none()
            if orm is None:
                return None
            return await self._to_domain(session, orm)

    async def find_by_portfolio(
        self, portfolio_id: str, status: OrderStatus | None = None, limit: int = 100
    ) -> list[SimulatedOrder]:
        async with self._session_factory() as session:
            stmt = select(PaperOrderORM).where(PaperOrderORM.portfolio_id == portfolio_id)
            if status is not None:
                stmt = stmt.where(PaperOrderORM.status == status.value)
            stmt = stmt.order_by(PaperOrderORM.created_at.desc()).limit(limit)
            result = await session.execute(stmt)
            orms = result.scalars().all()
            orders = []
            for orm in orms:
                order = await self._to_domain(session, orm)
                if order is not None:
                    orders.append(order)
            return orders

    async def find_by_status(self, status: OrderStatus, limit: int = 100) -> list[SimulatedOrder]:
        async with self._session_factory() as session:
            stmt = select(PaperOrderORM).where(PaperOrderORM.status == status.value).limit(limit)
            result = await session.execute(stmt)
            orms = result.scalars().all()
            orders = []
            for orm in orms:
                order = await self._to_domain(session, orm)
                if order is not None:
                    orders.append(order)
            return orders

    async def delete_by_id(self, order_id: str) -> None:
        async with self._session_factory() as session:
            await session.execute(delete(PaperFillORM).where(PaperFillORM.order_id == order_id))
            await session.execute(delete(PaperOrderORM).where(PaperOrderORM.order_id == order_id))
            await session.commit()

    async def save_fill(self, result: FillResult) -> str:
        async with self._session_factory() as session:
            orm = PaperFillORM(
                trade_id=result.trade_id,
                order_id=result.order_id,
                symbol=result.symbol,
                side=result.side.value,
                fill_quantity=str(result.fill_quantity),
                fill_price=str(result.fill_price),
                commission=str(result.commission),
                slippage=str(result.slippage),
                is_partial=result.is_partial,
                created_at=result.timestamp or datetime.now(),
            )
            session.add(orm)
            await session.flush()
            await session.commit()
            return result.trade_id

    async def find_fills_by_order(self, order_id: str) -> list[FillResult]:
        async with self._session_factory() as session:
            stmt = (
                select(PaperFillORM)
                .where(PaperFillORM.order_id == order_id)
                .order_by(PaperFillORM.created_at.asc())
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [
                FillResult(
                    trade_id=row.trade_id,
                    order_id=row.order_id,
                    symbol=row.symbol,
                    side=OrderSide(row.side),
                    fill_quantity=Decimal(row.fill_quantity),
                    fill_price=Decimal(row.fill_price),
                    commission=Decimal(row.commission),
                    slippage=Decimal(row.slippage),
                    is_partial=row.is_partial,
                    timestamp=row.created_at,
                )
                for row in rows
            ]

    async def _find_orm(self, session: AsyncSession, order_id: str) -> PaperOrderORM | None:
        stmt = select(PaperOrderORM).where(PaperOrderORM.order_id == order_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def _to_domain(self, session: AsyncSession, orm: PaperOrderORM) -> SimulatedOrder | None:
        fills = await self._get_fills_for_order(session, orm.order_id)
        return SimulatedOrder(
            order_id=orm.order_id,
            client_order_id=orm.client_order_id,
            portfolio_id=orm.portfolio_id,
            symbol=orm.symbol,
            side=OrderSide(orm.side),
            order_type=OrderType(orm.order_type),
            quantity=Decimal(orm.quantity),
            price=Decimal(orm.price) if orm.price else None,
            stop_price=Decimal(orm.stop_price) if orm.stop_price else None,
            filled_quantity=Decimal(orm.filled_quantity),
            filled_value=Decimal(orm.filled_value),
            total_commission=Decimal(orm.total_commission),
            avg_fill_price=Decimal(orm.avg_fill_price) if orm.avg_fill_price else None,
            last_fill_price=Decimal(orm.last_fill_price) if orm.last_fill_price else None,
            status=OrderStatus(orm.status),
            reject_reason=RejectReason(orm.reject_reason) if orm.reject_reason else None,
            reject_message=orm.reject_message,
            strategy=orm.strategy,
            correlation_id=orm.correlation_id,
            created_at=orm.created_at,
            updated_at=orm.updated_at,
            fills=list(fills),
        )

    async def _get_fills_for_order(self, session: AsyncSession, order_id: str) -> list[FillResult]:
        stmt = (
            select(PaperFillORM)
            .where(PaperFillORM.order_id == order_id)
            .order_by(PaperFillORM.created_at.asc())
        )
        result = await session.execute(stmt)
        rows = result.scalars().all()
        return [
            FillResult(
                trade_id=row.trade_id,
                order_id=row.order_id,
                symbol=row.symbol,
                side=OrderSide(row.side),
                fill_quantity=Decimal(row.fill_quantity),
                fill_price=Decimal(row.fill_price),
                commission=Decimal(row.commission),
                slippage=Decimal(row.slippage),
                is_partial=row.is_partial,
                timestamp=row.created_at,
            )
            for row in rows
        ]

    def _to_orm(self, order: SimulatedOrder) -> PaperOrderORM:
        return PaperOrderORM(
            order_id=order.order_id,
            client_order_id=order.client_order_id,
            portfolio_id=order.portfolio_id,
            symbol=order.symbol,
            side=order.side.value,
            order_type=order.order_type.value,
            quantity=str(order.quantity),
            price=str(order.price) if order.price else None,
            stop_price=str(order.stop_price) if order.stop_price else None,
            filled_quantity=str(order.filled_quantity),
            filled_value=str(order.filled_value),
            total_commission=str(order.total_commission),
            avg_fill_price=str(order.avg_fill_price) if order.avg_fill_price else None,
            last_fill_price=str(order.last_fill_price) if order.last_fill_price else None,
            status=order.status.value,
            reject_reason=order.reject_reason.value if order.reject_reason else None,
            reject_message=order.reject_message,
            strategy=order.strategy,
            correlation_id=order.correlation_id,
            created_at=order.created_at or datetime.now(),
            updated_at=order.updated_at or datetime.now(),
        )

    async def _update_orm(
        self, session: AsyncSession, orm: PaperOrderORM, order: SimulatedOrder
    ) -> None:
        orm.status = order.status.value
        orm.filled_quantity = str(order.filled_quantity)
        orm.filled_value = str(order.filled_value)
        orm.total_commission = str(order.total_commission)
        orm.avg_fill_price = str(order.avg_fill_price) if order.avg_fill_price else None
        orm.last_fill_price = str(order.last_fill_price) if order.last_fill_price else None
        orm.reject_reason = order.reject_reason.value if order.reject_reason else None
        orm.reject_message = order.reject_message
        orm.updated_at = datetime.now()
        session.add(orm)


class MarketDataRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save_snapshot(
        self,
        symbol: str,
        bid: Decimal,
        ask: Decimal,
        last: Decimal,
        volume: float,
        bid_size: float,
        ask_size: float,
    ) -> int:
        async with self._session_factory() as session:
            orm = MarketDataSnapshotORM(
                symbol=symbol,
                bid=str(bid),
                ask=str(ask),
                last=str(last),
                volume=volume,
                bid_size=bid_size,
                ask_size=ask_size,
                created_at=datetime.now(),
            )
            session.add(orm)
            await session.flush()
            result_id = orm.id
            await session.commit()
            return result_id
