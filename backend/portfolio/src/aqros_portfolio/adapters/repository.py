from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aqros_portfolio.adapters.orm import (
    PortfolioORM,
    PositionORM,
)
from aqros_portfolio.domain.models import (
    CashBalance,
    Portfolio,
    PortfolioStatus,
    Position,
    PositionStatus,
    TradeDirection,
)


class PortfolioRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, portfolio: Portfolio) -> None:
        stmt = select(PortfolioORM).where(PortfolioORM.portfolio_id == portfolio.portfolio_id)
        result = await self._session.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing:
            existing.name = portfolio.name
            existing.status = portfolio.status.value
            existing.cash_total = str(portfolio.cash.total)
            existing.cash_reserved = str(portfolio.cash.reserved)
            existing.total_fees = str(portfolio.total_fees)
            existing.total_pnl_realized = str(portfolio.total_pnl_realized)
            existing.daily_pnl = str(portfolio.daily_pnl)
            existing.peak_equity = str(portfolio.peak_equity)
            existing.correlation_id = portfolio.correlation_id
            existing.updated_at = portfolio.updated_at
        else:
            orm = PortfolioORM(
                portfolio_id=portfolio.portfolio_id,
                name=portfolio.name,
                status=portfolio.status.value,
                cash_total=str(portfolio.cash.total),
                cash_reserved=str(portfolio.cash.reserved),
                total_fees=str(portfolio.total_fees),
                total_pnl_realized=str(portfolio.total_pnl_realized),
                daily_pnl=str(portfolio.daily_pnl),
                peak_equity=str(portfolio.peak_equity),
                correlation_id=portfolio.correlation_id,
                created_at=portfolio.created_at or datetime.utcnow(),
                updated_at=portfolio.updated_at,
            )
            self._session.add(orm)

    async def find_by_id(self, portfolio_id: str) -> Portfolio | None:
        stmt = select(PortfolioORM).where(PortfolioORM.portfolio_id == portfolio_id)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm is None:
            return None
        return self._orm_to_domain(orm)

    async def find_all(self) -> list[Portfolio]:
        stmt = select(PortfolioORM).order_by(PortfolioORM.created_at)
        result = await self._session.execute(stmt)
        return [self._orm_to_domain(row) for row in result.scalars()]

    async def delete(self, portfolio_id: str) -> None:
        stmt = select(PortfolioORM).where(PortfolioORM.portfolio_id == portfolio_id)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm:
            await self._session.delete(orm)

    def _orm_to_domain(self, orm: PortfolioORM) -> Portfolio:
        return Portfolio(
            portfolio_id=orm.portfolio_id,
            name=orm.name,
            status=PortfolioStatus(orm.status),
            cash=CashBalance(
                total=Decimal(orm.cash_total),
                reserved=Decimal(orm.cash_reserved),
            ),
            total_fees=Decimal(orm.total_fees),
            total_pnl_realized=Decimal(orm.total_pnl_realized),
            daily_pnl=Decimal(orm.daily_pnl),
            peak_equity=Decimal(orm.peak_equity),
            correlation_id=orm.correlation_id,
            created_at=orm.created_at,
            updated_at=orm.updated_at,
        )


class PositionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, position: Position) -> None:
        stmt = select(PositionORM).where(PositionORM.position_id == position.position_id)
        result = await self._session.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing:
            existing.symbol = position.symbol
            existing.direction = position.direction.value
            existing.quantity = str(position.quantity)
            existing.avg_entry_price = str(position.avg_entry_price)
            existing.current_price = str(position.current_price)
            existing.stop_loss = str(position.stop_loss) if position.stop_loss else None
            existing.take_profit = str(position.take_profit) if position.take_profit else None
            existing.status = position.status.value
            existing.sector = position.sector
            existing.beta = position.beta
            existing.volatility = position.volatility
            existing.avg_daily_volume = position.avg_daily_volume
            existing.realized_pnl = str(position.realized_pnl)
            existing.fees_paid = str(position.fees_paid)
            existing.correlation_id = position.correlation_id
            existing.closed_at = position.closed_at
        else:
            orm = PositionORM(
                position_id=position.position_id,
                portfolio_id=position.portfolio_id,
                symbol=position.symbol,
                direction=position.direction.value,
                quantity=str(position.quantity),
                avg_entry_price=str(position.avg_entry_price),
                current_price=str(position.current_price),
                stop_loss=str(position.stop_loss) if position.stop_loss else None,
                take_profit=str(position.take_profit) if position.take_profit else None,
                status=position.status.value,
                sector=position.sector,
                beta=position.beta,
                volatility=position.volatility,
                avg_daily_volume=position.avg_daily_volume,
                realized_pnl=str(position.realized_pnl),
                fees_paid=str(position.fees_paid),
                correlation_id=position.correlation_id,
                opened_at=position.opened_at,
                closed_at=position.closed_at,
                created_at=datetime.utcnow(),
            )
            self._session.add(orm)

    async def find_by_id(self, position_id: str) -> Position | None:
        stmt = select(PositionORM).where(PositionORM.position_id == position_id)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm is None:
            return None
        return self._orm_to_domain(orm)

    async def find_by_portfolio(self, portfolio_id: str) -> list[Position]:
        stmt = (
            select(PositionORM)
            .where(PositionORM.portfolio_id == portfolio_id)
            .order_by(PositionORM.created_at)
        )
        result = await self._session.execute(stmt)
        return [self._orm_to_domain(row) for row in result.scalars()]

    async def find_open_by_symbol(self, portfolio_id: str, symbol: str) -> Position | None:
        stmt = select(PositionORM).where(
            PositionORM.portfolio_id == portfolio_id,
            PositionORM.symbol == symbol,
            PositionORM.status == PositionStatus.OPEN.value,
        )
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm is None:
            return None
        return self._orm_to_domain(orm)

    def _orm_to_domain(self, orm: PositionORM) -> Position:
        return Position(
            position_id=orm.position_id,
            portfolio_id=orm.portfolio_id,
            symbol=orm.symbol,
            direction=TradeDirection(orm.direction),
            quantity=Decimal(orm.quantity),
            avg_entry_price=Decimal(orm.avg_entry_price),
            current_price=Decimal(orm.current_price),
            stop_loss=Decimal(orm.stop_loss) if orm.stop_loss else None,
            take_profit=Decimal(orm.take_profit) if orm.take_profit else None,
            status=PositionStatus(orm.status),
            sector=orm.sector,
            beta=orm.beta,
            volatility=orm.volatility,
            avg_daily_volume=orm.avg_daily_volume,
            realized_pnl=Decimal(orm.realized_pnl),
            fees_paid=Decimal(orm.fees_paid),
            correlation_id=orm.correlation_id,
            opened_at=orm.opened_at,
            closed_at=orm.closed_at,
        )
