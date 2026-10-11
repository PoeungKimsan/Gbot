"""As-of replay test across 2024-2025 proving zero look-ahead bias."""

import datetime as dt
from pathlib import Path

from forward.db import get_trend_db
from forward.runner import run_forward_day


def test_as_of_replay_no_lookahead(tmp_path: Path):
    """Verify that truncating bars at as-of date reproduces identical closed trades as full batch."""
    bars_db_path = Path("forward/data/forward_bars.sqlite")
    full_db_path = tmp_path / "full_batch.db"

    # 1. Full batch run up to 2025-12-31
    run_forward_day(as_of_date=dt.date(2025, 12, 31), db_path=full_db_path, bars_db_path=bars_db_path)
    with get_trend_db(full_db_path) as conn:
        full_trades = conn.execute("SELECT * FROM trades ORDER BY exit_date ASC").fetchall()

    # 2. Checkpoints across the last 2 years (2024 - 2025)
    checkpoints = [
        dt.date(2024, 3, 31),
        dt.date(2024, 6, 30),
        dt.date(2024, 9, 30),
        dt.date(2024, 12, 31),
        dt.date(2025, 6, 30),
    ]

    for cp_date in checkpoints:
        cp_db_path = tmp_path / f"cp_{cp_date.isoformat()}.db"
        # Run truncated strictly at cp_date
        run_forward_day(as_of_date=cp_date, db_path=cp_db_path, bars_db_path=bars_db_path)

        with get_trend_db(cp_db_path) as cp_conn:
            # Naturally closed trades (not truncated PERIOD_END synthetic closes)
            cp_trades = cp_conn.execute(
                "SELECT * FROM trades WHERE exit_reason != 'PERIOD_END' ORDER BY exit_date ASC"
            ).fetchall()

        # Find trades from full batch that closed on or before cp_date with natural exit
        expected_subset = [
            t for t in full_trades
            if t["exit_date"] <= cp_date.isoformat() and t["exit_reason"] != "PERIOD_END"
        ]

        assert len(cp_trades) == len(expected_subset), (
            f"As-of count mismatch at {cp_date}: truncated had {len(cp_trades)}, full had {len(expected_subset)}"
        )

        # Assert every trade that closed on or before cp_date has exact same parameters
        for t_act, t_exp in zip(cp_trades, expected_subset):
            assert t_act["trade_id"] == t_exp["trade_id"]
            assert t_act["entry_date"] == t_exp["entry_date"]
            assert t_act["entry_price"] == t_exp["entry_price"]
            assert t_act["stop_loss"] == t_exp["stop_loss"]
            assert t_act["exit_date"] == t_exp["exit_date"]
            assert t_act["exit_price"] == t_exp["exit_price"]
            assert t_act["net_r_fin0"] == t_exp["net_r_fin0"]
