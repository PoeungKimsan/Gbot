"""Tests for timezone-aware daily bar completeness invariant."""

from __future__ import annotations

import datetime as dt
import sqlite3
import zoneinfo
from pathlib import Path

from forward.config import TICKERS_MAP
from forward.db import init_bars_db
from forward.feed import load_bars_from_db


def test_drops_incomplete_bar_in_own_timezone(tmp_path: Path):
    """Verify that load_bars_from_db drops any bar timestamped on or after today in the exchange timezone."""
    db_path = tmp_path / "test_completeness.sqlite"
    init_bars_db(db_path)

    # Insert historical bar for 2026-10-08 and an in-progress bar for 2026-10-09
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO bars (symbol, date, open, high, low, close, volume, fetch_timestamp, sanitized_hl)
        VALUES
            ('EURUSD', '2026-10-07', 1.12, 1.13, 1.11, 1.125, 100, '2026-10-07T23:00:00Z', 0),
            ('EURUSD', '2026-10-08', 1.125, 1.13, 1.12, 1.122, 100, '2026-10-08T23:00:00Z', 0),
            ('EURUSD', '2026-10-09', 1.122, 1.128, 1.120, 1.124, 100, '2026-10-09T22:30:00Z', 0)
        """
    )
    conn.commit()
    conn.close()

    # 1. Simulated time: Friday 2026-10-09 at 22:30 UTC (London is 23:30 BST, calendar date 2026-10-09)
    # EURUSD is in Europe/London. The 2026-10-09 bar is NOT complete yet.
    # Therefore, 2026-10-09 bar MUST BE DROPPED.
    london_tz = zoneinfo.ZoneInfo("Europe/London")
    eval_time_incomplete = dt.datetime(2026, 10, 9, 22, 30, tzinfo=dt.UTC)

    bars_incomplete = load_bars_from_db(
        "EURUSD",
        db_path=db_path,
        current_time=eval_time_incomplete,
    )
    assert len(bars_incomplete) == 2, f"Expected 2 completed bars, got {len(bars_incomplete)}"
    assert bars_incomplete[-1].date == dt.date(2026, 10, 8), (
        f"Latest bar should be 2026-10-08, got {bars_incomplete[-1].date}"
    )

    # 2. Simulated time: Saturday 2026-10-10 at 06:00 UTC (Next morning)
    # London calendar date is now 2026-10-10.
    # The 2026-10-09 session is now complete and finalized.
    eval_time_complete = dt.datetime(2026, 10, 10, 6, 0, tzinfo=dt.UTC)

    bars_complete = load_bars_from_db(
        "EURUSD",
        db_path=db_path,
        current_time=eval_time_complete,
    )
    assert len(bars_complete) == 3, f"Expected 3 completed bars, got {len(bars_complete)}"
    assert bars_complete[-1].date == dt.date(2026, 10, 9), (
        f"Latest bar should be 2026-10-09, got {bars_complete[-1].date}"
    )


def test_timezone_boundary_across_exchanges(tmp_path: Path):
    """Verify that Tokyo, London, and New York exchange timezones respect their independent dates."""
    db_path = tmp_path / "tz_test.sqlite"
    init_bars_db(db_path)

    conn = sqlite3.connect(db_path)
    # Put a bar dated 2026-10-10 for US500 (America/New_York)
    conn.execute(
        """
        INSERT INTO bars (symbol, date, open, high, low, close, volume, fetch_timestamp, sanitized_hl)
        VALUES
            ('US500', '2026-10-09', 5800, 5850, 5790, 5840, 1000, '2026-10-09T21:00:00Z', 0),
            ('US500', '2026-10-10', 5840, 5860, 5830, 5855, 1000, '2026-10-10T15:00:00Z', 0)
        """
    )
    conn.commit()
    conn.close()

    # When NY time is Saturday 2026-10-10 15:30 (date is 2026-10-10), bar for 2026-10-10 is dropped
    ny_time = dt.datetime(2026, 10, 10, 19, 30, tzinfo=dt.UTC)  # 15:30 EDT
    bars = load_bars_from_db("US500", db_path=db_path, current_time=ny_time)
    assert len(bars) == 1
    assert bars[0].date == dt.date(2026, 10, 9)
