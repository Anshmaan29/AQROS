from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from aqros_backtesting_engine.domain.models import BacktestConfiguration, BacktestResult, RunStatus
from aqros_backtesting_engine.domain.services import BacktestService


@dataclass(frozen=True, slots=True)
class ValidationResult:
    pbo: float
    dsr: float
    cpcv_mean_return: Decimal
    cpcv_std_return: Decimal
    num_trials: int
    sharpe_ratios: tuple[float, ...] = field(default_factory=tuple)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_cdf_inv(p: float) -> float:
    return math.sqrt(2.0) * _erf_inv(2.0 * p - 1.0)


def _erf_inv(x: float) -> float:
    if x >= 1.0:
        return float("inf")
    if x <= -1.0:
        return float("-inf")
    sign = 1.0 if x >= 0.0 else -1.0
    x = abs(x)
    a = 0.147
    ln1 = math.log(1.0 - x)
    ln2 = math.log(1.0 + x)
    inside = 2.0 / (math.pi * a) + ln1 / 2.0 - ln2 / 2.0
    t1 = 2.0 / (math.pi * a)
    return sign * math.sqrt(math.sqrt(inside**2 - (ln1 - ln2) / a) - t1)


def compute_dsr(sharpe_ratios: Sequence[float], num_observations: int = 252) -> float:
    num_trials = len(sharpe_ratios)
    if num_trials == 0:
        return 0.0
    mean_sharpe = float(sum(sharpe_ratios)) / num_trials
    if num_trials <= 1:
        z = mean_sharpe * math.sqrt(float(num_observations))
        return float(_norm_cdf(z))
    euler_mascheroni = 0.5772156649
    e_max_z = (1.0 - euler_mascheroni) * _norm_cdf_inv(
        1.0 - 1.0 / float(num_trials)
    ) + euler_mascheroni * _norm_cdf_inv(1.0 - 1.0 / (float(num_trials) * math.e))
    max_sharpe = float(max(sharpe_ratios))
    variance = max(1.0, max_sharpe * max_sharpe)
    z = (mean_sharpe * math.sqrt(float(num_observations)) - e_max_z) / math.sqrt(variance)
    return float(_norm_cdf(z))


def compute_pbo(
    results: Sequence[BacktestResult],
    in_sample_ratio: float = 0.6,
) -> float:
    if len(results) < 2:
        return 0.5
    is_returns: list[float] = []
    oos_returns: list[float] = []
    for r in results:
        curve = r.equity_curve
        if len(curve) < 2:
            continue
        split = max(1, int(len(curve) * in_sample_ratio))
        is_return = float(curve[min(split, len(curve)) - 1].total_value - curve[0].total_value)
        oos_return = float(curve[-1].total_value - curve[split - 1].total_value)
        is_returns.append(is_return)
        oos_returns.append(oos_return)
    if not is_returns or not oos_returns:
        return 0.5
    is_ranked = sorted(range(len(is_returns)), key=lambda i: is_returns[i], reverse=True)
    best_is_idx = is_ranked[0]
    oos_rank_of_best_is = sum(1 for idx in oos_returns if idx > oos_returns[best_is_idx])
    pbo = oos_rank_of_best_is / max(len(oos_returns) - 1, 1)
    return min(1.0, max(0.0, float(pbo)))


