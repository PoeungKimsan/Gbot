"""Test handling of weekends, non-trading days, and holidays."""

import datetime as dt
from pathlib import Path

from forward.db import get_trend_db
from forward.runner import run_forward_day


def test_weekend_execution_graceful(tmp_path: Path):
    db_path = tmp_path / "weekend.db"
    bars_db_path = Path("forward/data/forward_bars.sqlite")

    friday_date = dt.date(2025, 10, 17)
    saturday_date = dt.date(2025, 10, 18)
    sunday_date = dt.date(2025, 10, 19)

    # Run on Friday
    res_fri = run_forward_day(as_of_date=friday_date, db_path=db_path, bars_db_path=bars_db_path)

    with get_trend_db(db_path) as conn:
        trades_fri = conn.execute("SELECT * FROM trades ORDER BY trade_id").fetchall()

    # Run on Saturday (no new bars in market)
    res_sat = run_forward_day(as_of_date=saturday_date, db_path=db_path, bars_db_path=bars_db_path)

    with get_trend_db(db_path) as conn:
        trades_sat = conn.execute("SELECT * FROM trades ORDER BY trade_id").fetchall()

    # Run on Sunday
    res_sun = run_forward_day(as_of_date=sunday_date, db_path=db_path, bars_db_path=bars_db_path)

    with get_trend_db(db_path) as conn:
        trades_sun = conn.execute("SELECT * FROM trades ORDER BY trade_id").fetchall()

    # Invariant: Running over the weekend generates 0 spurious trades or duplicate records
    assert len(trades_fri) == len(trades_sat) == len(trades_sun)
    assert res_fri["closed_trades_count"] == res_sat["closed_trades_count"] == res_sun["closed_trades_count"]
