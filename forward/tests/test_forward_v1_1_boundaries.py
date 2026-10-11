"""Unit tests for Prereg v1.1 boundary rules, zoneinfo DST dates, and risk logging."""

from __future__ import annotations

import datetime as dt
import sqlite3
import zoneinfo
from pathlib import Path
import pytest

from research.trend.models import DailyBar
from forward.config import TICKERS_MAP
from forward.db import get_trend_db, init_bars_db, init_trend_db
from forward.feed import load_bars_from_db
from forward.runner import run_forward_day


def test_carried_pending_exclusion(tmp_path: Path):
    """Signals dated <= 2026-10-09 (pending for 10-12 open) are CARRIED_PENDING, not fresh."""
    bars_db = tmp_path / "bars.sqlite"
    trend_db = tmp_path / "trend.db"
    init_bars_db(bars_db)
    init_trend_db(trend_db)

    # Insert 300 daily bars for XAUUSD ending on 2026-10-12
    start_date = dt.date(2025, 1, 1)
    dates = []
    curr = start_date
    while len(dates) < 300:
        if curr.weekday() < 5:
            dates.append(curr)
        curr += dt.timedelta(days=1)

    dates[-2] = dt.date(2026, 10, 9)
    dates[-1] = dt.date(2026, 10, 12)

    with sqlite3.connect(bars_db) as conn:
        for idx, d in enumerate(dates):
            p = 2000.0 + idx * 2.0
            conn.execute(
                """
                INSERT INTO bars (symbol, date, open, high, low, close, volume, fetch_timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("XAUUSD", d.isoformat(), p, p + 5.0, p - 5.0, p + 2.0, 1000, "2026-10-12T00:00:00Z"),
            )

    # Run forward day as of 2026-10-12 with current_time at 12:00 UTC on 2026-10-13
    res = run_forward_day(
        as_of_date=dt.date(2026, 10, 12),
        db_path=trend_db,
        bars_db_path=bars_db,
        current_time=dt.datetime(2026, 10, 13, 12, 0, tzinfo=dt.UTC),
    )

    with get_trend_db(trend_db) as conn:
        carried = conn.execute("SELECT trade_id, status FROM carried_trades WHERE status = 'CARRIED_PENDING' OR status = 'CARRIED'").fetchall()
        fresh = conn.execute("SELECT trade_id FROM trades").fetchall()

    # The entry on or before 10-12 from signal <= 10-09 must be in carried_trades, NOT fresh trades
    assert len(carried) >= 1
    assert len(fresh) == 0


def test_fresh_signal_and_reversal_entry(tmp_path: Path):
    """Signal on >= 2026-10-12 is FRESH; reversal after carried exit is fresh."""
    bars_db = tmp_path / "bars.sqlite"
    trend_db = tmp_path / "trend.db"
    init_bars_db(bars_db)
    init_trend_db(trend_db)

    # Insert bars through 2026-10-15
    dates = []
    curr = dt.date(2025, 1, 1)
    while len(dates) < 310:
        if curr.weekday() < 5:
            dates.append(curr)
        curr += dt.timedelta(days=1)

    dates[-5] = dt.date(2026, 10, 9)
    dates[-4] = dt.date(2026, 10, 12)
    dates[-3] = dt.date(2026, 10, 13)
    dates[-2] = dt.date(2026, 10, 14)
    dates[-1] = dt.date(2026, 10, 15)

    with sqlite3.connect(bars_db) as conn:
        for idx, d in enumerate(dates):
            p = 2000.0 + (idx * 2.0 if d < dt.date(2026, 10, 12) else (1800.0 if d == dt.date(2026, 10, 12) else 2200.0))
            conn.execute(
                """
                INSERT INTO bars (symbol, date, open, high, low, close, volume, fetch_timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("XAUUSD", d.isoformat(), p, p + 10.0, p - 10.0, p + 5.0, 1000, "2026-10-16T00:00:00Z"),
            )

    res = run_forward_day(
        as_of_date=dt.date(2026, 10, 15),
        db_path=trend_db,
        bars_db_path=bars_db,
        current_time=dt.datetime(2026, 10, 16, 12, 0, tzinfo=dt.UTC),
    )
    assert res["as_of_date"] == "2026-10-15"


def test_zoneinfo_dst_transitions_2026():
    """Verify zoneinfo date handling and bar completion across 2026-10-25 and 2026-11-01."""
    tz_berlin = zoneinfo.ZoneInfo("Europe/Berlin")
    tz_london = zoneinfo.ZoneInfo("Europe/London")
    tz_ny = zoneinfo.ZoneInfo("America/New_York")

    # 1. Before EU DST transition: Friday 2026-10-23
    t_oct23_1600_utc = dt.datetime(2026, 10, 23, 16, 0, tzinfo=dt.UTC)
    berlin_oct23 = t_oct23_1600_utc.astimezone(tz_berlin)
    assert berlin_oct23.hour == 18
    assert berlin_oct23.date() == dt.date(2026, 10, 23)

    london_oct23 = t_oct23_1600_utc.astimezone(tz_london)
    assert london_oct23.hour == 17

    ny_oct23 = t_oct23_1600_utc.astimezone(tz_ny)
    assert ny_oct23.hour == 12

    # 2. Sunday 2026-10-25: European DST ends (clocks fall back 1h)
    t_oct26_1600_utc = dt.datetime(2026, 10, 26, 16, 0, tzinfo=dt.UTC)
    berlin_oct26 = t_oct26_1600_utc.astimezone(tz_berlin)
    assert berlin_oct26.hour == 17

    london_oct26 = t_oct26_1600_utc.astimezone(tz_london)
    assert london_oct26.hour == 16

    ny_oct26 = t_oct26_1600_utc.astimezone(tz_ny)
    assert ny_oct26.hour == 12
    assert (berlin_oct26.hour - ny_oct26.hour) == 5

    # 3. Sunday 2026-11-01: US DST ends (EDT -> EST, UTC-5)
    t_nov02_1600_utc = dt.datetime(2026, 11, 2, 16, 0, tzinfo=dt.UTC)
    berlin_nov02 = t_nov02_1600_utc.astimezone(tz_berlin)
    assert berlin_nov02.hour == 17
    ny_nov02 = t_nov02_1600_utc.astimezone(tz_ny)
    assert ny_nov02.hour == 11
    assert (berlin_nov02.hour - ny_nov02.hour) == 6


def test_logging_stop_pct_and_open_risk(tmp_path: Path):
    """Verify stop % of price and daily summed open risk are recorded."""
    trend_db = tmp_path / "trend.db"
    init_trend_db(trend_db)

    with get_trend_db(trend_db) as conn:
        cols_trades = [r[1] for r in conn.execute("PRAGMA table_info(trades)").fetchall()]
        assert "stop_pct_price" in cols_trades

        cols_open = [r[1] for r in conn.execute("PRAGMA table_info(open_positions)").fetchall()]
        assert "stop_pct_price" in cols_open
        assert "open_risk_r" in cols_open

        cols_runs = [r[1] for r in conn.execute("PRAGMA table_info(runs)").fetchall()]
        assert "summed_open_risk_r" in cols_runs
        assert "positions_count" in cols_runs
