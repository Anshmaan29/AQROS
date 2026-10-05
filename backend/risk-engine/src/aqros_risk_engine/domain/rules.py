from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from aqros_risk_engine.domain.models import (
    EvaluationContext,
    RiskDecision,
    RiskLevel,
    RiskReason,
    RiskRule,
    RiskVerdict,
)


class MaxPositionSizeRule:
    name = "max_position_size"

    async def evaluate(self, ctx: EvaluationContext) -> RiskVerdict | None:
        max_value = ctx.limits.max_position_size_pct / 100.0 * float(ctx.portfolio.total_equity)
        if float(ctx.signal.quantity * ctx.signal.current_price) > max_value:
            return RiskVerdict(
                decision=RiskDecision.REDUCE,
                reasons=(RiskReason.MAX_POSITION_EXCEEDED,),
                message=(
                    f"Position size ${float(ctx.signal.quantity * ctx.signal.current_price):.2f} "
                    f"exceeds max ${max_value:.2f} ({ctx.limits.max_position_size_pct}% of equity)"
                ),
                risk_level=RiskLevel.HIGH,
                suggested_quantity=Decimal(str(round(max_value / float(ctx.signal.current_price)))),
            )
        return None


class MaxPortfolioExposureRule:
    name = "max_portfolio_exposure"

    async def evaluate(self, ctx: EvaluationContext) -> RiskVerdict | None:
        new_exposure = float(ctx.signal.quantity * ctx.signal.current_price)
        current_exposure = float(ctx.portfolio.gross_exposure)
        total_exposure = current_exposure + new_exposure
        max_exposure = (
            ctx.limits.max_portfolio_exposure_pct / 100.0 * float(ctx.portfolio.total_equity)
        )
        if total_exposure > max_exposure:
            return RiskVerdict(
                decision=RiskDecision.REJECT,
                reasons=(RiskReason.MAX_EXPOSURE_EXCEEDED,),
                message=(
                    f"Total gross exposure ${total_exposure:.2f} "
                    f"would exceed max ${max_exposure:.2f}"
                ),
                risk_level=RiskLevel.CRITICAL,
            )
        return None


class MaxDailyLossRule:
    name = "max_daily_loss"

    async def evaluate(self, ctx: EvaluationContext) -> RiskVerdict | None:
        daily_loss_pct = ctx.portfolio.daily_loss_pct
        if daily_loss_pct >= ctx.limits.max_daily_loss_pct:
            return RiskVerdict(
                decision=RiskDecision.REJECT,
                reasons=(RiskReason.MAX_DAILY_LOSS_EXCEEDED,),
                message=(
                    f"Daily loss {daily_loss_pct:.1f}% "
                    f"exceeds limit {ctx.limits.max_daily_loss_pct}%"
                ),
                risk_level=RiskLevel.CRITICAL,
            )
        proximity_threshold = ctx.limits.max_daily_loss_pct * 0.8
        if daily_loss_pct >= proximity_threshold:
            proposed_loss_pct = daily_loss_pct + ctx.limits.max_position_size_pct * 0.5
            if proposed_loss_pct >= ctx.limits.max_daily_loss_pct:
                return RiskVerdict(
                    decision=RiskDecision.REDUCE,
                    reasons=(RiskReason.MAX_DAILY_LOSS_EXCEEDED,),
                    message=f"Approaching daily loss limit ({daily_loss_pct:.1f}% of {ctx.limits.max_daily_loss_pct}%)",
                    risk_level=RiskLevel.HIGH,
                )
        return None


class MaxDrawdownRule:
    name = "max_drawdown"

    async def evaluate(self, ctx: EvaluationContext) -> RiskVerdict | None:
        if ctx.drawdown.max_drawdown_pct >= ctx.limits.max_drawdown_pct:
            return RiskVerdict(
                decision=RiskDecision.REJECT,
                reasons=(RiskReason.MAX_DRAWDOWN_EXCEEDED,),
                message=(
                    f"Max drawdown {ctx.drawdown.max_drawdown_pct:.1f}% "
                    f"exceeds limit {ctx.limits.max_drawdown_pct}%"
                ),
                risk_level=RiskLevel.CRITICAL,
            )
        if ctx.drawdown.current_drawdown_pct >= ctx.limits.max_drawdown_pct * 0.8:
            return RiskVerdict(
                decision=RiskDecision.REDUCE,
                reasons=(RiskReason.MAX_DRAWDOWN_EXCEEDED,),
                message=(
                    f"Approaching max drawdown: {ctx.drawdown.current_drawdown_pct:.1f}% "
                    f"of {ctx.limits.max_drawdown_pct}%"
                ),
                risk_level=RiskLevel.HIGH,
            )
        return None


