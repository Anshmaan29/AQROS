from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from aqros_risk_engine.domain.models import (
    CircuitBreaker,
    DrawdownTracker,
    EvaluationContext,
    PortfolioExposure,
    PositionExposure,
    RiskDecision,
    RiskLevel,
    RiskLimits,
    Signal,
)
from aqros_risk_engine.domain.rules import (
    ALL_RULES,
    CircuitBreakerRule,
    CooldownTimerRule,
    LiquidityFilterRule,
    MaxConcurrentPositionsRule,
    MaxDailyLossRule,
    MaxDrawdownRule,
    MaxLeverageRule,
    MaxPortfolioExposureRule,
    MaxPositionSizeRule,
    MaxSymbolExposureRule,
    MinimumConfidenceRule,
    MinimumPredictionQualityRule,
    SectorExposureRule,
    VolatilityFilterRule,
)

_NOW = datetime.now(UTC)

_DEFAULT_LIMITS = RiskLimits(
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


def _make_portfolio(**overrides) -> PortfolioExposure:
    params = {
        "total_equity": Decimal("1000000"),
        "total_market_value": Decimal("200000"),
        "gross_exposure": Decimal("200000"),
        "net_exposure": Decimal("200000"),
        "cash": Decimal("800000"),
        "leverage": 0.2,
        "positions": (
            PositionExposure(
                symbol="MSFT",
                quantity=Decimal("1000"),
                avg_entry_price=Decimal("200"),
                current_price=Decimal("210"),
                market_value=Decimal("210000"),
                unrealized_pnl=Decimal("10000"),
                realized_pnl=Decimal("0"),
                sector="Technology",
            ),
        ),
        "sector_exposures": {"Technology": Decimal("210000")},
        "daily_pnl": Decimal("5000"),
    }
    params.update(overrides)
    return PortfolioExposure(**params)


def _make_ctx(
    signal: Signal | None = None, portfolio: PortfolioExposure | None = None
) -> EvaluationContext:
    return EvaluationContext(
        signal=signal or _make_signal(),
        portfolio=portfolio or _make_portfolio(),
        limits=_DEFAULT_LIMITS,
        drawdown=DrawdownTracker(peak_equity=Decimal("1000000")),
        circuit_breaker=CircuitBreaker(
            daily_loss_limit_pct=3.0, consecutive_loss_limit=3, cooldown_timedelta=60
        ),
        now=_NOW,
    )


async def test_max_position_size_rule_passes() -> None:
    rule = MaxPositionSizeRule()
    ctx = _make_ctx(_make_signal(quantity=Decimal("100")))
    result = await rule.evaluate(ctx)
    assert result is None


async def test_max_position_size_rule_reduces() -> None:
    rule = MaxPositionSizeRule()
    signal = _make_signal(quantity=Decimal("100000"), current_price=Decimal("1000"))
    ctx = _make_ctx(signal)
    result = await rule.evaluate(ctx)
    assert result is not None
    assert result.decision == RiskDecision.REDUCE
    assert result.suggested_quantity is not None
    assert result.suggested_quantity < signal.quantity


async def test_max_portfolio_exposure_passes() -> None:
    rule = MaxPortfolioExposureRule()
    ctx = _make_ctx(_make_signal(quantity=Decimal("100")))
    result = await rule.evaluate(ctx)
    assert result is None


async def test_max_portfolio_exposure_rejects() -> None:
    rule = MaxPortfolioExposureRule()
    signal = _make_signal(quantity=Decimal("1000000"), current_price=Decimal("1000"))
    ctx = _make_ctx(signal)
    result = await rule.evaluate(ctx)
    assert result is not None
    assert result.decision == RiskDecision.REJECT
    assert result.risk_level == RiskLevel.CRITICAL


async def test_max_daily_loss_passes() -> None:
    rule = MaxDailyLossRule()
    ctx = _make_ctx(portfolio=_make_portfolio(daily_pnl=Decimal("5000")))
    result = await rule.evaluate(ctx)
    assert result is None


async def test_max_daily_loss_rejects() -> None:
    rule = MaxDailyLossRule()
    ctx = _make_ctx(portfolio=_make_portfolio(daily_pnl=Decimal("-50000")))
    result = await rule.evaluate(ctx)
    assert result is not None
    assert result.decision == RiskDecision.REJECT


async def test_max_drawdown_passes() -> None:
    rule = MaxDrawdownRule()
    dd = DrawdownTracker(peak_equity=Decimal("1000000"))
    dd.update(Decimal("950000"))
    ctx = _make_ctx()
    ctx = EvaluationContext(
        signal=ctx.signal,
        portfolio=ctx.portfolio,
        limits=ctx.limits,
        drawdown=dd,
        circuit_breaker=ctx.circuit_breaker,
        now=ctx.now,
    )
    result = await rule.evaluate(ctx)
    assert result is None


async def test_max_drawdown_rejects() -> None:
    rule = MaxDrawdownRule()
    dd = DrawdownTracker(peak_equity=Decimal("1000000"))
    dd.update(Decimal("700000"))
    ctx = _make_ctx()
    ctx = EvaluationContext(
        signal=ctx.signal,
        portfolio=ctx.portfolio,
        limits=ctx.limits,
        drawdown=dd,
        circuit_breaker=ctx.circuit_breaker,
        now=ctx.now,
    )
    result = await rule.evaluate(ctx)
    assert result is not None
    assert result.decision == RiskDecision.REJECT


async def test_max_leverage_passes() -> None:
    rule = MaxLeverageRule()
    ctx = _make_ctx()
    result = await rule.evaluate(ctx)
    assert result is None


async def test_max_leverage_rejects() -> None:
    rule = MaxLeverageRule()
    signal = _make_signal(quantity=Decimal("500000"), current_price=Decimal("100"))
    portfolio = _make_portfolio(
        total_equity=Decimal("100000"), total_market_value=Decimal("500000")
    )
    ctx = _make_ctx(signal, portfolio)
    result = await rule.evaluate(ctx)
    assert result is not None
    assert result.decision == RiskDecision.REJECT


async def test_max_concurrent_positions_passes() -> None:
    rule = MaxConcurrentPositionsRule()
    ctx = _make_ctx()
    result = await rule.evaluate(ctx)
    assert result is None


async def test_max_concurrent_positions_rejects() -> None:
    rule = MaxConcurrentPositionsRule()
    positions = tuple(
        PositionExposure(
            symbol=f"SYM{i}",
            quantity=Decimal("100"),
            avg_entry_price=Decimal("10"),
            current_price=Decimal("10"),
            market_value=Decimal("1000"),
            unrealized_pnl=Decimal("0"),
            realized_pnl=Decimal("0"),
        )
        for i in range(20)
    )
    portfolio = PortfolioExposure(
        total_equity=Decimal("1000000"),
        total_market_value=Decimal("20000"),
        gross_exposure=Decimal("20000"),
        net_exposure=Decimal("20000"),
        cash=Decimal("980000"),
        leverage=0.02,
        positions=positions,
    )
    ctx = _make_ctx(portfolio=portfolio)
    result = await rule.evaluate(ctx)
    assert result is not None
    assert result.decision == RiskDecision.REJECT


async def test_max_symbol_exposure_passes() -> None:
    rule = MaxSymbolExposureRule()
    ctx = _make_ctx(_make_signal(symbol="GOOG"))
    result = await rule.evaluate(ctx)
    assert result is None


async def test_max_symbol_exposure_reduces() -> None:
    rule = MaxSymbolExposureRule()
    signal = _make_signal(quantity=Decimal("100000"), current_price=Decimal("100"))
    ctx = _make_ctx(signal)
    result = await rule.evaluate(ctx)
    assert result is not None
    assert result.decision == RiskDecision.REDUCE


async def test_sector_exposure_passes() -> None:
    rule = SectorExposureRule()
    ctx = _make_ctx(_make_signal(sector="Healthcare"))
    result = await rule.evaluate(ctx)
    assert result is None


async def test_sector_exposure_reduces() -> None:
    rule = SectorExposureRule()
    signal = _make_signal(
        quantity=Decimal("500000"), current_price=Decimal("10"), sector="Technology"
    )
    ctx = _make_ctx(signal)
    result = await rule.evaluate(ctx)
    assert result is not None
    assert result.decision == RiskDecision.REDUCE


async def test_volatility_filter_passes() -> None:
    rule = VolatilityFilterRule()
    ctx = _make_ctx(_make_signal(volatility=0.25))
    result = await rule.evaluate(ctx)
    assert result is None


async def test_volatility_too_low() -> None:
    rule = VolatilityFilterRule()
    ctx = _make_ctx(_make_signal(volatility=0.0001))
    result = await rule.evaluate(ctx)
    assert result is not None
    assert result.decision == RiskDecision.REJECT
    assert result.risk_level == RiskLevel.LOW


async def test_volatility_too_high() -> None:
    rule = VolatilityFilterRule()
    ctx = _make_ctx(_make_signal(volatility=0.8))
    result = await rule.evaluate(ctx)
    assert result is not None
    assert result.decision == RiskDecision.REJECT
    assert result.risk_level == RiskLevel.HIGH


async def test_liquidity_filter_passes() -> None:
    rule = LiquidityFilterRule()
    ctx = _make_ctx(_make_signal(avg_daily_volume=50_000_000.0))
    result = await rule.evaluate(ctx)
    assert result is None


async def test_liquidity_below_minimum() -> None:
    rule = LiquidityFilterRule()
    ctx = _make_ctx(_make_signal(avg_daily_volume=1000.0))
    result = await rule.evaluate(ctx)
    assert result is not None
    assert result.decision == RiskDecision.REJECT


async def test_liquidity_exceeds_pct() -> None:
    rule = LiquidityFilterRule()
    ctx = _make_ctx(
        _make_signal(
            quantity=Decimal("5000"), current_price=Decimal("100"), avg_daily_volume=100_000.0
        )
    )
    result = await rule.evaluate(ctx)
    assert result is not None
    assert result.decision == RiskDecision.REDUCE


async def test_minimum_confidence_passes() -> None:
    rule = MinimumConfidenceRule()
    ctx = _make_ctx(_make_signal(confidence=0.85))
    result = await rule.evaluate(ctx)
    assert result is None


async def test_minimum_confidence_rejects() -> None:
    rule = MinimumConfidenceRule()
    ctx = _make_ctx(_make_signal(confidence=0.1))
    result = await rule.evaluate(ctx)
    assert result is not None
    assert result.decision == RiskDecision.REJECT


async def test_minimum_prediction_quality_passes() -> None:
    rule = MinimumPredictionQualityRule()
    ctx = _make_ctx(_make_signal(prediction_quality=0.7))
    result = await rule.evaluate(ctx)
    assert result is None


async def test_minimum_prediction_quality_rejects() -> None:
    rule = MinimumPredictionQualityRule()
    ctx = _make_ctx(_make_signal(prediction_quality=0.05))
    result = await rule.evaluate(ctx)
    assert result is not None
    assert result.decision == RiskDecision.REJECT


async def test_circuit_breaker_passes() -> None:
    rule = CircuitBreakerRule()
    ctx = _make_ctx()
    result = await rule.evaluate(ctx)
    assert result is None


async def test_circuit_breaker_rejects() -> None:
    rule = CircuitBreakerRule()
    cb = CircuitBreaker(daily_loss_limit_pct=1.0, consecutive_loss_limit=1, cooldown_timedelta=60)
    cb.is_tripped = True
    ctx = _make_ctx()
    ctx = EvaluationContext(
        signal=ctx.signal,
        portfolio=ctx.portfolio,
        limits=ctx.limits,
        drawdown=ctx.drawdown,
        circuit_breaker=cb,
        now=ctx.now,
    )
    result = await rule.evaluate(ctx)
    assert result is not None
    assert result.decision == RiskDecision.REJECT
    assert result.risk_level == RiskLevel.CRITICAL


async def test_all_rules_approve_valid_signal() -> None:
    signal = _make_signal()
    portfolio = _make_portfolio()
    dd = DrawdownTracker(peak_equity=Decimal("1000000"))
    cb = CircuitBreaker(daily_loss_limit_pct=3.0, consecutive_loss_limit=3, cooldown_timedelta=60)
    ctx = EvaluationContext(
        signal=signal,
        portfolio=portfolio,
        limits=_DEFAULT_LIMITS,
        drawdown=dd,
        circuit_breaker=cb,
        now=_NOW,
    )
    for rule in ALL_RULES:
        result = await rule.evaluate(ctx)
        assert result is None, f"Rule {rule.name} unexpectedly rejected: {result}"


async def test_cooldown_timer_no_position() -> None:
    rule = CooldownTimerRule()
    ctx = _make_ctx(_make_signal(symbol="GOOG"))
    result = await rule.evaluate(ctx)
    assert result is None
