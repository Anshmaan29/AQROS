from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from aqros_portfolio.adapters.repository import PortfolioRepository, PositionRepository
from aqros_portfolio.api.deps import get_db_session
from aqros_portfolio.api.schemas import (
    CashUpdateRequest,
    ExposureResponse,
    HealthResponse,
    PnLResponse,
    PortfolioCreateRequest,
    PortfolioCreateResponse,
    PortfolioResponse,
    PortfolioStatisticsResponse,
    PositionCloseRequest,
    PositionCreateRequest,
    PositionModifyRequest,
    PositionResponse,
)
from aqros_portfolio.domain.models import (
    CashBalance,
    Portfolio,
    PortfolioStatistics,
    PortfolioStatus,
    Position,
    PositionStatus,
    TradeDirection,
)

_logger = get_logger(__name__)

router = APIRouter(tags=["portfolio"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", service="portfolio-engine", database=True)


@router.get("/health/live", response_model=HealthResponse)
async def health_live() -> HealthResponse:
    return HealthResponse(status="healthy", service="portfolio-engine", database=True)


@router.get("/health/ready", response_model=HealthResponse)
async def health_ready() -> HealthResponse:
    return HealthResponse(status="healthy", service="portfolio-engine", database=True)


@router.post("/v1/portfolios", response_model=PortfolioCreateResponse, status_code=201)
async def create_portfolio(
    req: PortfolioCreateRequest,
    session: AsyncSession = Depends(get_db_session),
) -> PortfolioCreateResponse:
    repo = PortfolioRepository(session)
    portfolio_id = str(uuid4())
    now = datetime.now(UTC)
    portfolio = Portfolio(
        portfolio_id=portfolio_id,
        name=req.name,
        status=PortfolioStatus.ACTIVE,
        cash=CashBalance(total=req.initial_cash),
        peak_equity=req.initial_cash,
        created_at=now,
        updated_at=now,
        correlation_id=req.correlation_id,
    )
    await repo.save(portfolio)
    _logger.info("portfolio.created", portfolio_id=portfolio_id, name=req.name)
    return PortfolioCreateResponse(
        portfolio_id=portfolio_id,
        name=portfolio.name,
        total_equity=portfolio.total_equity,
        cash=portfolio.cash.available,
        status=portfolio.status.value,
    )


@router.get("/v1/portfolios", response_model=list[PortfolioResponse])
async def list_portfolios(
    session: AsyncSession = Depends(get_db_session),
) -> list[PortfolioResponse]:
    repo = PortfolioRepository(session)
    portfolios = await repo.find_all()
    return [_portfolio_to_response(p) for p in portfolios]


@router.get("/v1/portfolios/{portfolio_id}", response_model=PortfolioResponse)
async def get_portfolio(
    portfolio_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> PortfolioResponse:
    repo = PortfolioRepository(session)
    portfolio = await repo.find_by_id(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail=f"Portfolio {portfolio_id} not found")
    return _portfolio_to_response(portfolio)


@router.get("/v1/portfolios/{portfolio_id}/positions", response_model=list[PositionResponse])
async def list_positions(
    portfolio_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> list[PositionResponse]:
    repo = PositionRepository(session)
    positions = await repo.find_by_portfolio(portfolio_id)
    return [_position_to_response(p) for p in positions]


@router.post(
    "/v1/portfolios/{portfolio_id}/positions", response_model=PositionResponse, status_code=201
)
async def open_position(
    portfolio_id: str,
    req: PositionCreateRequest,
    session: AsyncSession = Depends(get_db_session),
) -> PositionResponse:
    port_repo = PortfolioRepository(session)
    pos_repo = PositionRepository(session)

    portfolio = await port_repo.find_by_id(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail=f"Portfolio {portfolio_id} not found")

    position_id = str(uuid4())
    now = datetime.now(UTC)
    symbol = req.symbol.upper()

    existing = await pos_repo.find_open_by_symbol(portfolio_id, symbol)
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Open position for {symbol} already exists")

    direction = TradeDirection.LONG if req.direction == "long" else TradeDirection.SHORT
    position = Position(
        position_id=position_id,
        portfolio_id=portfolio_id,
        symbol=symbol,
        direction=direction,
        quantity=req.quantity,
        avg_entry_price=req.entry_price,
        current_price=req.entry_price,
        stop_loss=req.stop_loss,
        take_profit=req.take_profit,
        status=PositionStatus.OPEN,
        sector=req.sector,
        beta=req.beta,
        volatility=req.volatility,
        avg_daily_volume=req.avg_daily_volume,
        correlation_id=req.correlation_id,
        opened_at=now,
    )

    cost = req.quantity * req.entry_price
    try:
        portfolio.reserve_cash(cost)
    except ValueError:
        raise HTTPException(
            status_code=400, detail="Insufficient available cash to open position"
        ) from None

    portfolio.add_position(position)
    await pos_repo.save(position)
    await port_repo.save(portfolio)

    _logger.info("position.opened", portfolio_id=portfolio_id, symbol=symbol, qty=str(req.quantity))
    return _position_to_response(position)


@router.post(
    "/v1/portfolios/{portfolio_id}/positions/{symbol}/close", response_model=PositionResponse
)
async def close_position(
    portfolio_id: str,
    symbol: str,
    req: PositionCloseRequest,
    session: AsyncSession = Depends(get_db_session),
) -> PositionResponse:
    port_repo = PortfolioRepository(session)
    pos_repo = PositionRepository(session)

    portfolio = await port_repo.find_by_id(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail=f"Portfolio {portfolio_id} not found")

    sym = symbol.upper()
    position = await pos_repo.find_open_by_symbol(portfolio_id, sym)
    if position is None:
        raise HTTPException(status_code=404, detail=f"Open position for {sym} not found")

    now = datetime.now(UTC)
    position.close(req.exit_price, now)
    portfolio.close_position(sym, req.exit_price, now)
    portfolio.release_cash(position.entry_value)

    await pos_repo.save(position)
    await port_repo.save(portfolio)

    _logger.info(
        "position.closed", portfolio_id=portfolio_id, symbol=sym, pnl=str(position.realized_pnl)
    )
    return _position_to_response(position)


@router.patch("/v1/portfolios/{portfolio_id}/positions/{symbol}", response_model=PositionResponse)
async def modify_position(
    portfolio_id: str,
    symbol: str,
    req: PositionModifyRequest,
    session: AsyncSession = Depends(get_db_session),
) -> PositionResponse:
    pos_repo = PositionRepository(session)
    sym = symbol.upper()

    position = await pos_repo.find_open_by_symbol(portfolio_id, sym)
    if position is None:
        raise HTTPException(status_code=404, detail=f"Open position for {sym} not found")

    if req.stop_loss is not None:
        position.update_stop_loss(req.stop_loss)
    if req.take_profit is not None:
        position.update_take_profit(req.take_profit)

    await pos_repo.save(position)
    _logger.info("position.modified", portfolio_id=portfolio_id, symbol=sym)
    return _position_to_response(position)


@router.get("/v1/portfolios/{portfolio_id}/pnl", response_model=PnLResponse)
async def get_pnl(
    portfolio_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> PnLResponse:
    repo = PortfolioRepository(session)
    portfolio = await repo.find_by_id(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail=f"Portfolio {portfolio_id} not found")

    return PnLResponse(
        portfolio_id=portfolio_id,
        total_equity=portfolio.total_equity,
        unrealized_pnl=portfolio.unrealized_pnl,
        realized_pnl=portfolio.total_pnl_realized,
        daily_pnl=portfolio.daily_pnl,
        total_pnl=portfolio.total_pnl,
        daily_return_pct=round(portfolio.daily_return_pct, 4),
        as_of=datetime.now(UTC),
    )


@router.get("/v1/portfolios/{portfolio_id}/statistics", response_model=PortfolioStatisticsResponse)
async def get_statistics(
    portfolio_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> PortfolioStatisticsResponse:
    port_repo = PortfolioRepository(session)
    pos_repo = PositionRepository(session)

    portfolio = await port_repo.find_by_id(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail=f"Portfolio {portfolio_id} not found")

    positions = await pos_repo.find_by_portfolio(portfolio_id)
    stats = PortfolioStatistics.calculate(positions, portfolio.drawdown_pct)

    return PortfolioStatisticsResponse(
        total_trades=stats.total_trades,
        winning_trades=stats.winning_trades,
        losing_trades=stats.losing_trades,
        total_pnl=stats.total_pnl,
        gross_profit=stats.gross_profit,
        gross_loss=stats.gross_loss,
        max_drawdown_pct=round(stats.max_drawdown_pct, 4),
        current_drawdown_pct=round(stats.current_drawdown_pct, 4),
        sharpe_ratio=stats.sharpe_ratio,
        sortino_ratio=stats.sortino_ratio,
        profit_factor=stats.profit_factor,
        win_rate=stats.win_rate,
        avg_win=stats.avg_win,
        avg_loss=stats.avg_loss,
        largest_win=stats.largest_win,
        largest_loss=stats.largest_loss,
        total_fees=stats.total_fees,
    )


@router.get("/v1/portfolios/{portfolio_id}/exposure", response_model=ExposureResponse)
async def get_exposure(
    portfolio_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> ExposureResponse:
    repo = PortfolioRepository(session)
    portfolio = await repo.find_by_id(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail=f"Portfolio {portfolio_id} not found")

    sector_exposures = portfolio.sector_exposures()
    symbol_exposures = {p.symbol: p.market_value for p in portfolio.open_positions}

    concentration_risk = 0.0
    if portfolio.total_equity > 0 and portfolio.open_positions:
        max_sym_exposure = max(
            float(p.market_value / portfolio.total_equity) * 100.0 for p in portfolio.open_positions
        )
        concentration_risk = max_sym_exposure

    return ExposureResponse(
        total_equity=portfolio.total_equity,
        cash=portfolio.cash.total,
        gross_exposure=portfolio.gross_exposure,
        net_exposure=portfolio.net_exposure,
        gross_exposure_pct=round(portfolio.gross_exposure_pct, 4),
        net_exposure_pct=round(portfolio.net_exposure_pct, 4),
        leverage=round(portfolio.leverage, 4),
        position_count=portfolio.position_count,
        sector_exposures=sector_exposures,
        symbol_exposures=symbol_exposures,
        concentration_risk=round(concentration_risk, 4),
    )


@router.post("/v1/portfolios/{portfolio_id}/cash", response_model=PortfolioResponse)
async def update_cash(
    portfolio_id: str,
    req: CashUpdateRequest,
    session: AsyncSession = Depends(get_db_session),
) -> PortfolioResponse:
    repo = PortfolioRepository(session)
    portfolio = await repo.find_by_id(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail=f"Portfolio {portfolio_id} not found")

    if req.amount > 0:
        portfolio.update_cash(req.amount)
        _logger.info("cash.deposited", portfolio_id=portfolio_id, amount=str(req.amount))
    else:
        try:
            portfolio.update_cash(req.amount)
            _logger.info("cash.withdrawn", portfolio_id=portfolio_id, amount=str(abs(req.amount)))
        except ValueError:
            raise HTTPException(
                status_code=400, detail="Insufficient cash for withdrawal"
            ) from None

    portfolio.updated_at = datetime.now(UTC)
    await repo.save(portfolio)
    return _portfolio_to_response(portfolio)


def _portfolio_to_response(p: Portfolio) -> PortfolioResponse:
    return PortfolioResponse(
        portfolio_id=p.portfolio_id,
        name=p.name,
        status=p.status.value,
        total_equity=p.total_equity,
        cash_available=p.cash.available,
        cash_reserved=p.cash.reserved,
        market_value=p.market_value,
        gross_exposure=p.gross_exposure,
        gross_exposure_pct=round(p.gross_exposure_pct, 4),
        net_exposure=p.net_exposure,
        net_exposure_pct=round(p.net_exposure_pct, 4),
        leverage=round(p.leverage, 4),
        position_count=p.position_count,
        unrealized_pnl=p.unrealized_pnl,
        realized_pnl=p.total_pnl_realized,
        daily_pnl=p.daily_pnl,
        daily_return_pct=round(p.daily_return_pct, 4),
        drawdown_pct=round(p.drawdown_pct, 4),
        peak_equity=p.peak_equity,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


def _position_to_response(p: Position) -> PositionResponse:
    return PositionResponse(
        position_id=p.position_id,
        portfolio_id=p.portfolio_id,
        symbol=p.symbol,
        direction=p.direction.value,
        quantity=p.quantity,
        avg_entry_price=p.avg_entry_price,
        current_price=p.current_price,
        market_value=p.market_value,
        unrealized_pnl=p.unrealized_pnl,
        unrealized_pnl_pct=round(p.unrealized_pnl_pct, 4),
        realized_pnl=p.realized_pnl,
        total_pnl=p.total_pnl,
        status=p.status.value,
        stop_loss=p.stop_loss,
        take_profit=p.take_profit,
        sector=p.sector,
        beta=p.beta,
        volatility=p.volatility,
        opened_at=p.opened_at,
        closed_at=p.closed_at,
    )