async def run_cpcv(
    service: BacktestService,
    configuration: BacktestConfiguration,
    num_splits: int = 3,
) -> tuple[Decimal, Decimal]:
    total_days = (configuration.end.date() - configuration.start.date()).days
    if total_days < num_splits:
        return (Decimal("0"), Decimal("0"))
    segment_days = total_days // num_splits
    returns: list[Decimal] = []
    for i in range(num_splits):
        fold_start = configuration.start + timedelta(days=i * segment_days)
        fold_end = fold_start + timedelta(days=segment_days)
        if fold_end > configuration.end:
            break
        test_start = fold_end - timedelta(days=segment_days // 4)
        purged_config = BacktestConfiguration(
            strategy_id=configuration.strategy_id,
            strategy_params=configuration.strategy_params,
            model_name=configuration.model_name,
            model_version=configuration.model_version,
            universe=configuration.universe,
            exchange=configuration.exchange,
            start=fold_start,
            end=test_start,
            starting_cash=configuration.starting_cash,
            bar_interval=configuration.bar_interval,
            slippage_model=configuration.slippage_model,
            slippage_params=configuration.slippage_params,
            commission_model=configuration.commission_model,
            commission_params=configuration.commission_params,
            fill_model=configuration.fill_model,
            fill_params=configuration.fill_params,
            latency_model=configuration.latency_model,
            latency_params=configuration.latency_params,
            leverage_enabled=configuration.leverage_enabled,
            max_leverage=configuration.max_leverage,
            equity_sample_interval=configuration.equity_sample_interval,
            benchmark_symbol=configuration.benchmark_symbol,
            seed=configuration.seed + 1 + i,
        )
        try:
            result = await service.run(purged_config)
            if result.status == RunStatus.COMPLETED:
                returns.append(result.performance.total_return)
        except Exception:
            pass
    if not returns:
        return (Decimal("0"), Decimal("0"))
    mean = float(sum(returns, Decimal("0"))) / len(returns)
    variance = sum((float(r) - mean) ** 2 for r in returns) / len(returns)
    std = Decimal(str(math.sqrt(variance)))
    return (Decimal(str(mean)), std)


async def run_validation_gauntlet(
    service: BacktestService,
    base_configuration: BacktestConfiguration,
    num_trials: int = 10,
    in_sample_ratio: float = 0.6,
    num_cpcv_splits: int = 3,
) -> ValidationResult:
    results: list[BacktestResult] = []
    for i in range(num_trials):
        config = BacktestConfiguration(
            strategy_id=base_configuration.strategy_id,
            strategy_params=base_configuration.strategy_params,
            model_name=base_configuration.model_name,
            model_version=base_configuration.model_version,
            universe=base_configuration.universe,
            exchange=base_configuration.exchange,
            start=base_configuration.start,
            end=base_configuration.end,
            starting_cash=base_configuration.starting_cash,
            bar_interval=base_configuration.bar_interval,
            slippage_model=base_configuration.slippage_model,
            slippage_params=base_configuration.slippage_params,
            commission_model=base_configuration.commission_model,
            commission_params=base_configuration.commission_params,
            fill_model=base_configuration.fill_model,
            fill_params=base_configuration.fill_params,
            latency_model=base_configuration.latency_model,
            latency_params=base_configuration.latency_params,
            leverage_enabled=base_configuration.leverage_enabled,
            max_leverage=base_configuration.max_leverage,
            equity_sample_interval=base_configuration.equity_sample_interval,
            benchmark_symbol=base_configuration.benchmark_symbol,
            seed=base_configuration.seed + i,
        )
        try:
            result = await service.run(config)
            if result.status == RunStatus.COMPLETED:
                results.append(result)
        except Exception:
            pass
    sharpe_ratios = [float(r.performance.sharpe_ratio or 0) for r in results]
    pbo = compute_pbo(results, in_sample_ratio)
    dsr = compute_dsr(sharpe_ratios)
    cpcv_mean, cpcv_std = await run_cpcv(service, base_configuration, num_cpcv_splits)
    return ValidationResult(
        pbo=pbo,
        dsr=dsr,
        cpcv_mean_return=cpcv_mean,
        cpcv_std_return=cpcv_std,
        num_trials=len(results),
        sharpe_ratios=tuple(sharpe_ratios),
    )


__all__ = [
    "ValidationResult",
    "compute_dsr",
    "compute_pbo",
    "run_cpcv",
    "run_validation_gauntlet",
]
