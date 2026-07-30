from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from aqros_backtesting_engine.domain.models import (
    BacktestResult,
    CashLedger,
    DrawdownSummary,
    EquityPoint,
    PerformanceMetrics,
    Portfolio,
    RiskMetrics,
    RunManifest,
    RunStatus,
)
from aqros_backtesting_engine.domain.validation import (
    ValidationResult,
    compute_dsr,
    compute_pbo,
)


def _result(
    equity_values: list[Decimal],
    sharpe: Decimal | None = Decimal("1.0"),
) -> BacktestResult:
    run_uuid = uuid4()
    now = datetime.now(UTC)
    curve = tuple(EquityPoint(now + timedelta(days=i), v) for i, v in enumerate(equity_values))
    return BacktestResult(
        run_uuid=run_uuid,
        manifest=RunManifest(
            run_uuid=run_uuid,
            engine_git_commit="abc",
            strategy_core_git_commit="def",
            configuration=None,
            resolved_models=(),
            feature_versions={},
            universe=(),
            period_start=now,
            period_end=now,
            knowledge_time_boundary=now,
            calendar_source="test",
            price_adjustment_convention="raw",
            corporate_actions_applied=(),
            corporate_actions_unavailable=(),
            library_versions={},
            seed=42,
        ),
        status=RunStatus.COMPLETED,
        trade_log=(),
        equity_curve=curve,
        drawdown=DrawdownSummary(Decimal("0"), now, now, timedelta()),
        performance=PerformanceMetrics(
            total_return=Decimal("0"),
            annualized_return=Decimal("0"),
            sharpe_ratio=sharpe,
            sortino_ratio=None,
            win_rate=Decimal("0"),
        ),
        risk=RiskMetrics(Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")),
        benchmark=None,
        final_portfolio=Portfolio(CashLedger(Decimal("0"), Decimal("0")), (), now),
        failure_reason=None,
    )


class TestComputePBO:
    async def test_single_result_returns_05(self) -> None:
        results = [_result([Decimal("100"), Decimal("110")])]
        assert compute_pbo(results) == 0.5

    async def test_no_results_returns_05(self) -> None:
        assert compute_pbo([]) == 0.5

    async def test_best_is_is_best_oos(self) -> None:
        r1 = _result([Decimal("100"), Decimal("110")])
        r2 = _result([Decimal("100"), Decimal("95")])
        pbo = compute_pbo([r1, r2], in_sample_ratio=0.5)
        assert 0.0 <= pbo <= 1.0

    async def test_short_curves_return_05(self) -> None:
        results = [_result([Decimal("100")])]
        assert compute_pbo(results) == 0.5


class TestComputeDSR:
    async def test_empty_list_returns_zero(self) -> None:
        assert compute_dsr([]) == 0.0

    async def test_single_sharpe(self) -> None:
        dsr = compute_dsr([1.0], num_observations=252)
        assert 0.0 < dsr <= 1.0

    async def test_high_sharpe_ratio_gives_high_dsr(self) -> None:
        dsr_low = compute_dsr([0.1], num_observations=252)
        dsr_high = compute_dsr([3.0], num_observations=252)
        assert dsr_high > dsr_low

    async def test_dsr_bounds(self) -> None:
        dsr = compute_dsr([0.5, 1.0, 1.5], num_observations=252)
        assert 0.0 <= dsr <= 1.0

    async def test_many_trials_reduces_dsr(self) -> None:
        dsr_few = compute_dsr([1.0], num_observations=252)
        dsr_many = compute_dsr([1.0, 1.01, 0.99, 1.02, 0.98], num_observations=252)
        assert dsr_many <= dsr_few + 0.01


class TestValidationResult:
    async def test_default_sharpe_ratios_empty(self) -> None:
        vr = ValidationResult(
            pbo=0.5,
            dsr=0.5,
            cpcv_mean_return=Decimal("0"),
            cpcv_std_return=Decimal("0"),
            num_trials=0,
        )
        assert vr.sharpe_ratios == ()

    async def test_stores_metrics(self) -> None:
        vr = ValidationResult(
            pbo=0.3,
            dsr=0.8,
            cpcv_mean_return=Decimal("0.01"),
            cpcv_std_return=Decimal("0.02"),
            num_trials=10,
            sharpe_ratios=(0.5, 1.0, 1.5),
        )
        assert vr.pbo == 0.3
        assert vr.dsr == 0.8
        assert vr.cpcv_mean_return == Decimal("0.01")
        assert vr.cpcv_std_return == Decimal("0.02")
        assert vr.num_trials == 10
        assert len(vr.sharpe_ratios) == 3
