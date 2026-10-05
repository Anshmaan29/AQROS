from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aqros_live_trading.adapters.orm import BrokerConnectionORM, LiveOrderORM, PositionSyncORM
from aqros_live_trading.domain.models import (
    BrokerPosition,
    ConnectionHealth,
    ConnectionStatus,
    KillSwitch,
    KillSwitchStatus,
    LiveOrder,
    OrderRouteStatus,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)


class LiveOrderRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save(self, order: LiveOrder) -> str:
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

    async def find_by_id(self, order_id: str) -> LiveOrder | None:
        async with self._session_factory() as session:
            orm = await self._find_orm(session, order_id)
            if orm is None:
                return None
            return self._to_domain(orm)

    async def find_by_client_order_id(self, client_order_id: str) -> LiveOrder | None:
        async with self._session_factory() as session:
            stmt = select(LiveOrderORM).where(LiveOrderORM.client_order_id == client_order_id)
            result = await session.execute(stmt)
            orm = result.scalar_one_or_none()
            if orm is None:
                return None
            return self._to_domain(orm)

    async def find_by_portfolio(
        self, portfolio_id: str, status: OrderStatus | None = None, limit: int = 100
    ) -> list[LiveOrder]:
        async with self._session_factory() as session:
            stmt = select(LiveOrderORM).where(LiveOrderORM.portfolio_id == portfolio_id)
            if status is not None:
                stmt = stmt.where(LiveOrderORM.status == status.value)
            stmt = stmt.order_by(LiveOrderORM.created_at.desc()).limit(limit)
            result = await session.execute(stmt)
            orms = result.scalars().all()
            return [self._to_domain(o) for o in orms]

    async def find_open_orders(
        self, portfolio_id: str | None = None, limit: int = 100
    ) -> list[LiveOrder]:
        async with self._session_factory() as session:
            stmt = select(LiveOrderORM).where(
                LiveOrderORM.status.in_(
                    [OrderStatus.OPEN.value, OrderStatus.PARTIALLY_FILLED.value]
                )
            )
            if portfolio_id is not None:
                stmt = stmt.where(LiveOrderORM.portfolio_id == portfolio_id)
            stmt = stmt.order_by(LiveOrderORM.created_at.asc()).limit(limit)
            result = await session.execute(stmt)
            orms = result.scalars().all()
            return [self._to_domain(o) for o in orms]

    async def delete_by_id(self, order_id: str) -> None:
        async with self._session_factory() as session:
            stmt = delete(LiveOrderORM).where(LiveOrderORM.order_id == order_id)
            await session.execute(stmt)
            await session.commit()

    async def _find_orm(self, session: AsyncSession, order_id: str) -> LiveOrderORM | None:
        stmt = select(LiveOrderORM).where(LiveOrderORM.order_id == order_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    def _to_orm(self, order: LiveOrder) -> LiveOrderORM:
        return LiveOrderORM(
            order_id=order.order_id,
            client_order_id=order.client_order_id,
            broker_order_id=order.broker_order_id,
            portfolio_id=order.portfolio_id,
            symbol=order.symbol,
            side=order.side.value,
            order_type=order.order_type.value,
            quantity=str(order.quantity),
            price=str(order.price) if order.price else None,
            stop_price=str(order.stop_price) if order.stop_price else None,
            time_in_force=order.time_in_force.value,
            status=order.status.value,
            route_status=order.route_status.value,
            filled_quantity=str(order.filled_quantity),
            remaining_quantity=str(order.remaining_quantity),
            avg_fill_price=str(order.avg_fill_price) if order.avg_fill_price else None,
            last_fill_price=str(order.last_fill_price) if order.last_fill_price else None,
            total_commission=str(order.total_commission),
            broker_latency_ms=order.broker_latency_ms,
            reject_reason=order.reject_reason,
            reject_message=order.reject_message,
            strategy=order.strategy,
            correlation_id=order.correlation_id,
            routed_to=order.routed_to,
            created_at=order.created_at or datetime.now(),
            updated_at=order.updated_at or datetime.now(),
        )

    def _to_domain(self, orm: LiveOrderORM) -> LiveOrder:
        return LiveOrder(
            order_id=orm.order_id,
            client_order_id=orm.client_order_id,
            broker_order_id=orm.broker_order_id,
            portfolio_id=orm.portfolio_id,
            symbol=orm.symbol,
            side=OrderSide(orm.side),
            order_type=OrderType(orm.order_type),
            quantity=Decimal(orm.quantity),
            price=Decimal(orm.price) if orm.price else None,
            stop_price=Decimal(orm.stop_price) if orm.stop_price else None,
            time_in_force=TimeInForce(orm.time_in_force),
            status=OrderStatus(orm.status),
            route_status=OrderRouteStatus(orm.route_status),
            filled_quantity=Decimal(orm.filled_quantity),
            remaining_quantity=Decimal(orm.remaining_quantity),
            avg_fill_price=Decimal(orm.avg_fill_price) if orm.avg_fill_price else None,
            last_fill_price=Decimal(orm.last_fill_price) if orm.last_fill_price else None,
            total_commission=Decimal(orm.total_commission),
            broker_latency_ms=orm.broker_latency_ms,
            reject_reason=orm.reject_reason,
            reject_message=orm.reject_message,
            strategy=orm.strategy,
            correlation_id=orm.correlation_id,
            routed_to=orm.routed_to,
            created_at=orm.created_at,
            updated_at=orm.updated_at,
        )

    async def _update_orm(self, session: AsyncSession, orm: LiveOrderORM, order: LiveOrder) -> None:
        orm.broker_order_id = order.broker_order_id
        orm.status = order.status.value
        orm.route_status = order.route_status.value
        orm.filled_quantity = str(order.filled_quantity)
        orm.remaining_quantity = str(order.remaining_quantity)
        orm.avg_fill_price = str(order.avg_fill_price) if order.avg_fill_price else None
        orm.last_fill_price = str(order.last_fill_price) if order.last_fill_price else None
        orm.total_commission = str(order.total_commission)
        orm.broker_latency_ms = order.broker_latency_ms
        orm.reject_reason = order.reject_reason
        orm.reject_message = order.reject_message
        orm.routed_to = order.routed_to
        orm.updated_at = datetime.now()
        session.add(orm)


class BrokerConnectionRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save(self, health: ConnectionHealth, kill_switch: KillSwitch) -> None:
        async with self._session_factory() as session:
            stmt = select(BrokerConnectionORM)
            result = await session.execute(stmt)
            orm = result.scalar_one_or_none()
            if orm is None:
                orm = BrokerConnectionORM(
                    broker_name="default",
                    status=health.status.value,
                    last_connected_at=health.last_connected_at,
                    last_disconnected_at=health.last_disconnected_at,
                    last_heartbeat_at=health.last_heartbeat_at,
                    consecutive_failures=health.consecutive_failures,
                    total_disconnections=health.total_disconnections,
                    total_reconnections=health.total_reconnections,
                    kill_switch_status=kill_switch.status.value,
                    kill_switch_triggered_at=kill_switch.triggered_at,
                    kill_switch_triggered_by=kill_switch.triggered_by,
                    kill_switch_reason=kill_switch.reason,
                    created_at=datetime.now(),
                    updated_at=datetime.now(),
                )
                session.add(orm)
            else:
                orm.status = health.status.value
                orm.last_connected_at = health.last_connected_at
                orm.last_disconnected_at = health.last_disconnected_at
                orm.last_heartbeat_at = health.last_heartbeat_at
                orm.consecutive_failures = health.consecutive_failures
                orm.total_disconnections = health.total_disconnections
                orm.total_reconnections = health.total_reconnections
                orm.kill_switch_status = kill_switch.status.value
                orm.kill_switch_triggered_at = kill_switch.triggered_at
                orm.kill_switch_triggered_by = kill_switch.triggered_by
                orm.kill_switch_reason = kill_switch.reason
                orm.updated_at = datetime.now()
                session.add(orm)
            await session.flush()
            await session.commit()

    async def load(self) -> tuple[ConnectionHealth, KillSwitch] | None:
        async with self._session_factory() as session:
            stmt = select(BrokerConnectionORM)
            result = await session.execute(stmt)
            orm = result.scalar_one_or_none()
            if orm is None:
                return None
            health = ConnectionHealth(
                status=ConnectionStatus(orm.status),
                last_connected_at=orm.last_connected_at,
                last_disconnected_at=orm.last_disconnected_at,
                last_heartbeat_at=orm.last_heartbeat_at,
                consecutive_failures=orm.consecutive_failures,
                total_disconnections=orm.total_disconnections,
                total_reconnections=orm.total_reconnections,
            )
            kill_switch = KillSwitch(
                status=KillSwitchStatus(orm.kill_switch_status),
                triggered_at=orm.kill_switch_triggered_at,
                triggered_by=orm.kill_switch_triggered_by,
                reason=orm.kill_switch_reason,
            )
            return health, kill_switch


class PositionSyncRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save_snapshot(self, position: BrokerPosition, portfolio_id: str) -> None:
        async with self._session_factory() as session:
            orm = PositionSyncORM(
                portfolio_id=portfolio_id,
                symbol=position.symbol,
                quantity=str(position.quantity),
                market_value=str(position.market_value),
                cost_basis=str(position.cost_basis),
                avg_entry_price=str(position.avg_entry_price) if position.avg_entry_price else None,
                unrealized_pl=str(position.unrealized_pl),
                realized_pl=str(position.realized_pl),
                snapshot_at=position.updated_at or datetime.now(),
                created_at=datetime.now(),
            )
            session.add(orm)
            await session.flush()
            await session.commit()

    async def find_latest(
        self, portfolio_id: str, symbol: str | None = None, limit: int = 100
    ) -> list[BrokerPosition]:
        async with self._session_factory() as session:
            stmt = select(PositionSyncORM).where(PositionSyncORM.portfolio_id == portfolio_id)
            if symbol is not None:
                stmt = stmt.where(PositionSyncORM.symbol == symbol)
            stmt = stmt.order_by(PositionSyncORM.snapshot_at.desc()).limit(limit)
            result = await session.execute(stmt)
            orms = result.scalars().all()
            distinct: dict[str, BrokerPosition] = {}
            for o in orms:
                if o.symbol not in distinct:
                    distinct[o.symbol] = BrokerPosition(
                        symbol=o.symbol,
                        quantity=Decimal(o.quantity),
                        market_value=Decimal(o.market_value),
                        cost_basis=Decimal(o.cost_basis),
                        avg_entry_price=Decimal(o.avg_entry_price) if o.avg_entry_price else None,
                        unrealized_pl=Decimal(o.unrealized_pl),
                        realized_pl=Decimal(o.realized_pl),
                        updated_at=o.snapshot_at,
                    )
            return list(distinct.values())
