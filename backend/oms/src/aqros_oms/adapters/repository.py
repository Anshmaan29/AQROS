from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aqros_oms.adapters.orm import FillORM, OrderORM
from aqros_oms.domain.models import (
    Fill,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    RejectReason,
    TimeInForce,
)


class OrderRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save(self, order: Order) -> str:
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

    async def find_by_id(self, order_id: str) -> Order | None:
        async with self._session_factory() as session:
            orm = await self._find_orm(session, order_id)
            if orm is None:
                return None
            return await self._to_domain(session, orm)

    async def find_by_client_order_id(self, client_order_id: str) -> Order | None:
        async with self._session_factory() as session:
            stmt = select(OrderORM).where(OrderORM.client_order_id == client_order_id)
            result = await session.execute(stmt)
            orm = result.scalar_one_or_none()
            if orm is None:
                return None
            return await self._to_domain(session, orm)

    async def find_by_portfolio(
        self, portfolio_id: str, status: OrderStatus | None = None, limit: int = 100
    ) -> list[Order]:
        async with self._session_factory() as session:
            stmt = select(OrderORM).where(OrderORM.portfolio_id == portfolio_id)
            if status is not None:
                stmt = stmt.where(OrderORM.status == status.value)
            stmt = stmt.order_by(OrderORM.created_at.desc()).limit(limit)
            result = await session.execute(stmt)
            orms = result.scalars().all()
            orders = []
            for orm in orms:
                order = await self._to_domain(session, orm)
                if order is not None:
                    orders.append(order)
            return orders

    async def find_open_orders(
        self, portfolio_id: str | None = None, limit: int = 100
    ) -> list[Order]:
        async with self._session_factory() as session:
            stmt = select(OrderORM).where(
                OrderORM.status.in_([OrderStatus.OPEN.value, OrderStatus.PARTIALLY_FILLED.value])
            )
            if portfolio_id is not None:
                stmt = stmt.where(OrderORM.portfolio_id == portfolio_id)
            stmt = stmt.order_by(OrderORM.created_at.asc()).limit(limit)
            result = await session.execute(stmt)
            orms = result.scalars().all()
            orders = []
            for orm in orms:
                order = await self._to_domain(session, orm)
                if order is not None:
                    orders.append(order)
            return orders

    async def find_expired_orders(self, now: datetime, limit: int = 100) -> list[Order]:
        async with self._session_factory() as session:
            stmt = (
                select(OrderORM)
                .where(
                    and_(
                        OrderORM.status.in_(
                            [OrderStatus.OPEN.value, OrderStatus.PARTIALLY_FILLED.value]
                        ),
                        OrderORM.expires_at.isnot(None),
                        OrderORM.expires_at <= now,
                    )
                )
                .limit(limit)
            )
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
            stmt = delete(FillORM).where(FillORM.order_id == order_id)
            await session.execute(stmt)
            stmt = delete(OrderORM).where(OrderORM.order_id == order_id)
            await session.execute(stmt)
            await session.commit()

    async def save_fill(self, fill: Fill) -> str:
        async with self._session_factory() as session:
            orm = FillORM(
                trade_id=fill.trade_id,
                order_id=fill.order_id,
                quantity=str(fill.quantity),
                price=str(fill.price),
                commission=str(fill.commission),
                created_at=fill.created_at or datetime.now(),
            )
            session.add(orm)
            await session.flush()
            await session.commit()
            return fill.trade_id

    async def find_fills_by_order(self, order_id: str) -> list[Fill]:
        async with self._session_factory() as session:
            stmt = (
                select(FillORM)
                .where(FillORM.order_id == order_id)
                .order_by(FillORM.created_at.asc())
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [
                Fill(
                    trade_id=row.trade_id,
                    order_id=row.order_id,
                    quantity=Decimal(row.quantity),
                    price=Decimal(row.price),
                    commission=Decimal(row.commission),
                    created_at=row.created_at,
                )
                for row in rows
            ]

    async def _find_orm(self, session: AsyncSession, order_id: str) -> OrderORM | None:
        stmt = select(OrderORM).where(OrderORM.order_id == order_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def _to_domain(self, session: AsyncSession, orm: OrderORM) -> Order | None:
        fills = await self._get_fills_for_order(session, orm.order_id)
        return Order(
            order_id=orm.order_id,
            client_order_id=orm.client_order_id,
            portfolio_id=orm.portfolio_id,
            symbol=orm.symbol,
            side=OrderSide(orm.side),
            order_type=OrderType(orm.order_type),
            quantity=Decimal(orm.quantity),
            price=Decimal(orm.price) if orm.price else None,
            stop_price=Decimal(orm.stop_price) if orm.stop_price else None,
            time_in_force=TimeInForce(orm.time_in_force),
            status=OrderStatus(orm.status),
            filled_quantity=Decimal(orm.filled_quantity),
            filled_value=Decimal(orm.filled_value),
            total_commission=Decimal(orm.total_commission),
            avg_fill_price=Decimal(orm.avg_fill_price) if orm.avg_fill_price else None,
            last_fill_price=Decimal(orm.last_fill_price) if orm.last_fill_price else None,
            reject_reason=RejectReason(orm.reject_reason) if orm.reject_reason else None,
            reject_message=orm.reject_message,
            expires_at=orm.expires_at,
            strategy=orm.strategy,
            correlation_id=orm.correlation_id,
            created_at=orm.created_at,
            updated_at=orm.updated_at,
            fills=list(fills),
        )

    async def _get_fills_for_order(self, session: AsyncSession, order_id: str) -> list[Fill]:
        stmt = (
            select(FillORM).where(FillORM.order_id == order_id).order_by(FillORM.created_at.asc())
        )
        result = await session.execute(stmt)
        rows = result.scalars().all()
        return [
            Fill(
                trade_id=row.trade_id,
                order_id=row.order_id,
                quantity=Decimal(row.quantity),
                price=Decimal(row.price),
                commission=Decimal(row.commission),
                created_at=row.created_at,
            )
            for row in rows
        ]

    def _to_orm(self, order: Order) -> OrderORM:
        return OrderORM(
            order_id=order.order_id,
            client_order_id=order.client_order_id,
            portfolio_id=order.portfolio_id,
            symbol=order.symbol,
            side=order.side.value,
            order_type=order.order_type.value,
            quantity=str(order.quantity),
            price=str(order.price) if order.price else None,
            stop_price=str(order.stop_price) if order.stop_price else None,
            time_in_force=order.time_in_force.value,
            status=order.status.value,
            filled_quantity=str(order.filled_quantity),
            filled_value=str(order.filled_value),
            total_commission=str(order.total_commission),
            avg_fill_price=str(order.avg_fill_price) if order.avg_fill_price else None,
            last_fill_price=str(order.last_fill_price) if order.last_fill_price else None,
            reject_reason=order.reject_reason.value if order.reject_reason else None,
            reject_message=order.reject_message,
            expires_at=order.expires_at,
            strategy=order.strategy,
            correlation_id=order.correlation_id,
            created_at=order.created_at or datetime.now(),
            updated_at=order.updated_at or datetime.now(),
        )

    async def _update_orm(self, session: AsyncSession, orm: OrderORM, order: Order) -> None:
        orm.status = order.status.value
        orm.filled_quantity = str(order.filled_quantity)
        orm.filled_value = str(order.filled_value)
        orm.total_commission = str(order.total_commission)
        orm.avg_fill_price = str(order.avg_fill_price) if order.avg_fill_price else None
        orm.last_fill_price = str(order.last_fill_price) if order.last_fill_price else None
        orm.reject_reason = order.reject_reason.value if order.reject_reason else None
        orm.reject_message = order.reject_message
        orm.expires_at = order.expires_at
        orm.updated_at = datetime.now()
        session.add(orm)
