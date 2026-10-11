"""Populate forward/data/forward_bars.sqlite from verified data_fxfixed CSVs."""

import csv
import datetime as dt
import logging
from pathlib import Path

from forward.config import BARS_DB_PATH, SYMBOLS
from forward.db import get_bars_db, init_bars_db

logger = logging.getLogger("init_bars_sqlite")
SRC_DIR = Path("research/trend/data_fxfixed")


def populate_bars_db():
    init_bars_db(BARS_DB_PATH)
    now_utc_str = dt.datetime.now(dt.UTC).isoformat()

    with get_bars_db(BARS_DB_PATH) as conn:
        for sym in SYMBOLS:
            csv_path = SRC_DIR / f"{sym}_daily.csv"
            if not csv_path.is_file():
                continue
            with open(csv_path, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)

            count = 0
            for r in rows:
                b_date = r["date"]
                op = float(r["open"])
                hi = float(r["high"])
                lo = float(r["low"])
                cl = float(r["close"])
                vol = int(r.get("volume", 0) or 0)

                sanitized = 1 if (hi < max(op, cl) or lo > min(op, cl) or hi < lo) else 0
                hi = max(hi, op, cl)
                lo = min(lo, op, cl)

                conn.execute(
                    """
                    INSERT OR REPLACE INTO bars (
                        symbol, date, open, high, low, close, volume, fetch_timestamp, sanitized_hl
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (sym, b_date, op, hi, lo, cl, vol, now_utc_str, sanitized),
                )
                count += 1
            print(f"Populated {count} bars for {sym} into {BARS_DB_PATH}")


if __name__ == "__main__":
    populate_bars_db()
