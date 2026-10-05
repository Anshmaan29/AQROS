from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from aqros_live_trading.domain.models import KillSwitch


class KillSwitchManager:
    def __init__(self, kill_switch: KillSwitch) -> None:
        self._kill_switch = kill_switch

    @property
    def kill_switch(self) -> KillSwitch:
        return self._kill_switch

    def trigger(self, by: str = "manual", reason: str = "") -> None:
        self._kill_switch.trigger(by=by, reason=reason, now=datetime.now(UTC))

    def reset(self) -> None:
        self._kill_switch.reset()

    def arm(self) -> None:
        self._kill_switch.arm()

    def disable(self) -> None:
        self._kill_switch.disable()

    def enable(self) -> None:
        self._kill_switch.enable()

    @property
    def is_triggered(self) -> bool:
        return self._kill_switch.is_triggered()

    def get_status(self) -> dict[str, Any]:
        return {
            "status": self._kill_switch.status.value,
            "triggered_at": self._kill_switch.triggered_at,
            "triggered_by": self._kill_switch.triggered_by,
            "reason": self._kill_switch.reason,
            "enabled": self._kill_switch.enabled,
        }
