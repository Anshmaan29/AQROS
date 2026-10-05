from __future__ import annotations

from decimal import Decimal

from aqros_risk_engine.domain.models import (
    CircuitBreaker,
    DrawdownTracker,
    PortfolioExposure,
    PositionExposure,
    PositionSizingResult,
    RiskDecision,
    RiskLevel,
    RiskLimits,
    RiskReason,
    RiskStatistics,
    RiskVerdict,
    Signal,
)


def test_risk_decision_values() -> None:
    assert RiskDecision.APPROVE.value == "approve"
    assert RiskDecision.REJECT.value == "reject"
    assert RiskDecision.MODIFY.value == "modify"
    assert RiskDecision.REDUCE.value == "reduce"
    assert len(RiskDecision) == 4


def test_risk_level_ordering() -> None:
    assert RiskLevel.LOW.value < RiskLevel.MEDIUM.value
    assert RiskLevel.MEDIUM.value < RiskLevel.HIGH.value
    assert RiskLevel.HIGH.value < RiskLevel.CRITICAL.value


def test_risk_reason_codes() -> None:
    assert RiskReason.MAX_POSITION_EXCEEDED.value == "max_position_exceeded"
    assert RiskReason.APPROVED.value == "approved"
    assert RiskReason.CIRCUIT_BREAKER_ACTIVE.value == "circuit_breaker_active"
    assert len(RiskReason) == 19


def test_position_exposure() -> None:
    pos = PositionExposure(
        symbol="AAPL",
        quantity=Decimal("100"),
        avg_entry_price=Decimal("150.0"),
        current_price=Decimal("155.0"),
        market_value=Decimal("15500.0"),
        unrealized_pnl=Decimal("500.0"),
        realized_pnl=Decimal("0"),
        sector="Technology",
    )
    assert pos.symbol == "AAPL"
    assert pos.quantity == Decimal("100")
    assert pos.sector == "Technology"


def test_portfolio_exposure_properties() -> None:
    pe = PortfolioExposure(
        total_equity=Decimal("1000000"),
        total_market_value=Decimal("500000"),
        gross_exposure=Decimal("500000"),
        net_exposure=Decimal("500000"),
        cash=Decimal("500000"),
        leverage=0.5,
        positions=(),
    )
    assert pe.position_count == 0
    assert pe.gross_exposure_pct == 50.0
    assert pe.daily_loss_pct == 0.0

    pe_loss = PortfolioExposure(
        total_equity=Decimal("1000000"),
        total_market_value=Decimal("500000"),
        gross_exposure=Decimal("600000"),
        net_exposure=Decimal("400000"),
        cash=Decimal("400000"),
        leverage=0.6,
        positions=(),
        daily_pnl=Decimal("-30000"),
    )
    assert round(pe_loss.daily_loss_pct, 1) == 3.0


def test_drawdown_tracker() -> None:
    dd = DrawdownTracker(peak_equity=Decimal("1000000"))
    assert dd.current_drawdown_pct == 0.0
    assert dd.max_drawdown_pct == 0.0

    dd.update(Decimal("900000"))
    assert round(dd.current_drawdown_pct, 1) == 10.0
    assert round(dd.max_drawdown_pct, 1) == 10.0

    dd.update(Decimal("800000"))
    assert round(dd.current_drawdown_pct, 1) == 20.0
    assert round(dd.max_drawdown_pct, 1) == 20.0

    dd.update(Decimal("950000"))
    assert round(dd.current_drawdown_pct, 1) == 5.0
    assert round(dd.max_drawdown_pct, 1) == 20.0

    dd.update(Decimal("1100000"))
    assert dd.current_drawdown_pct == 0.0


def test_circuit_breaker_trip_on_loss_pct() -> None:
    from datetime import datetime, timedelta

    cb = CircuitBreaker(
        daily_loss_limit_pct=3.0,
        consecutive_loss_limit=3,
        cooldown_timedelta=timedelta(hours=1),
    )
    assert not cb.check(datetime.now())

    cb.record_trade_result(Decimal("-20000"), Decimal("1000000"), datetime.now())
    assert cb.daily_loss_pct == 2.0
    assert cb.consecutive_losses == 1
    assert not cb.is_tripped

    cb.record_trade_result(Decimal("-15000"), Decimal("1000000"), datetime.now())
    assert round(cb.daily_loss_pct, 1) == 3.5
    assert cb.is_tripped
    assert cb.check(datetime.now())


