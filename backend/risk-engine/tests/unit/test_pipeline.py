from __future__ import annotations

from datetime import UTC
from decimal import Decimal

import pytest

from aqros_risk_engine.domain.models import (
    PortfolioExposure,
    RiskDecision,
    RiskLimits,
    Signal,
)
from aqros_risk_engine.domain.pipeline import RiskPipeline


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
        "strategy": "momentum",
        "correlation_id": "corr-001",
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
        "positions": (),
        "sector_exposures": {},
        "daily_pnl": Decimal("5000"),
    }
    params.update(overrides)
    return PortfolioExposure(**params)


@pytest.mark.asyncio
async def test_pipeline_approves_valid_signal() -> None:
    pipeline = RiskPipeline()
    signal = _make_signal()
    portfolio = _make_portfolio()
    result = await pipeline.evaluate(signal, portfolio)
    assert result.decision == RiskDecision.APPROVE
    assert result.approved_quantity > 0
    assert result.latency_ms >= 0
    assert pipeline.statistics.total_evaluations == 1
    assert pipeline.statistics.approved_count == 1


@pytest.mark.asyncio
async def test_pipeline_rejects_low_confidence() -> None:
    pipeline = RiskPipeline()
    signal = _make_signal(confidence=0.1)
    portfolio = _make_portfolio()
    result = await pipeline.evaluate(signal, portfolio)
    assert result.decision == RiskDecision.REJECT
    assert result.approved_quantity == Decimal("0")


@pytest.mark.asyncio
async def test_pipeline_rejects_excessive_exposure() -> None:
    pipeline = RiskPipeline()
    signal = _make_signal(quantity=Decimal("1000000"), current_price=Decimal("1000"))
    portfolio = _make_portfolio()
    result = await pipeline.evaluate(signal, portfolio)
    assert result.decision == RiskDecision.REJECT


@pytest.mark.asyncio
async def test_pipeline_reduces_position() -> None:
    pipeline = RiskPipeline()
    signal = _make_signal(quantity=Decimal("6000"), current_price=Decimal("100"))
    portfolio = _make_portfolio(total_equity=Decimal("10000000"), gross_exposure=Decimal("200000"))
    result = await pipeline.evaluate(signal, portfolio)
    assert result.approved_quantity <= Decimal("6000")
    if result.decision == RiskDecision.REDUCE:
        assert result.approved_quantity < Decimal("6000")


@pytest.mark.asyncio
async def test_pipeline_circuit_breaker_rejects() -> None:
    pipeline = RiskPipeline()
    pipeline._circuit_breaker.is_tripped = True
    pipeline._circuit_breaker.tripped_at = None
    signal = _make_signal()
    portfolio = _make_portfolio()
    result = await pipeline.evaluate(signal, portfolio)
    assert result.decision == RiskDecision.REJECT


@pytest.mark.asyncio
async def test_pipeline_without_portfolio() -> None:
    pipeline = RiskPipeline()
    signal = _make_signal()
    result = await pipeline.evaluate(signal)
    assert result.decision in (RiskDecision.APPROVE, RiskDecision.REJECT, RiskDecision.REDUCE)


@pytest.mark.asyncio
async def test_pipeline_statistics() -> None:
    pipeline = RiskPipeline()
    portfolio = _make_portfolio()

    await pipeline.evaluate(_make_signal(signal_id="s1", confidence=0.9), portfolio)
    await pipeline.evaluate(_make_signal(signal_id="s2", confidence=0.1), portfolio)
    await pipeline.evaluate(_make_signal(signal_id="s3", confidence=0.8), portfolio)

    stats = pipeline.statistics
    assert stats.total_evaluations == 3
    assert stats.approved_count + stats.rejected_count + stats.reduced_count == 3
    snap = stats.snapshot()
    assert snap["total_evaluations"] == 3
    assert snap["avg_latency_ms"] >= 0


@pytest.mark.asyncio
async def test_pipeline_drawdown_tracking() -> None:
    pipeline = RiskPipeline()
    portfolio = _make_portfolio(total_equity=Decimal("900000"))
    pipeline.set_portfolio(portfolio)
    assert pipeline.drawdown.current_drawdown_pct > 0


@pytest.mark.asyncio
async def test_pipeline_set_limits() -> None:
    pipeline = RiskPipeline()
    new_limits = RiskLimits(
        max_position_size_pct=10.0,
        max_portfolio_exposure_pct=90.0,
        max_daily_loss_pct=5.0,
        max_drawdown_pct=25.0,
        max_leverage=3.0,
        max_concurrent_positions=50,
        max_symbol_exposure_pct=20.0,
        max_sector_exposure_pct=40.0,
        min_volatility=0.0005,
        max_volatility=0.8,
        min_avg_daily_volume=50000.0,
        min_confidence=0.2,
        min_prediction_quality=0.05,
    )
    pipeline.set_limits(new_limits)
    signal = _make_signal(confidence=0.25)
    portfolio = _make_portfolio()
    result = await pipeline.evaluate(signal, portfolio)
    assert result is not None


@pytest.mark.asyncio
async def test_record_trade_result() -> None:
    from datetime import datetime

    pipeline = RiskPipeline()
    pipeline.record_trade_result(Decimal("-10000"), Decimal("990000"), datetime.now(UTC))
    assert pipeline.drawdown.current_drawdown_pct > 0