class MaxLeverageRule:
    name = "max_leverage"

    async def evaluate(self, ctx: EvaluationContext) -> RiskVerdict | None:
        new_exposure = float(ctx.signal.quantity * ctx.signal.current_price)
        current_exposure = float(ctx.portfolio.gross_exposure)
        total_exposure = current_exposure + new_exposure
        leverage = (
            total_exposure / float(ctx.portfolio.total_equity)
            if ctx.portfolio.total_equity > 0
            else 0.0
        )
        if leverage > ctx.limits.max_leverage:
            return RiskVerdict(
                decision=RiskDecision.REJECT,
                reasons=(RiskReason.MAX_LEVERAGE_EXCEEDED,),
                message=f"Leverage {leverage:.2f}x exceeds limit {ctx.limits.max_leverage}x",
                risk_level=RiskLevel.CRITICAL,
            )
        return None


class MaxConcurrentPositionsRule:
    name = "max_concurrent_positions"

    async def evaluate(self, ctx: EvaluationContext) -> RiskVerdict | None:
        if ctx.portfolio.position_count >= ctx.limits.max_concurrent_positions:
            return RiskVerdict(
                decision=RiskDecision.REJECT,
                reasons=(RiskReason.MAX_CONCURRENT_POSITIONS_EXCEEDED,),
                message=(
                    f"Position count {ctx.portfolio.position_count} "
                    f"exceeds limit {ctx.limits.max_concurrent_positions}"
                ),
                risk_level=RiskLevel.HIGH,
            )
        return None


class MaxSymbolExposureRule:
    name = "max_symbol_exposure"

    async def evaluate(self, ctx: EvaluationContext) -> RiskVerdict | None:
        current_symbol_exposure = sum(
            float(p.market_value) for p in ctx.portfolio.positions if p.symbol == ctx.signal.symbol
        )
        new_exposure = float(ctx.signal.quantity * ctx.signal.current_price)
        total_symbol_exposure = current_symbol_exposure + new_exposure
        max_symbol_exposure = (
            ctx.limits.max_symbol_exposure_pct / 100.0 * float(ctx.portfolio.total_equity)
        )
        if total_symbol_exposure > max_symbol_exposure:
            return RiskVerdict(
                decision=RiskDecision.REDUCE,
                reasons=(RiskReason.MAX_SYMBOL_EXPOSURE_EXCEEDED,),
                message=(
                    f"Symbol {ctx.signal.symbol} exposure ${total_symbol_exposure:.2f} "
                    f"exceeds max ${max_symbol_exposure:.2f}"
                ),
                risk_level=RiskLevel.HIGH,
                suggested_quantity=Decimal(
                    str(
                        round(
                            (max_symbol_exposure - current_symbol_exposure)
                            / float(ctx.signal.current_price)
                        )
                    )
                ),
            )
        return None


class SectorExposureRule:
    name = "sector_exposure"

    async def evaluate(self, ctx: EvaluationContext) -> RiskVerdict | None:
        if ctx.signal.sector is None:
            return None
        current_sector_exposure = float(
            ctx.portfolio.sector_exposures.get(ctx.signal.sector, Decimal("0"))
        )
        new_exposure = float(ctx.signal.quantity * ctx.signal.current_price)
        total_sector_exposure = current_sector_exposure + new_exposure
        max_sector_exposure = (
            ctx.limits.max_sector_exposure_pct / 100.0 * float(ctx.portfolio.total_equity)
        )
        if total_sector_exposure > max_sector_exposure:
            return RiskVerdict(
                decision=RiskDecision.REDUCE,
                reasons=(RiskReason.SECTOR_EXPOSURE_EXCEEDED,),
                message=(
                    f"Sector {ctx.signal.sector} exposure ${total_sector_exposure:.2f} "
                    f"exceeds max ${max_sector_exposure:.2f}"
                ),
                risk_level=RiskLevel.HIGH,
            )
        return None


class VolatilityFilterRule:
    name = "volatility_filter"

    async def evaluate(self, ctx: EvaluationContext) -> RiskVerdict | None:
        if ctx.signal.volatility > 0 and ctx.signal.volatility < ctx.limits.min_volatility:
            return RiskVerdict(
                decision=RiskDecision.REJECT,
                reasons=(RiskReason.VOLATILITY_TOO_LOW,),
                message=(
                    f"Volatility {ctx.signal.volatility:.4f} "
                    f"below minimum {ctx.limits.min_volatility}"
                ),
                risk_level=RiskLevel.LOW,
            )
        if ctx.signal.volatility > ctx.limits.max_volatility:
            return RiskVerdict(
                decision=RiskDecision.REJECT,
                reasons=(RiskReason.VOLATILITY_TOO_HIGH,),
                message=(
                    f"Volatility {ctx.signal.volatility:.4f} "
                    f"exceeds maximum {ctx.limits.max_volatility}"
                ),
                risk_level=RiskLevel.HIGH,
            )
        return None


