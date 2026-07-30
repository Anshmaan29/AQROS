from __future__ import annotations

from datetime import date, time

import pytest
from aqros_backtesting_engine.adapters.calendar_provider import DefaultCalendarProvider


class TestDefaultCalendarProvider:
    async def test_nyse_calendar(self) -> None:
        provider = DefaultCalendarProvider(start_year=2024, end_year=2024)
        cal = await provider.get_calendar("NYSE")
        assert cal.exchange == "NYSE"
        assert cal.timezone == "America/New_York"
        assert cal.regular_open == time(9, 30)
        assert cal.regular_close == time(16, 0)

    async def test_nasdaq_alias(self) -> None:
        provider = DefaultCalendarProvider(start_year=2024, end_year=2024)
        cal = await provider.get_calendar("NASDAQ")
        assert cal.exchange == "NYSE"

    async def test_us_alias(self) -> None:
        provider = DefaultCalendarProvider(start_year=2024, end_year=2024)
        cal = await provider.get_calendar("US")
        assert cal.exchange == "NYSE"

    async def test_unsupported_exchange(self) -> None:
        provider = DefaultCalendarProvider()
        with pytest.raises(ValueError, match="unsupported exchange"):
            await provider.get_calendar("LSE")

    async def test_new_years_day_holiday(self) -> None:
        provider = DefaultCalendarProvider(start_year=2024, end_year=2024)
        cal = await provider.get_calendar("NYSE")
        assert date(2024, 1, 1) in cal.holidays

    async def test_july_4th_holiday(self) -> None:
        provider = DefaultCalendarProvider(start_year=2024, end_year=2024)
        cal = await provider.get_calendar("NYSE")
        assert date(2024, 7, 4) in cal.holidays

    async def test_christmas_holiday(self) -> None:
        provider = DefaultCalendarProvider(start_year=2024, end_year=2024)
        cal = await provider.get_calendar("NYSE")
        assert date(2024, 12, 25) in cal.holidays

    async def test_memorial_day_holiday(self) -> None:
        provider = DefaultCalendarProvider(start_year=2024, end_year=2024)
        cal = await provider.get_calendar("NYSE")
        assert date(2024, 5, 27) in cal.holidays

    async def test_labor_day_holiday(self) -> None:
        provider = DefaultCalendarProvider(start_year=2024, end_year=2024)
        cal = await provider.get_calendar("NYSE")
        assert date(2024, 9, 2) in cal.holidays

    async def test_thanksgiving_holiday(self) -> None:
        provider = DefaultCalendarProvider(start_year=2024, end_year=2024)
        cal = await provider.get_calendar("NYSE")
        assert date(2024, 11, 28) in cal.holidays

    async def test_martin_luther_king_day(self) -> None:
        provider = DefaultCalendarProvider(start_year=2024, end_year=2024)
        cal = await provider.get_calendar("NYSE")
        assert date(2024, 1, 15) in cal.holidays

    async def test_presidents_day(self) -> None:
        provider = DefaultCalendarProvider(start_year=2024, end_year=2024)
        cal = await provider.get_calendar("NYSE")
        assert date(2024, 2, 19) in cal.holidays

    async def test_weekend_not_in_holidays(self) -> None:
        provider = DefaultCalendarProvider(start_year=2024, end_year=2024)
        cal = await provider.get_calendar("NYSE")
        assert date(2024, 1, 6) not in cal.holidays
        assert date(2024, 1, 7) not in cal.holidays

    async def test_holiday_on_saturday_observed_friday(self) -> None:
        provider = DefaultCalendarProvider(start_year=2027, end_year=2027)
        cal = await provider.get_calendar("NYSE")
        assert date(2027, 1, 1) in cal.holidays

    async def test_multi_year_range(self) -> None:
        provider = DefaultCalendarProvider(start_year=2020, end_year=2025)
        cal = await provider.get_calendar("NYSE")
        assert date(2020, 1, 1) in cal.holidays
        assert date(2025, 12, 25) in cal.holidays

    async def test_no_half_days(self) -> None:
        provider = DefaultCalendarProvider(start_year=2024, end_year=2024)
        cal = await provider.get_calendar("NYSE")
        assert cal.half_days == {}
