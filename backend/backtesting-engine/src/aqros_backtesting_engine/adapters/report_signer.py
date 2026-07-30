from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from aqros_backtesting_engine.domain.models import BacktestResult


def _encode_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, set):
        return sorted(value)
    if isinstance(value, frozenset):
        return sorted(value)
    if isinstance(value, dict):
        return {str(k): _encode_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode_value(v) for v in value]
    return value


def generate_report(result: BacktestResult) -> dict[str, Any]:
    report: dict[str, Any] = {
        "run_uuid": str(result.run_uuid),
        "status": result.status.value,
        "generated_at": datetime.now(UTC).isoformat(),
        "performance": _encode_value(asdict(result.performance)),
        "risk": _encode_value(asdict(result.risk)),
        "drawdown": _encode_value(asdict(result.drawdown)),
        "benchmark": _encode_value(asdict(result.benchmark)) if result.benchmark else None,
        "final_portfolio": _encode_value(asdict(result.final_portfolio)),
        "failure_reason": result.failure_reason,
        "trade_log_count": len(result.trade_log),
        "equity_curve_points": len(result.equity_curve),
    }
    return report


def sign_report(report: dict[str, Any], signing_key: bytes) -> str:
    payload = json.dumps(report, sort_keys=True, default=str).encode("utf-8")
    digest = hmac.new(signing_key, payload, hashlib.sha256).hexdigest()
    return f"v1:{digest}"


def verify_report(report: dict[str, Any], signature: str, signing_key: bytes) -> bool:
    if not signature.startswith("v1:"):
        return False
    expected = sign_report(report, signing_key)
    return hmac.compare_digest(signature, expected)