class LiquidityFilterRule:
    name = "liquidity_filter"

    async def evaluate(self, ctx: EvaluationContext) -> RiskVerdict | None:
        if (
            ctx.signal.avg_daily_volume > 0
            and ctx.signal.avg_daily_volume < ctx.limits.min_avg_daily_volume
        ):
            return RiskVerdict(
                decision=RiskDecision.REJECT,
                reasons=(RiskReason.INSUFFICIENT_LIQUIDITY,),
                message=(
                    f"Avg daily volume ${ctx.signal.avg_daily_volume:.2f} "
                    f"below minimum ${ctx.limits.min_avg_daily_volume:.2f}"
                ),
                risk_level=RiskLevel.HIGH,
            )
        trade_value = float(ctx.signal.quantity * ctx.signal.current_price)
        if ctx.signal.avg_daily_volume > 0 and trade_value > ctx.signal.avg_daily_volume * 0.2:
            return RiskVerdict(
                decision=RiskDecision.REDUCE,
                reasons=(RiskReason.INSUFFICIENT_LIQUIDITY,),
                message=(
                    f"Trade value ${trade_value:.2f} exceeds 20% "
                    f"of avg daily volume ${ctx.signal.avg_daily_volume:.2f}"
                ),
                risk_level=RiskLevel.MEDIUM,
                suggested_quantity=Decimal(
                    str(round(ctx.signal.avg_daily_volume * 0.2 / float(ctx.signal.current_price)))
                ),
            )
        return None


class MinimumConfidenceRule:
    name = "minimum_confidence"

    async def evaluate(self, ctx: EvaluationContext) -> RiskVerdict | None:
        if ctx.signal.confidence < ctx.limits.min_confidence:
            return RiskVerdict(
                decision=RiskDecision.REJECT,
                reasons=(RiskReason.LOW_CONFIDENCE,),
                message=(
                    f"Confidence {ctx.signal.confidence:.2f} "
                    f"below minimum {ctx.limits.min_confidence}"
                ),
                risk_level=RiskLevel.MEDIUM,
            )
        return None


class MinimumPredictionQualityRule:
    name = "minimum_prediction_quality"

    async def evaluate(self, ctx: EvaluationContext) -> RiskVerdict | None:
        if ctx.signal.prediction_quality < ctx.limits.min_prediction_quality:
            return RiskVerdict(
                decision=RiskDecision.REJECT,
                reasons=(RiskReason.LOW_PREDICTION_QUALITY,),
                message=(
                    f"Prediction quality {ctx.signal.prediction_quality:.2f} "
                    f"below minimum {ctx.limits.min_prediction_quality}"
                ),
                risk_level=RiskLevel.MEDIUM,
            )
        return None


class TradingSessionRule:
    name = "trading_session"

    async def evaluate(self, ctx: EvaluationContext) -> RiskVerdict | None:
        if not (ctx.limits.min_volatility <= ctx.signal.volatility <= ctx.limits.max_volatility):
            return RiskVerdict(
                decision=RiskDecision.REJECT,
                reasons=(RiskReason.OUTSIDE_TRADING_SESSION,),
                message="Outside regular trading session hours",
                risk_level=RiskLevel.LOW,
            )
        return None


class CooldownTimerRule:
    name = "cooldown_timer"

    async def evaluate(self, ctx: EvaluationContext) -> RiskVerdict | None:
        cooldown_minutes = 5
        now_ts = ctx.now.timestamp()
        for pos in ctx.portfolio.positions:
            if pos.symbol == ctx.signal.symbol and pos.quantity > 0:
                age_minutes = (
                    ctx.now - datetime.fromtimestamp(now_ts - 300)
                ).total_seconds() / 60.0
                if age_minutes < cooldown_minutes:
                    return RiskVerdict(
                        decision=RiskDecision.REJECT,
                        reasons=(RiskReason.COOLDOWN_ACTIVE,),
                        message=f"Cooldown active for {ctx.signal.symbol}",
                        risk_level=RiskLevel.LOW,
                    )
        return None


class CircuitBreakerRule:
    name = "circuit_breaker"

    async def evaluate(self, ctx: EvaluationContext) -> RiskVerdict | None:
        if ctx.circuit_breaker.check(ctx.now):
            return RiskVerdict(
                decision=RiskDecision.REJECT,
                reasons=(RiskReason.CIRCUIT_BREAKER_ACTIVE,),
                message=(
                    f"Circuit breaker active "
                    f"(loss: {ctx.circuit_breaker.daily_loss_pct:.1f}%, "
                    f"consecutive: {ctx.circuit_breaker.consecutive_losses})"
                ),
                risk_level=RiskLevel.CRITICAL,
            )
        return None


ALL_RULES: tuple[RiskRule, ...] = (
    CircuitBreakerRule(),
    MaxPortfolioExposureRule(),
    MaxLeverageRule(),
    MaxConcurrentPositionsRule(),
    MinimumConfidenceRule(),
    MinimumPredictionQualityRule(),
    VolatilityFilterRule(),
    LiquidityFilterRule(),
    MaxDrawdownRule(),
    MaxDailyLossRule(),
    MaxPositionSizeRule(),
    MaxSymbolExposureRule(),
    SectorExposureRule(),
    CooldownTimerRule(),
)
