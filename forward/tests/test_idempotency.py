"""Idempotency test: executing forward runner twice produces identical DB records."""

import datetime as dt
from pathlib import Path

from forward.db import get_trend_db
from forward.runner import run_forward_day

TEMP_TREND_DB = Path("forward/data/test_idempotent_trend.db")


def test_runner_idempotency(tmp_path: Path):
    db_path = tmp_path / "forward_trend.db"
    bars_db_path = Path("forward/data/forward_bars.sqlite")
    as_of = dt.date(2025, 12, 31)

    # First run
    res1 = run_forward_day(as_of_date=as_of, db_path=db_path, bars_db_path=bars_db_path)

    with get_trend_db(db_path) as conn:
        trades1 = conn.execute("SELECT * FROM trades ORDER BY trade_id").fetchall()
        carried1 = conn.execute("SELECT * FROM carried_trades ORDER BY trade_id").fetchall()

    # Second run on same DB
    res2 = run_forward_day(as_of_date=as_of, db_path=db_path, bars_db_path=bars_db_path)

    with get_trend_db(db_path) as conn:
        trades2 = conn.execute("SELECT * FROM trades ORDER BY trade_id").fetchall()
        carried2 = conn.execute("SELECT * FROM carried_trades ORDER BY trade_id").fetchall()

    assert res1["closed_trades_count"] == res2["closed_trades_count"]
    assert len(trades1) == len(trades2)
    assert len(carried1) == len(carried2)

    for r1, r2 in zip(trades1, trades2):
        assert r1["trade_id"] == r2["trade_id"]
        assert r1["gross_r"] == r2["gross_r"]
        assert r1["net_r_fin0"] == r2["net_r_fin0"]
        assert r1["net_r_fin2"] == r2["net_r_fin2"]