def test_circuit_breaker_trip_on_consecutive_losses() -> None:
    from datetime import datetime, timedelta

    cb = CircuitBreaker(
        daily_loss_limit_pct=10.0,
        consecutive_loss_limit=2,
        cooldown_timedelta=timedelta(hours=1),
    )
    cb.record_trade_result(Decimal("-1000"), Decimal("1000000"), datetime.now())
    assert cb.consecutive_losses == 1
    assert not cb.is_tripped

    cb.record_trade_result(Decimal("-2000"), Decimal("1000000"), datetime.now())
    assert cb.consecutive_losses == 2
    assert cb.is_tripped

    cb.record_trade_result(Decimal("500"), Decimal("1000000"), datetime.now())
    assert cb.consecutive_losses == 0
    assert cb.is_tripped


def test_circuit_breaker_cooldown_expiry() -> None:
    from datetime import datetime, timedelta

    now = datetime.now()
    cb = CircuitBreaker(
        daily_loss_limit_pct=1.0,
        consecutive_loss_limit=3,
        cooldown_timedelta=timedelta(minutes=5),
    )
    cb.record_trade_result(Decimal("-50000"), Decimal("1000000"), now - timedelta(minutes=10))
    assert cb.is_tripped

    after_cooldown = now + timedelta(minutes=6)
    assert not cb.check(after_cooldown)
    assert not cb.is_tripped


def test_risk_statistics() -> None:
    stats = RiskStatistics()
    assert stats.total_evaluations == 0
    assert stats.avg_latency_ms == 0.0

    stats.record_evaluation(RiskDecision.APPROVE, 5.0, ())
    assert stats.total_evaluations == 1
    assert stats.approved_count == 1
    assert stats.approved_rate == 100.0

    stats.record_evaluation(RiskDecision.REJECT, 10.0, (RiskReason.MAX_EXPOSURE_EXCEEDED,))
    assert stats.total_evaluations == 2
    assert stats.rejected_count == 1
    assert round(stats.approved_rate, 1) == 50.0
    assert round(stats.avg_latency_ms, 1) == 7.5
    assert stats.max_latency_ms == 10.0

    stats.record_evaluation(RiskDecision.MODIFY, 3.0, ())
    assert stats.modified_count == 1

    stats.record_evaluation(RiskDecision.REDUCE, 7.0, (RiskReason.MAX_POSITION_EXCEEDED,))
    assert stats.reduced_count == 1
    assert stats.limit_hits["max_position_exceeded"] == 1

    snap = stats.snapshot()
    assert snap["total_evaluations"] == 4
    assert snap["limit_hits"]["max_position_exceeded"] == 1


def test_risk_verdict() -> None:
    v = RiskVerdict(
        decision=RiskDecision.REJECT,
        reasons=(RiskReason.MAX_EXPOSURE_EXCEEDED,),
        message="Exceeded limit",
        risk_level=RiskLevel.CRITICAL,
        suggested_quantity=Decimal("50"),
    )
    assert v.decision == RiskDecision.REJECT
    assert v.suggested_quantity == Decimal("50")
    assert v.risk_level == RiskLevel.CRITICAL


def test_risk_limits() -> None:
    limits = RiskLimits(
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
    assert limits.is_kernel

    overridden = limits.with_overrides(max_position_size_pct=10.0)
    assert overridden.max_position_size_pct == 10.0
    assert limits.max_position_size_pct == 5.0

    snap = limits.snapshot()
    assert snap["max_position_size_pct"] == 5.0
    assert snap["is_kernel"]


def test_signal() -> None:
    sig = Signal(
        signal_id="sig-001",
        symbol="AAPL",
        side="buy",
        quantity=Decimal("100"),
        confidence=0.85,
        prediction=0.02,
        prediction_quality=0.7,
        current_price=Decimal("150.0"),
        sector="Technology",
        volatility=0.25,
        avg_daily_volume=50_000_000.0,
    )
    assert sig.signal_id == "sig-001"
    assert sig.volatility == 0.25


def test_position_sizing_result() -> None:
    result = PositionSizingResult(
        quantity=Decimal("100"),
        sizing_method="kelly_criterion",
        reason="Kelly sizing",
        risk_per_trade=Decimal("500"),
        stop_loss=Decimal("148.0"),
    )
    assert result.quantity == Decimal("100")
    assert result.stop_loss == Decimal("148.0")
