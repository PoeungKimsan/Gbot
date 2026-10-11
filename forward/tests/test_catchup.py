"""Test multi-day catch-up behavior over missed cron days."""

import datetime as dt
from pathlib import Path

from forward.db import get_trend_db
from forward.runner import run_forward_day


def test_missed_days_catchup(tmp_path: Path):
    db_seq = tmp_path / "seq.db"
    db_catchup = tmp_path / "catchup.db"
    bars_db_path = Path("forward/data/forward_bars.sqlite")

    dates = [
        dt.date(2025, 6, 2),
        dt.date(2025, 6, 3),
        dt.date(2025, 6, 4),
        dt.date(2025, 6, 5),
    ]

    # Sequential: run each day
    for d in dates:
        run_forward_day(as_of_date=d, db_path=db_seq, bars_db_path=bars_db_path)

    # Catch-up: run day 1, then skip straight to day 4
    run_forward_day(as_of_date=dates[0], db_path=db_catchup, bars_db_path=bars_db_path)
    run_forward_day(as_of_date=dates[-1], db_path=db_catchup, bars_db_path=bars_db_path)

    with get_trend_db(db_seq) as conn_seq:
        seq_trades = conn_seq.execute("SELECT * FROM trades ORDER BY trade_id").fetchall()

    with get_trend_db(db_catchup) as conn_catchup:
        catchup_trades = conn_catchup.execute("SELECT * FROM trades ORDER BY trade_id").fetchall()

    assert len(seq_trades) == len(catchup_trades)
    for s, c in zip(seq_trades, catchup_trades):
        assert s["trade_id"] == c["trade_id"]
        assert s["exit_date"] == c["exit_date"]
        assert s["net_r_fin0"] == c["net_r_fin0"]
