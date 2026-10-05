from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from aqros_risk_engine.domain.models import (
    AccountLimits,
    CircuitBreaker,
    DrawdownTracker,
    EvaluationContext,
    PortfolioExposure,
    PositionSizingResult,
    PositionSizingStrategy,
    RiskDecision,
    RiskLevel,
    RiskLimits,
    RiskRule,
    RiskStatistics,
    RiskVerdict,
    Signal,
)
from aqros_risk_engine.domain.rules import ALL_RULES
from aqros_risk_engine.domain.sizing import PercentageOfEquitySizing


@dataclass
class RiskPipelineResult:
    signal: Signal
    decision: RiskDecision
    reasons: tuple[str, ...]
    message: str
    risk_level: RiskLevel
    approved_quantity: Decimal
    suggested_quantity: Decimal | None = None
    stop_loss: Decimal | None = None
    sizing_result: PositionSizingResult | None = None
    latency_ms: float = 0.0
    rule_results: list[RiskVerdict] = field(default_factory=list)


class RiskPipeline:
    def __init__(
        self,
        rules: Sequence[RiskRule] | None = None,
        sizing_strategy: PositionSizingStrategy | None = None,
        limits: RiskLimits | None = None,
        account_limits: AccountLimits | None = None,
    ) -> None:
        self._rules = list(rules) if rules is not None else list(ALL_RULES)
        self._sizing = sizing_strategy or PercentageOfEquitySizing()
        self._limits = limits or RiskLimits(
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
        self._stats = RiskStatistics()
        self._drawdown = DrawdownTracker(peak_equity=Decimal("1000000"))
        self._circuit_breaker = CircuitBreaker(
            daily_loss_limit_pct=3.0,
            consecutive_loss_limit=3,
            cooldown_timedelta=timedelta(seconds=60),
        )

    @property
    def statistics(self) -> RiskStatistics:
        return self._stats

    @property
    def drawdown(self) -> DrawdownTracker:
        return self._drawdown

    @property
    def circuit_breaker(self) -> CircuitBreaker:
        return self._circuit_breaker

    @property
    def limits(self) -> RiskLimits:
        return self._limits

    def set_limits(self, limits: RiskLimits) -> None:
        self._limits = limits

    def set_sizing_strategy(self, strategy: PositionSizingStrategy) -> None:
        self._sizing = strategy

    def set_portfolio(self, portfolio: PortfolioExposure) -> None:
        self._drawdown.update(portfolio.total_equity)
        self._circuit_breaker.daily_loss_pct = portfolio.daily_loss_pct

    def record_trade_result(self, pnl: Decimal, equity: Decimal, now: datetime) -> None:
        self._drawdown.update(equity)
        self._circuit_breaker.record_trade_result(pnl, equity, now)

    async def evaluate(
        self, signal: Signal, portfolio: PortfolioExposure | None = None
    ) -> RiskPipelineResult:
        from datetime import datetime

        start = time.monotonic()
        now = datetime.now(UTC)
        portfolio = portfolio or PortfolioExposure(
            total_equity=Decimal("1000000"),
            total_market_value=Decimal("0"),
            gross_exposure=Decimal("0"),
            net_exposure=Decimal("0"),
            cash=Decimal("1000000"),
            leverage=0.0,
        )
        self._drawdown.update(portfolio.total_equity)

        ctx = EvaluationContext(
            signal=signal,
            portfolio=portfolio,
            limits=self._limits,
            drawdown=self._drawdown,
            circuit_breaker=self._circuit_breaker,
            now=now,
        )

        rule_results: list[RiskVerdict] = []
        final_decision = RiskDecision.APPROVE
        reasons: list[str] = []
        risk_level = RiskLevel.LOW
        suggested_qty: Decimal | None = None

        for rule in self._rules:
            verdict = await rule.evaluate(ctx)
            if verdict is not None:
                rule_results.append(verdict)
                reasons.extend(str(r) for r in verdict.reasons)
                if verdict.risk_level.value > risk_level.value:
                    risk_level = verdict.risk_level
                if verdict.decision == RiskDecision.REJECT:
                    final_decision = RiskDecision.REJECT
                    break
                if verdict.decision == RiskDecision.REDUCE:
                    final_decision = RiskDecision.REDUCE
                    if verdict.suggested_quantity is not None:
                        suggested_qty = verdict.suggested_quantity
                elif (
                    verdict.decision == RiskDecision.MODIFY
                    and final_decision != RiskDecision.REDUCE
                ):
                    final_decision = RiskDecision.MODIFY

        if final_decision == RiskDecision.REJECT:
            latency_ms = (time.monotonic() - start) * 1000
            self._stats.record_evaluation(final_decision, latency_ms, ())
            return RiskPipelineResult(
                signal=signal,
                decision=RiskDecision.REJECT,
                reasons=tuple(reasons[:5]),
                message=reasons[0] if reasons else "Rejected by risk rules",
                risk_level=risk_level,
                approved_quantity=Decimal("0"),
                latency_ms=latency_ms,
                rule_results=rule_results,
            )

        sizing_result = await self._sizing.size(signal, ctx)
        approved_qty = sizing_result.quantity

        if suggested_qty is not None and approved_qty > suggested_qty:
            approved_qty = suggested_qty
            final_decision = RiskDecision.REDUCE

        if approved_qty <= 0:
            final_decision = RiskDecision.REJECT
            latency_ms = (time.monotonic() - start) * 1000
            self._stats.record_evaluation(final_decision, latency_ms, ())
            return RiskPipelineResult(
                signal=signal,
                decision=RiskDecision.REJECT,
                reasons=("Sizing resulted in zero quantity",),
                message="Sizing resulted in zero quantity",
                risk_level=RiskLevel.LOW,
                approved_quantity=Decimal("0"),
                latency_ms=latency_ms,
                rule_results=rule_results,
            )

        latency_ms = (time.monotonic() - start) * 1000
        self._stats.record_evaluation(final_decision, latency_ms, ())

        message = (
            f"Approved {approved_qty} shares of {signal.symbol}"
            if final_decision == RiskDecision.APPROVE
            else f"Reduced: {reasons[0] if reasons else 'position reduced'}"
        )

        return RiskPipelineResult(
            signal=signal,
            decision=final_decision,
            reasons=tuple(reasons[:5]),
            message=message,
            risk_level=risk_level,
            approved_quantity=approved_qty,
            suggested_quantity=suggested_qty,
            stop_loss=sizing_result.stop_loss,
            sizing_result=sizing_result,
            latency_ms=latency_ms,
            rule_results=rule_results,
        )
