"""NYSE trading calendar helper for validating ETF historical date sequences.

Provides deterministic verification of New York Stock Exchange trading sessions,
accounting for federal market holidays, Easter/Good Friday, and historical
National Days of Mourning.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from zoneinfo import ZoneInfo

__all__ = [
    "easter_sunday",
    "get_nyse_holidays",
    "get_nyse_trading_days",
    "is_last_trading_day_of_month",
    "is_nyse_trading_day",
    "last_completed_session",
    "next_trading_day",
    "validate_nyse_sequence",
]

NEW_YORK = ZoneInfo("America/New_York")

# Historic unscheduled NYSE closures
SPECIAL_NYSE_CLOSURES: frozenset[dt.date] = frozenset({
    dt.date(2025, 1, 9),   # National Day of Mourning for Jimmy Carter
    dt.date(2018, 12, 5),  # National Day of Mourning for George H.W. Bush
    dt.date(2012, 10, 29), # Hurricane Sandy
    dt.date(2012, 10, 30), # Hurricane Sandy
    dt.date(2007, 1, 2),   # National Day of Mourning for Gerald Ford
    dt.date(2004, 6, 11),  # National Day of Mourning for Ronald Reagan
    dt.date(2001, 9, 11),  # September 11 attacks
    dt.date(2001, 9, 12),
    dt.date(2001, 9, 13),
    dt.date(2001, 9, 14),
})


def easter_sunday(year: int) -> dt.date:
    """Compute Easter Sunday date via the Meeus/Jones/Butcher algorithm."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l_val = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_val) // 451
    month = (h + l_val - 7 * m + 114) // 31
    day = ((h + l_val - 7 * m + 114) % 31) + 1
    return dt.date(year, month, day)


def get_nyse_holidays(year: int) -> set[dt.date]:
    """Return all standard observed NYSE holidays for a given calendar year."""
    holidays: set[dt.date] = set()

    # 1. New Year's Day (Jan 1)
    ny = dt.date(year, 1, 1)
    if ny.weekday() == 6:  # Sunday -> observed Monday
        holidays.add(dt.date(year, 1, 2))
    elif ny.weekday() != 5:  # If Saturday, not observed on preceding Friday unless separate rule
        holidays.add(ny)

    # 2. Martin Luther King Jr. Day (3rd Monday in January)
    d = dt.date(year, 1, 1)
    mondays = [
        d + dt.timedelta(days=k)
        for k in range(31)
        if (d + dt.timedelta(days=k)).weekday() == 0
    ]
    if len(mondays) >= 3:
        holidays.add(mondays[2])

    # 3. Washington's Birthday / Presidents' Day (3rd Monday in February)
    d = dt.date(year, 2, 1)
    mondays = [
        d + dt.timedelta(days=k)
        for k in range(28)
        if (d + dt.timedelta(days=k)).weekday() == 0
    ]
    if len(mondays) >= 3:
        holidays.add(mondays[2])

    # 4. Good Friday (Friday before Easter Sunday)
    holidays.add(easter_sunday(year) - dt.timedelta(days=2))

    # 5. Memorial Day (Last Monday in May)
    d = dt.date(year, 5, 31)
    while d.weekday() != 0:
        d -= dt.timedelta(days=1)
    holidays.add(d)

    # 6. Juneteenth National Independence Day (June 19, observed since 2022)
    if year >= 2022:
        june19 = dt.date(year, 6, 19)
        if june19.weekday() == 5:
            holidays.add(dt.date(year, 6, 18))
        elif june19.weekday() == 6:
            holidays.add(dt.date(year, 6, 20))
        else:
            holidays.add(june19)

    # 7. Independence Day (July 4)
    july4 = dt.date(year, 7, 4)
    if july4.weekday() == 5:
        holidays.add(dt.date(year, 7, 3))
    elif july4.weekday() == 6:
        holidays.add(dt.date(year, 7, 5))
    else:
        holidays.add(july4)

    # 8. Labor Day (1st Monday in September)
    d = dt.date(year, 9, 1)
    while d.weekday() != 0:
        d += dt.timedelta(days=1)
    holidays.add(d)

    # 9. Thanksgiving Day (4th Thursday in November)
    d = dt.date(year, 11, 1)
    thursdays = [
        d + dt.timedelta(days=k)
        for k in range(30)
        if (d + dt.timedelta(days=k)).weekday() == 3
    ]
    if len(thursdays) >= 4:
        holidays.add(thursdays[3])

    # 10. Christmas Day (Dec 25)
    xmas = dt.date(year, 12, 25)
    if xmas.weekday() == 5:
        holidays.add(dt.date(year, 12, 24))
    elif xmas.weekday() == 6:
        holidays.add(dt.date(year, 12, 26))
    else:
        holidays.add(xmas)

    return holidays


