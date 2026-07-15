"""System clock adapter implementing the ``Clock`` port.

Domain services take their notion of "now" through the ``Clock`` port so that
timestamps are injectable and tests are deterministic (a fake clock can return
a fixed instant). This adapter is the production implementation: it returns the
real, timezone-aware current UTC time.
"""

from __future__ import annotations

from datetime import UTC, datetime

from aqros_model_registry.domain.ports import Clock


class SystemClock(Clock):
    """Production ``Clock`` returning the real timezone-aware UTC time."""

    def now(self) -> datetime:
        """Return the current time as a timezone-aware UTC ``datetime``."""
        return datetime.now(UTC)
