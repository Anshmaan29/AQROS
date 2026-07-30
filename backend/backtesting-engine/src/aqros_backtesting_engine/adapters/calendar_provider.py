from __future__ import annotations

from datetime import date, time, timedelta

from aqros_backtesting_engine.domain.models import ExchangeCalendarData
from aqros_backtesting_engine.domain.ports import CalendarProvider

_HOLIDAY_CACHE: dict[str, frozenset[date]] = {}


def _observed(dt: date) -> date:
    if dt.weekday() == 5:
        return dt - timedelta(days=1)
    if dt.weekday() == 6:
        return dt + timedelta(days=1)
    return dt


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    last = date(year, month + 1, 1) - timedelta(days=1)
    offset = (last.weekday() - weekday) % 7
    return last - timedelta(days=offset)


def _us_holidays(year: int) -> frozenset[date]:
    cache_key = f"us:{year}"
    if cache_key in _HOLIDAY_CACHE:
        return _HOLIDAY_CACHE[cache_key]
    h: set[date] = set()
    h.add(_observed(date(year, 1, 1)))
    h.add(_nth_weekday(year, 1, 0, 3))
    h.add(_nth_weekday(year, 2, 0, 3))
    h.add(_last_weekday(year, 5, 0))
    h.add(_observed(date(year, 7, 4)))
    h.add(_nth_weekday(year, 9, 0, 1))
    h.add(_nth_weekday(year, 11, 3, 4))
    h.add(_observed(date(year, 12, 25)))
    result = frozenset(h)
    _HOLIDAY_CACHE[cache_key] = result
    return result


class DefaultCalendarProvider(CalendarProvider):
    def __init__(self, start_year: int = 2000, end_year: int = 2030) -> None:
        self._start_year = start_year
        self._end_year = end_year

    async def get_calendar(self, exchange: str) -> ExchangeCalendarData:
        exchange_lower = exchange.lower()
        if exchange_lower in ("nyse", "nasdaq", "xnys", "xnas", "us"):
            return self._nyse_calendar()
        raise ValueError(f"unsupported exchange: {exchange}")

    def _nyse_calendar(self) -> ExchangeCalendarData:
        all_holidays: set[date] = set()
        for year in range(self._start_year, self._end_year + 1):
            all_holidays.update(_us_holidays(year))
        return ExchangeCalendarData(
            exchange="NYSE",
            timezone="America/New_York",
            regular_open=time(9, 30),
            regular_close=time(16, 0),
            holidays=frozenset(all_holidays),
            half_days={},
            source="DefaultCalendarProvider",
        )
