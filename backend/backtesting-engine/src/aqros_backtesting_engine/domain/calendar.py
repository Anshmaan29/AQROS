"""Trading calendar domain component for the Backtesting Engine.

Pure, deterministic derivation of exchange trading sessions from an
``ExchangeCalendarData`` record supplied by the ``CalendarProvider`` port
(``domain/ports.py``). Excludes weekends and configured holidays, applies
half-day session-close overrides, and resolves session open/close as
timezone-aware, DST-safe ``datetime`` instances using the IANA time zone
recorded on the calendar data. Python's ``zoneinfo`` (backed by the system
tz database) resolves a local wall-clock time in that time zone to a
UTC-aware instant correctly across daylight-saving transitions, so no
special-casing of DST boundaries is needed here.

This module performs no I/O and never reads wall-clock time: every result
is a pure function of the ``ExchangeCalendarData`` and the date(s)
requested, so identical inputs always yield identical session boundaries on
every run (design.md Section 13 "Trading Calendar"; Requirements 7.1, 7.2,
7.3, 7.4, 7.5).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from aqros_backtesting_engine.domain.models import ExchangeCalendarData

__all__ = [
    "TradingCalendar",
    "is_half_day",
    "is_session_day",
    "session_close",
    "session_days",
    "session_open",
]

_WEEKEND_THRESHOLD = 5  # date.weekday() returns 0=Monday .. 6=Sunday; 5/6 == Sat/Sun


def is_session_day(calendar_data: ExchangeCalendarData, day: date) -> bool:
    """Return ``True`` if ``day`` is a trading session day on ``calendar_data``.

    A day is a session day when it is not a weekend (Saturday/Sunday) and is
    not one of the exchange's configured holidays (Requirements 7.1, 7.2).
    """
    return day.weekday() < _WEEKEND_THRESHOLD and day not in calendar_data.holidays


def is_half_day(calendar_data: ExchangeCalendarData, day: date) -> bool:
    """Return ``True`` if ``day`` is a configured half-day (shortened session close).

    Does not itself imply ``day`` is a session day at all — callers that
    need that guarantee should also check ``is_session_day``.
    """
    return day in calendar_data.half_days


def session_open(calendar_data: ExchangeCalendarData, day: date) -> datetime:
    """Return the timezone-aware session open instant for ``day``.

    The instant is resolved in the exchange's IANA time zone via
    ``zoneinfo``, which correctly accounts for daylight-saving transitions
    (Requirements 7.3, 7.4). Session open is unaffected by half-days — only
    the close time is shortened (Requirement 7.2).

    Raises:
        ValueError: if ``day`` is not a trading session day (weekend or
            configured holiday) on ``calendar_data`` — there is no session
            to open.
    """
    if not is_session_day(calendar_data, day):
        raise ValueError(
            f"{day.isoformat()} is not a trading session day on {calendar_data.exchange}"
        )
    tz = ZoneInfo(calendar_data.timezone)
    return datetime.combine(day, calendar_data.regular_open, tzinfo=tz)


def session_close(calendar_data: ExchangeCalendarData, day: date) -> datetime:
    """Return the timezone-aware session close instant for ``day``.

    Uses the half-day override close time-of-day when ``day`` is a
    configured half-day, otherwise the exchange's regular close
    (Requirement 7.2). Resolved as a DST-safe instant via ``zoneinfo``
    (Requirements 7.3, 7.4).

    Raises:
        ValueError: if ``day`` is not a trading session day (weekend or
            configured holiday) on ``calendar_data`` — there is no session
            to close.
    """
    if not is_session_day(calendar_data, day):
        raise ValueError(
            f"{day.isoformat()} is not a trading session day on {calendar_data.exchange}"
        )
    close_time = calendar_data.half_days.get(day, calendar_data.regular_close)
    tz = ZoneInfo(calendar_data.timezone)
    return datetime.combine(day, close_time, tzinfo=tz)


def session_days(calendar_data: ExchangeCalendarData, start: date, end: date) -> Iterator[date]:
    """Yield every trading session day in ``[start, end]`` (inclusive), in order.

    Excludes weekends and holidays; half-days are included since they still
    have a session, just a shortened one (Requirements 7.1, 7.2, 7.5).
    Yields nothing if ``end`` is before ``start``.
    """
    current = start
    while current <= end:
        if is_session_day(calendar_data, current):
            yield current
        current += timedelta(days=1)


@dataclass(frozen=True, slots=True)
class TradingCalendar:
    """Pure, deterministic exchange trading calendar (design.md's ``Trading_Calendar``).

    A thin, stateless wrapper around a single exchange's
    ``ExchangeCalendarData`` exposing session-day and session-open/close
    resolution as methods, for callers (e.g. ``Historical_Replay``) that
    prefer an object handle on one exchange's calendar over passing
    ``calendar_data`` explicitly to the module-level functions above. Every
    method is a pure function of ``calendar_data`` and its arguments: no
    I/O, no wall-clock reads (Requirement 7).
    """

    calendar_data: ExchangeCalendarData

    def is_session_day(self, day: date) -> bool:
        return is_session_day(self.calendar_data, day)

    def is_half_day(self, day: date) -> bool:
        return is_half_day(self.calendar_data, day)

    def session_open(self, day: date) -> datetime:
        return session_open(self.calendar_data, day)

    def session_close(self, day: date) -> datetime:
        return session_close(self.calendar_data, day)

    def session_days(self, start: date, end: date) -> Iterator[date]:
        return session_days(self.calendar_data, start, end)
