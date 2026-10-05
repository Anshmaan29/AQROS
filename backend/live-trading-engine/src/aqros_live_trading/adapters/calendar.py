from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

from aqros_live_trading.domain.models import SessionStatus, TradingSession


@dataclass
class TradingCalendar:
    timezone_str: str = "America/New_York"
    regular_open: time = field(default_factory=lambda: time(9, 30))
    regular_close: time = field(default_factory=lambda: time(16, 0))
    pre_market_open: time = field(default_factory=lambda: time(4, 0))
    pre_market_close: time = field(default_factory=lambda: time(9, 30))
    after_hours_open: time = field(default_factory=lambda: time(16, 0))
    after_hours_close: time = field(default_factory=lambda: time(20, 0))

    _holidays: set[date] = field(default_factory=set)

    def add_holiday(self, d: date) -> None:
        self._holidays.add(d)

    def is_weekend(self, d: date) -> bool:
        return d.weekday() >= 5

    def is_holiday(self, d: date) -> bool:
        return d in self._holidays

    def is_trading_day(self, d: date) -> bool:
        return not self.is_weekend(d) and not self.is_holiday(d)

    def get_trading_session(self, dt: datetime) -> TradingSession:
        d = dt.date()
        session_date = d if self.is_trading_day(d) else self._next_trading_day(d)
        return TradingSession(
            date=session_date,
            open=datetime.combine(session_date, self.regular_open, tzinfo=UTC),
            close=datetime.combine(session_date, self.regular_close, tzinfo=UTC),
            pre_market_open=datetime.combine(session_date, self.pre_market_open, tzinfo=UTC),
            pre_market_close=datetime.combine(session_date, self.pre_market_close, tzinfo=UTC),
            after_hours_open=datetime.combine(session_date, self.after_hours_open, tzinfo=UTC),
            after_hours_close=datetime.combine(session_date, self.after_hours_close, tzinfo=UTC),
        )

    def current_session_status(self, now: datetime | None = None) -> SessionStatus:
        now = now or datetime.now(UTC)
        session = self.get_trading_session(now)
        return session.get_session_for(now)

    def is_market_open(self, now: datetime | None = None) -> bool:
        return self.current_session_status(now) == SessionStatus.REGULAR

    def next_market_open(self, now: datetime | None = None) -> datetime | None:
        now = now or datetime.now(UTC)
        d = now.date()
        d = d if now.time() < self.regular_open else d + timedelta(days=1)
        while not self.is_trading_day(d):
            d = d + timedelta(days=1)
        return datetime.combine(d, self.regular_open, tzinfo=UTC)

    def _next_trading_day(self, d: date) -> date:
        d = d + timedelta(days=1)
        while not self.is_trading_day(d):
            d = d + timedelta(days=1)
        return d
