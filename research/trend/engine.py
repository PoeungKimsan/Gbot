"""Trend research backtest engine across all 12 liquid markets."""

from __future__ import annotations

import csv
import datetime as dt
import json
import logging
from dataclasses import asdict
from pathlib import Path

from research.trend.costs import PREREG_COMMIT, PREREG_HASH
from research.trend.indicators import load_daily_bars
from research.trend.models import TrendTrade
from research.trend.strategies import run_t1_donchian, run_t2_sma_cross, run_t3_tsmom

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("trend.engine")

DATA_DIR = Path("research/trend/data")
OUTPUT_DIR = Path("research/trend/output")

SYMBOLS = [
    "XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "USDJPY",
    "AUDUSD", "USDCAD", "USDCHF", "US500", "GER40", "Brent", "NDX",
]

START_DATE = dt.date(2010, 1, 1)
END_DATE = dt.date(2025, 12, 31)


def save_trades_to_csv(trades: list[TrendTrade], out_path: Path) -> None:
    if not trades:
        return
    fieldnames = list(asdict(trades[0]).keys())
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for t in trades:
            d = asdict(t)
            d["entry_date"] = t.entry_date.isoformat()
            d["exit_date"] = t.exit_date.isoformat()
            writer.writerow(d)


def run_all(run_number: int = 1) -> dict[str, list[TrendTrade]]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("Executing Full Backtest Run #%d across 12 markets...", run_number)
    logger.info("Pre-reg SHA256: %s | Commit: %s", PREREG_HASH, PREREG_COMMIT)

    bars_by_symbol = {}
    for sym in SYMBOLS:
        csv_file = DATA_DIR / f"{sym}_daily.csv"
        if not csv_file.is_file():
            raise FileNotFoundError(f"Missing data file: {csv_file}")
        bars = load_daily_bars(csv_file, hard_stop_date=END_DATE)
        bars_by_symbol[sym] = bars
        logger.info("Loaded %d bars for %s", len(bars), sym)

    results: dict[str, list[TrendTrade]] = {
        "T1_DONCHIAN": [],
        "T2_SMA_CROSS": [],
        "T3_TSMOM": [],
    }

    for sym in SYMBOLS:
        bars = bars_by_symbol[sym]
        t1 = run_t1_donchian(sym, bars, start_date=None, end_date=END_DATE)
        t2 = run_t2_sma_cross(sym, bars, start_date=None, end_date=END_DATE)
        t3 = run_t3_tsmom(sym, bars, start_date=None, end_date=END_DATE)

        results["T1_DONCHIAN"].extend(t1)
        results["T2_SMA_CROSS"].extend(t2)
        results["T3_TSMOM"].extend(t3)

        logger.info(
            "%s: T1 Donchian=%d trades, T2 SMA Cross=%d trades, T3 TSMOM=%d trades",
            sym, len(t1), len(t2), len(t3),
        )

    # Save per-rule pooled CSVs
    for rule_name, trades in results.items():
        csv_path = OUTPUT_DIR / f"{rule_name}_trades.csv"
        save_trades_to_csv(trades, csv_path)
        logger.info("Saved %d trades to %s", len(trades), csv_path)

    # Save run metadata
    run_meta = {
        "run_number": run_number,
        "timestamp_utc": dt.datetime.now(dt.UTC).isoformat(),
        "prereg_sha256": PREREG_HASH,
        "prereg_commit": PREREG_COMMIT,
        "evaluation_window": f"earliest+1yr to {END_DATE.isoformat()}",
        "trade_counts": {k: len(v) for k, v in results.items()},
    }
    with open(OUTPUT_DIR / "run_meta.json", "w", encoding="utf-8") as f:
        json.dump(run_meta, f, indent=2)

    return results


if __name__ == "__main__":
    run_all(run_number=3)

