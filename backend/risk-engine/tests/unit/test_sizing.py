from __future__ import annotations

from datetime import UTC
from decimal import Decimal

from aqros_risk_engine.domain.models import (
    CircuitBreaker,
    DrawdownTracker,
    EvaluationContext,
    PortfolioExposure,
    RiskLimits,
    Signal,
)
from aqros_risk_engine.domain.sizing import (
    AtrSizing,
    FixedDollarSizing,
    FixedQuantitySizing,
    KellyCriterionSizing,
    PercentageOfEquitySizing,
    RiskPerTradeSizing,
    VolatilityTargetingSizing,
)


def _make_signal(**overrides) -> Signal:
    params = {
        "signal_id": "sig-001",
        "symbol": "AAPL",
        "side": "buy",
        "quantity": Decimal("100"),
        "confidence": 0.85,
        "prediction": 0.02,
        "prediction_quality": 0.7,
        "current_price": Decimal("150.0"),
        "sector": "Technology",
        "volatility": 0.25,
        "avg_daily_volume": 50_000_000.0,
    }
    params.update(overrides)
    return Signal(**params)


def _make_ctx(signal: Signal | None = None) -> EvaluationContext:
    from datetime import datetime

    return EvaluationContext(
        signal=signal or _make_signal(),
        portfolio=PortfolioExposure(
            total_equity=Decimal("1000000"),
            total_market_value=Decimal("200000"),
            gross_exposure=Decimal("200000"),
            net_exposure=Decimal("200000"),
            cash=Decimal("800000"),
            leverage=0.2,
        ),
        limits=RiskLimits(
            max_position_size_pct=5.0,
            max_portfolio_exposure_pct=80.0,
            max_daily_loss_pct=2.0,
            max_drawdown_pct=15.0,
            max_leverage=2.0,
            max_concurrent_positions=20,
            max_symbol_exposure_pct=10.0,
            max_sector_exposure_pct=25.0,
            min_volatility=0.001,
            max_volatility=0.6,
            min_avg_daily_volume=100_000.0,
            min_confidence=0.3,
            min_prediction_quality=0.1,
        ),
        drawdown=DrawdownTracker(peak_equity=Decimal("1000000")),
        circuit_breaker=CircuitBreaker(
            daily_loss_limit_pct=3.0,
            consecutive_loss_limit=3,
            cooldown_timedelta=60,
        ),
        now=datetime.now(UTC),
    )


async def test_fixed_quantity_sizing() -> None:
    strategy = FixedQuantitySizing(Decimal("200"))
    signal = _make_signal()
    ctx = _make_ctx(signal)
    result = await strategy.size(signal, ctx)
    assert result.quantity == Decimal("200")
    assert result.sizing_method == "fixed_quantity"


async def test_fixed_dollar_sizing() -> None:
    strategy = FixedDollarSizing(Decimal("30000"))
    signal = _make_signal(current_price=Decimal("150.0"))
    ctx = _make_ctx(signal)
    result = await strategy.size(signal, ctx)
    assert result.quantity == Decimal("200")
    assert result.sizing_method == "fixed_dollar"


async def test_fixed_dollar_zero_price() -> None:
    strategy = FixedDollarSizing(Decimal("10000"))
    signal = _make_signal(current_price=Decimal("0"))
    ctx = _make_ctx(signal)
    result = await strategy.size(signal, ctx)
    assert result.quantity == Decimal("0")


async def test_percentage_of_equity_sizing() -> None:
    strategy = PercentageOfEquitySizing(2.0)
    signal = _make_signal(current_price=Decimal("100.0"))
    ctx = _make_ctx(signal)
    result = await strategy.size(signal, ctx)
    assert result.sizing_method == "percentage_of_equity"
    assert result.quantity == Decimal("200")
    assert "2.0%" in result.reason


async def test_percentage_of_equity_zero_price() -> None:
    strategy = PercentageOfEquitySizing(2.0)
    signal = _make_signal(current_price=Decimal("0"))
    ctx = _make_ctx(signal)
    result = await strategy.size(signal, ctx)
    assert result.quantity == Decimal("0")


async def test_kelly_criterion_sizing() -> None:
    strategy = KellyCriterionSizing(0.25)
    signal = _make_signal(confidence=0.7, prediction=1.0, current_price=Decimal("50.0"))
    ctx = _make_ctx(signal)
    result = await strategy.size(signal, ctx)
    assert result.quantity > 0
    assert result.sizing_method == "kelly_criterion"
    assert "Kelly" in result.reason


async def test_kelly_criterion_low_confidence_zero() -> None:
    strategy = KellyCriterionSizing(0.25)
    signal = _make_signal(confidence=0.1, prediction=-0.01, current_price=Decimal("50.0"))
    ctx = _make_ctx(signal)
    result = await strategy.size(signal, ctx)
    assert result.quantity == 0


async def test_volatility_targeting_sizing() -> None:
    strategy = VolatilityTargetingSizing(0.15)
    signal = _make_signal(current_price=Decimal("100.0"), volatility=0.30)
    ctx = _make_ctx(signal)
    result = await strategy.size(signal, ctx)
    assert result.quantity > 0
    assert result.sizing_method == "volatility_targeting"
    assert "vol" in result.reason.lower()


async def test_volatility_targeting_zero_vol() -> None:
    strategy = VolatilityTargetingSizing(0.15)
    signal = _make_signal(current_price=Decimal("100.0"), volatility=0.0)
    ctx = _make_ctx(signal)
    result = await strategy.size(signal, ctx)
    assert result.quantity > 0


async def test_atr_sizing() -> None:
    strategy = AtrSizing()
    signal = _make_signal(current_price=Decimal("100.0"), volatility=0.02)
    ctx = _make_ctx(signal)
    result = await strategy.size(signal, ctx)
    assert result.quantity > 0
    assert result.sizing_method == "atr_sizing"


async def test_risk_per_trade_sizing() -> None:
    strategy = RiskPerTradeSizing(0.5, 2.0)
    signal = _make_signal(current_price=Decimal("100.0"))
    ctx = _make_ctx(signal)
    result = await strategy.size(signal, ctx)
    assert result.quantity == 2500
    assert result.sizing_method == "risk_per_trade"
    assert result.stop_loss is not None
    assert result.stop_loss == Decimal("98.0")


async def test_risk_per_trade_zero_price() -> None:
    strategy = RiskPerTradeSizing(0.5, 2.0)
    signal = _make_signal(current_price=Decimal("0"))
    ctx = _make_ctx(signal)
    result = await strategy.size(signal, ctx)
    assert result.quantity == 0
