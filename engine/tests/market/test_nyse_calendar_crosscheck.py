"""Dev-only verification cross-checking nyse_calendar against exchange_calendars for 2024-2027."""

from __future__ import annotations

import datetime as dt

import pytest

from engine.market.nyse_calendar import SPECIAL_NYSE_CLOSURES, get_nyse_trading_days


def test_crosscheck_nyse_calendar_against_exchange_calendars() -> None:
    try:
        import exchange_calendars as xcals
    except ImportError:
        pytest.skip("exchange_calendars not installed in environment")

    xnys = xcals.get_calendar("XNYS")
    last_session = xnys.last_session.date()

    # Compare all trading days from 2024-01-01 through exchange_calendars max session
    x_sessions = set(xnys.sessions_in_range("2024-01-01", str(last_session)).date)
    our_sessions = set(get_nyse_trading_days(dt.date(2024, 1, 1), last_session))

    diff_x_not_ours = x_sessions - our_sessions
    diff_ours_not_x = our_sessions - x_sessions

    assert diff_x_not_ours == set(), (
        f"Sessions in exchange_calendars but missing from ours: {diff_x_not_ours}"
    )
    assert diff_ours_not_x == set(), (
        f"Sessions in ours but missing from exchange_calendars: {diff_ours_not_x}"
    )
    assert len(our_sessions) == len(x_sessions) == 949

    # Verify one-off closures in range 2024-2027
    special_in_range = {d for d in SPECIAL_NYSE_CLOSURES if 2024 <= d.year <= 2027}
    assert dt.date(2025, 1, 9) in special_in_range  # Jimmy Carter National Day of Mourning
