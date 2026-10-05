from __future__ import annotations

from decimal import Decimal

from aqros_risk_engine.domain.models import (
    EvaluationContext,
    PositionSizingResult,
    Signal,
)


class FixedQuantitySizing:
    name = "fixed_quantity"

    def __init__(self, quantity: Decimal = Decimal("100")) -> None:
        self._quantity = quantity

    async def size(self, signal: Signal, ctx: EvaluationContext) -> PositionSizingResult:
        return PositionSizingResult(
            quantity=self._quantity,
            sizing_method=self.name,
            reason=f"Fixed quantity {self._quantity}",
        )


class FixedDollarSizing:
    name = "fixed_dollar"

    def __init__(self, dollar_amount: Decimal = Decimal("10000")) -> None:
        self._dollar_amount = dollar_amount

    async def size(self, signal: Signal, ctx: EvaluationContext) -> PositionSizingResult:
        price = signal.current_price
        if price == 0:
            return PositionSizingResult(
                quantity=Decimal("0"),
                sizing_method=self.name,
                reason="Zero price, quantity set to 0",
            )
        quantity = int(self._dollar_amount / price)
        return PositionSizingResult(
            quantity=Decimal(str(quantity)),
            sizing_method=self.name,
            reason=f"Fixed ${float(self._dollar_amount):.2f} at ${float(price):.2f}",
        )


class PercentageOfEquitySizing:
    name = "percentage_of_equity"

    def __init__(self, percentage: float = 2.0) -> None:
        self._percentage = percentage

    async def size(self, signal: Signal, ctx: EvaluationContext) -> PositionSizingResult:
        equity = ctx.portfolio.total_equity
        price = signal.current_price
        if price == 0 or equity == 0:
            return PositionSizingResult(
                quantity=Decimal("0"),
                sizing_method=self.name,
                reason="Zero price or equity, quantity set to 0",
            )
        dollar_amount = float(equity) * (self._percentage / 100.0)
        quantity = int(dollar_amount / float(price))
        return PositionSizingResult(
            quantity=Decimal(str(quantity)),
            sizing_method=self.name,
            reason=f"{self._percentage}% of equity (${dollar_amount:.2f}) at ${float(price):.2f}",
        )


class KellyCriterionSizing:
    name = "kelly_criterion"

    def __init__(self, kelly_fraction: float = 0.25) -> None:
        self._kelly_fraction = kelly_fraction

    async def size(self, signal: Signal, ctx: EvaluationContext) -> PositionSizingResult:
        win_prob = max(min(signal.confidence, 0.99), 0.01)
        odds = abs(signal.prediction) if abs(signal.prediction) > 0 else 0.01
        kelly_pct = (win_prob * odds - (1 - win_prob)) / odds
        kelly_pct = max(0.0, kelly_pct) * self._kelly_fraction
        price = signal.current_price
        if price == 0:
            return PositionSizingResult(
                quantity=Decimal("0"),
                sizing_method=self.name,
                reason="Zero price, quantity set to 0",
            )
        equity = (
            float(ctx.portfolio.total_equity) if ctx.portfolio.total_equity > 0 else 1_000_000.0
        )
        dollar_amount = equity * kelly_pct
        quantity = int(dollar_amount / float(price))
        return PositionSizingResult(
            quantity=Decimal(str(max(quantity, 0))),
            sizing_method=self.name,
            reason=(f"Kelly ({kelly_pct*100:.1f}% of equity, " f"fraction={self._kelly_fraction})"),
        )


class VolatilityTargetingSizing:
    name = "volatility_targeting"

    def __init__(self, target_volatility: float = 0.15) -> None:
        self._target_volatility = target_volatility

    async def size(self, signal: Signal, ctx: EvaluationContext) -> PositionSizingResult:
        vol = signal.volatility if signal.volatility > 0 else 0.2
        risk_scaling = self._target_volatility / vol
        price = signal.current_price
        if price == 0:
            return PositionSizingResult(
                quantity=Decimal("0"),
                sizing_method=self.name,
                reason="Zero price, quantity set to 0",
            )
        equity = (
            float(ctx.portfolio.total_equity) if ctx.portfolio.total_equity > 0 else 1_000_000.0
        )
        base_allocation = equity * 0.02
        dollar_amount = base_allocation * risk_scaling
        quantity = int(dollar_amount / float(price))
        return PositionSizingResult(
            quantity=Decimal(str(max(quantity, 0))),
            sizing_method=self.name,
            reason=(
                f"Vol targeting (target={self._target_volatility:.0%}, "
                f"asset_vol={vol:.0%}, scaling={risk_scaling:.2f})"
            ),
        )


class AtrSizing:
    name = "atr_sizing"

    async def size(self, signal: Signal, ctx: EvaluationContext) -> PositionSizingResult:
        risk_per_trade_pct = 0.5
        price = float(signal.current_price)
        atr = signal.volatility * price if signal.volatility > 0 else price * 0.02
        risk_per_share = atr * 2
        if risk_per_share == 0:
            return PositionSizingResult(
                quantity=Decimal("0"),
                sizing_method=self.name,
                reason="Zero risk per share, quantity set to 0",
            )
        equity = (
            float(ctx.portfolio.total_equity) if ctx.portfolio.total_equity > 0 else 1_000_000.0
        )
        risk_amount = equity * (risk_per_trade_pct / 100.0)
        quantity = int(risk_amount / float(risk_per_share))
        return PositionSizingResult(
            quantity=Decimal(str(max(quantity, 0))),
            sizing_method=self.name,
            reason=f"ATR sizing (risk=${risk_amount:.2f}, risk/share=${float(risk_per_share):.2f})",
        )


class RiskPerTradeSizing:
    name = "risk_per_trade"

    def __init__(self, risk_per_trade_pct: float = 0.5, stop_loss_pct: float = 2.0) -> None:
        self._risk_per_trade_pct = risk_per_trade_pct
        self._stop_loss_pct = stop_loss_pct

    async def size(self, signal: Signal, ctx: EvaluationContext) -> PositionSizingResult:
        price = signal.current_price
        if price == 0:
            return PositionSizingResult(
                quantity=Decimal("0"),
                sizing_method=self.name,
                reason="Zero price, quantity set to 0",
            )
        equity = (
            float(ctx.portfolio.total_equity) if ctx.portfolio.total_equity > 0 else 1_000_000.0
        )
        risk_amount = equity * (self._risk_per_trade_pct / 100.0)
        stop_loss_price = float(price) * (self._stop_loss_pct / 100.0)
        if stop_loss_price == 0:
            return PositionSizingResult(
                quantity=Decimal("0"),
                sizing_method=self.name,
                reason="Zero stop loss distance, quantity set to 0",
            )
        quantity = int(risk_amount / stop_loss_price)
        stop_loss = Decimal(str(round(float(price) * (1 - self._stop_loss_pct / 100.0), 4)))
        return PositionSizingResult(
            quantity=Decimal(str(max(quantity, 0))),
            sizing_method=self.name,
            reason=f"Risk {self._risk_per_trade_pct}% of equity (${risk_amount:.2f})",
            risk_per_trade=Decimal(str(round(risk_amount, 2))),
            stop_loss=stop_loss,
        )
