from __future__ import annotations

from decimal import Decimal

from hypothesis import assume, given
from hypothesis import strategies as st

from aqros_risk_engine.domain.models import (
    DrawdownTracker,
    PortfolioExposure,
    PositionExposure,
    RiskDecision,
    RiskStatistics,
)

EQUITY = st.decimals(min_value=100_000, max_value=100_000_000)
QUANTITY = st.integers(min_value=1, max_value=1_000_000)
PRICE = st.decimals(min_value=0.01, max_value=10_000)
VOLUME = st.floats(min_value=1_000, max_value=1_000_000_000)
PERCENTAGE = st.floats(min_value=0.0, max_value=100.0)
LATENCY = st.floats(min_value=0.0, max_value=10_000.0)
CONFIDENCE = st.floats(min_value=0.0, max_value=1.0)


@given(EQUITY)
def test_drawdown_never_exceeds_peak(equity: Decimal) -> None:
    assume(equity > 0)
    dd = DrawdownTracker(peak_equity=equity)
    dd.update(equity)
    assert dd.current_drawdown_pct == 0.0, "Drawdown should be 0 at peak"
    assert dd.max_drawdown_pct == 0.0


@given(EQUITY, EQUITY)
def test_drawdown_bounded(peak: Decimal, current: Decimal) -> None:
    assume(peak > 0 and current > 0)
    dd = DrawdownTracker(peak_equity=peak)
    dd.update(current)
    assert dd.current_drawdown_pct >= 0.0, "Drawdown should never be negative"
    assert dd.current_drawdown_pct <= 100.0, "Drawdown should never exceed 100%"
    assert dd.max_drawdown_pct >= 0.0
    assert dd.max_drawdown_pct <= 100.0


@given(st.lists(PERCENTAGE, min_size=1, max_size=50))
def test_risk_statistics_accumulation(latencies: list[float]) -> None:
    stats = RiskStatistics()
    assert stats.total_evaluations == 0
    assert stats.avg_latency_ms == 0.0

    decisions = [
        RiskDecision.APPROVE,
        RiskDecision.REJECT,
        RiskDecision.MODIFY,
        RiskDecision.REDUCE,
    ]
    for idx, lat in enumerate(latencies):
        decision = decisions[idx % len(decisions)]
        stats.record_evaluation(decision, lat, ())
        assert stats.total_evaluations == idx + 1

    assert stats.total_evaluations == len(latencies)
    assert (
        stats.approved_count + stats.rejected_count + stats.modified_count + stats.reduced_count
        == len(latencies)
    )
    assert stats.max_latency_ms == max(latencies) if latencies else 0.0
    assert 0 <= stats.approved_rate <= 100.0

    if len(latencies) > 0:
        expected_avg = sum(latencies) / len(latencies)
        assert abs(stats.avg_latency_ms - expected_avg) < 0.01


@given(st.integers(min_value=1, max_value=1000))
def test_risk_statistics_consumer_loop(count: int) -> None:
    stats = RiskStatistics()
    for _i in range(count):
        stats.record_evaluation(RiskDecision.APPROVE, 1.0, ())
    assert stats.total_evaluations == count
    assert stats.approved_count == count
    assert stats.approved_rate == 100.0

    snap = stats.snapshot()
    assert snap["total_evaluations"] == count
    assert snap["approved_rate"] == 100.0


@given(EQUITY, EQUITY)
def test_portfolio_exposure_pct_bounds(equity: Decimal, exposure: Decimal) -> None:
    assume(equity > 0)
    pct = float(exposure / equity) * 100.0 if equity > 0 else 0.0
    if pct > 0:
        pe = PortfolioExposure(
            total_equity=equity,
            total_market_value=exposure,
            gross_exposure=exposure,
            net_exposure=exposure,
            cash=equity - exposure,
            leverage=pct / 100.0,
        )
        assert pe.gross_exposure_pct >= 0.0
        assert pe.gross_exposure_pct <= float("inf")
        assert abs(pe.gross_exposure_pct - pct) < 0.01


@given(st.integers(min_value=0, max_value=100))
def test_concurrent_positions_bounded(count: int) -> None:
    pos = tuple(
        PositionExposure(
            symbol=f"S{i}",
            quantity=Decimal("10"),
            avg_entry_price=Decimal("100"),
            current_price=Decimal("100"),
            market_value=Decimal("1000"),
            unrealized_pnl=Decimal("0"),
            realized_pnl=Decimal("0"),
        )
        for i in range(count)
    )
    pe = PortfolioExposure(
        total_equity=Decimal("1000000"),
        total_market_value=Decimal(str(count * 1000)),
        gross_exposure=Decimal(str(count * 1000)),
        net_exposure=Decimal(str(count * 1000)),
        cash=Decimal("1000000") - Decimal(str(count * 1000)),
        leverage=float(count * 1000) / 1_000_000.0,
        positions=pos,
    )
    assert pe.position_count == count