def is_nyse_trading_day(d: dt.date) -> bool:
    """Return True if the date is a valid scheduled NYSE trading session."""
    if d.weekday() >= 5:  # Saturday or Sunday
        return False
    if d in SPECIAL_NYSE_CLOSURES:
        return False
    return d not in get_nyse_holidays(d.year)


def get_nyse_trading_days(start_date: dt.date, end_date: dt.date) -> list[dt.date]:
    """Generate all expected NYSE trading sessions in the closed interval [start_date, end_date]."""
    if start_date > end_date:
        return []

    holidays_by_year: dict[int, set[dt.date]] = {}
    trading_days: list[dt.date] = []
    curr = start_date

    while curr <= end_date:
        if curr.weekday() < 5 and curr not in SPECIAL_NYSE_CLOSURES:
            if curr.year not in holidays_by_year:
                holidays_by_year[curr.year] = get_nyse_holidays(curr.year)
            if curr not in holidays_by_year[curr.year]:
                trading_days.append(curr)
        curr += dt.timedelta(days=1)

    return trading_days


def validate_nyse_sequence(dates: Iterable[dt.date | str]) -> list[dt.date]:
    """Validate a sequence of dates against the NYSE calendar.

    Raises ValueError if:
    - sequence is empty
    - duplicate dates are present
    - dates are not in strictly ascending order
    - any expected NYSE trading day between the first and last date is missing (gap).
    """
    parsed: list[dt.date] = []
    for item in dates:
        if isinstance(item, str):
            parsed.append(dt.date.fromisoformat(item))
        else:
            parsed.append(item)

    if not parsed:
        raise ValueError("Date sequence is empty.")

    # 1. Unsorted / duplicate checks
    seen: set[dt.date] = set()
    prev: dt.date | None = None
    for d in parsed:
        if d in seen:
            raise ValueError(f"Duplicate date found in series: {d.isoformat()}")
        seen.add(d)
        if prev is not None and d <= prev:
            raise ValueError(f"Unsorted date sequence: {d.isoformat()} follows {prev.isoformat()}")
        prev = d

    # 2. Gap check against expected NYSE calendar between first and last date
    first_d = parsed[0]
    last_d = parsed[-1]
    expected = get_nyse_trading_days(first_d, last_d)
    actual_set = set(parsed)
    missing = [d for d in expected if d not in actual_set]

    if missing:
        missing_preview = [m.isoformat() for m in missing[:5]]
        raise ValueError(
            f"Gap against NYSE calendar detected: {len(missing)} trading day(s) missing between "
            f"{first_d.isoformat()} and {last_d.isoformat()} (missing: {missing_preview})"
        )

    return parsed


def last_completed_session(now_ny: dt.datetime) -> dt.date:
    """Return the date of the most recent completed NYSE trading session.

    A session is completed if `now_ny` is on a scheduled NYSE trading day at or
    after the regular 16:00 ET close. Otherwise, returns the preceding trading day.
    """
    if now_ny.tzinfo is None:
        now_ny = now_ny.replace(tzinfo=NEW_YORK)
    else:
        now_ny = now_ny.astimezone(NEW_YORK)

    today = now_ny.date()
    if is_nyse_trading_day(today) and now_ny.time() >= dt.time(16, 0):
        return today

    d = today - dt.timedelta(days=1)
    while not is_nyse_trading_day(d):
        d -= dt.timedelta(days=1)
    return d


def is_last_trading_day_of_month(d: dt.date) -> bool:
    """Return True if `d` is the final NYSE trading session of its calendar month."""
    if not is_nyse_trading_day(d):
        return False
    next_day = d + dt.timedelta(days=1)
    while next_day.month == d.month:
        if is_nyse_trading_day(next_day):
            return False
        next_day += dt.timedelta(days=1)
    return True


def next_trading_day(d: dt.date) -> dt.date:
    """Return the next scheduled NYSE trading day strictly after date `d`."""
    curr = d + dt.timedelta(days=1)
    while not is_nyse_trading_day(curr):
        curr += dt.timedelta(days=1)
    return curr

