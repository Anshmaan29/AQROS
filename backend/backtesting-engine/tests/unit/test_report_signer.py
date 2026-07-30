from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from aqros_backtesting_engine.adapters.report_signer import (
    generate_report,
    sign_report,
    verify_report,
)
from aqros_backtesting_engine.domain.models import (
    BacktestResult,
    BenchmarkComparison,
    CashLedger,
    DrawdownSummary,
    EquityPoint,
    PerformanceMetrics,
    Portfolio,
    Position,
    RiskMetrics,
    RunManifest,
    RunStatus,
)


def _result() -> BacktestResult:
    run_uuid = uuid4()
    now = datetime.now(UTC)
    return BacktestResult(
        run_uuid=run_uuid,
        manifest=RunManifest(
            run_uuid=run_uuid,
            engine_git_commit="abc",
            strategy_core_git_commit="def",
            configuration=None,  # type: ignore[arg-type]
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
        equity_curve=(
            EquityPoint(now, Decimal("100000")),
            EquityPoint(now + timedelta(days=1), Decimal("101000")),
        ),
        drawdown=DrawdownSummary(Decimal("0.05"), now, now, timedelta(days=3)),
        performance=PerformanceMetrics(
            total_return=Decimal("0.01"),
            annualized_return=Decimal("0.12"),
            sharpe_ratio=Decimal("1.5"),
            sortino_ratio=Decimal("2.0"),
            win_rate=Decimal("0.6"),
        ),
        risk=RiskMetrics(
            volatility=Decimal("0.15"),
            max_drawdown=Decimal("0.05"),
            value_at_risk=Decimal("0.02"),
            gross_exposure=Decimal("1.0"),
            net_exposure=Decimal("0.5"),
        ),
        benchmark=BenchmarkComparison(
            benchmark_symbol="SPY",
            benchmark_return=Decimal("0.005"),
            excess_return=Decimal("0.005"),
        ),
        final_portfolio=Portfolio(
            cash=CashLedger(Decimal("50000"), Decimal("100000")),
            positions=(Position("AAPL", Decimal("100"), Decimal("150"), Decimal("1000")),),
            as_of=now,
        ),
        failure_reason=None,
    )


class TestGenerateReport:
    async def test_contains_all_top_level_keys(self) -> None:
        result = _result()
        report = generate_report(result)
        expected_keys = {
            "run_uuid",
            "status",
            "generated_at",
            "performance",
            "risk",
            "drawdown",
            "benchmark",
            "final_portfolio",
            "failure_reason",
            "trade_log_count",
            "equity_curve_points",
        }
        assert expected_keys.issubset(report.keys())

    async def test_trade_log_count(self) -> None:
        report = generate_report(_result())
        assert report["trade_log_count"] == 0

    async def test_equity_curve_points(self) -> None:
        report = generate_report(_result())
        assert report["equity_curve_points"] == 2

    async def test_status_is_completed(self) -> None:
        report = generate_report(_result())
        assert report["status"] == "completed"

    async def test_run_uuid_is_string(self) -> None:
        report = generate_report(_result())
        assert isinstance(report["run_uuid"], str)

    async def test_generated_at_is_isoformat(self) -> None:
        report = generate_report(_result())
        assert "T" in str(report["generated_at"])

    async def test_performance_has_numeric_values(self) -> None:
        report = generate_report(_result())
        perf = report["performance"]
        assert isinstance(perf["total_return"], float)
        assert isinstance(perf["sharpe_ratio"], float)

    async def test_benchmark_included_when_present(self) -> None:
        report = generate_report(_result())
        assert report["benchmark"] is not None
        assert report["benchmark"]["benchmark_symbol"] == "SPY"

    async def test_failure_reason_is_none(self) -> None:
        report = generate_report(_result())
        assert report["failure_reason"] is None

    async def test_no_benchmark_when_none(self) -> None:
        result = _result()
        result = BacktestResult(
            run_uuid=result.run_uuid,
            manifest=result.manifest,
            status=result.status,
            trade_log=result.trade_log,
            equity_curve=result.equity_curve,
            drawdown=result.drawdown,
            performance=result.performance,
            risk=result.risk,
            benchmark=None,
            final_portfolio=result.final_portfolio,
            failure_reason=result.failure_reason,
        )
        report = generate_report(result)
        assert report["benchmark"] is None


class TestSignReport:
    async def test_signature_format(self) -> None:
        report = generate_report(_result())
        sig = sign_report(report, b"test-key")
        assert sig.startswith("v1:")
        assert len(sig) > 10

    async def test_same_report_same_key_produces_same_signature(self) -> None:
        report = generate_report(_result())
        sig1 = sign_report(report, b"test-key")
        sig2 = sign_report(report, b"test-key")
        assert sig1 == sig2

    async def test_different_keys_produce_different_signatures(self) -> None:
        report = generate_report(_result())
        sig1 = sign_report(report, b"key-1")
        sig2 = sign_report(report, b"key-2")
        assert sig1 != sig2

    async def test_different_reports_different_signatures(self) -> None:
        report1 = generate_report(_result())
        result2 = _result()
        result2 = BacktestResult(
            run_uuid=uuid4(),
            manifest=result2.manifest,
            status=RunStatus.FAILED,
            trade_log=result2.trade_log,
            equity_curve=result2.equity_curve,
            drawdown=result2.drawdown,
            performance=result2.performance,
            risk=result2.risk,
            benchmark=result2.benchmark,
            final_portfolio=result2.final_portfolio,
            failure_reason="test failure",
        )
        report2 = generate_report(result2)
        sig1 = sign_report(report1, b"key")
        sig2 = sign_report(report2, b"key")
        assert sig1 != sig2


class TestVerifyReport:
    async def test_verifies_valid_signature(self) -> None:
        report = generate_report(_result())
        sig = sign_report(report, b"test-key")
        assert verify_report(report, sig, b"test-key") is True

    async def test_rejects_tampered_report(self) -> None:
        report = generate_report(_result())
        sig = sign_report(report, b"test-key")
        report["status"] = "tampered"
        assert verify_report(report, sig, b"test-key") is False

    async def test_rejects_wrong_key(self) -> None:
        report = generate_report(_result())
        sig = sign_report(report, b"key-1")
        assert verify_report(report, sig, b"key-2") is False

    async def test_rejects_malformed_signature(self) -> None:
        report = generate_report(_result())
        assert verify_report(report, "bad-format", b"key") is False

    async def test_rejects_empty_signature(self) -> None:
        report = generate_report(_result())
        assert verify_report(report, "", b"key") is False
