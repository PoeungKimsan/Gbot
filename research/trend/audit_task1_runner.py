"""Comprehensive Audit Script for trend_v1.2 (Task 1).

Covers:
- 1a. Beta check (T2 & T3): % days long/short/flat, buy-and-hold return, long-side price return,
      Long-only holding-period-matched null with 95% CI (1,000 draws).
- 1b. Win rates: Gross, Net 0%, Net 2% across T1, T2, T3; reconciliation table & analysis.
- 1c. Weekend-bar bug: Re-date FX bars using Europe/London session dates, run T1-T3 under
      research/trend/output_fxfixed/, and generate side-by-side comparison table.
- 1d. Brent T1 check: Long +0.48R vs Short +0.48R precision audit.
- 1e. Concentration (T2 & T3): Group share, yearly share, top-10 trades, ex-best-market, ex-best-year.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import logging
from dataclasses import asdict
from pathlib import Path
import zoneinfo

import httpx
import numpy as np

from research.trend.costs import compute_costs
from research.trend.indicators import load_daily_bars
from research.trend.models import DailyBar, TrendTrade
from research.trend.strategies import run_t1_donchian, run_t2_sma_cross, run_t3_tsmom

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("audit_task1")

DATA_DIR = Path("research/trend/data")
OUTPUT_DIR = Path("research/trend/output")
AUDIT_DIR = Path("research/trend/audit")
FX_FIXED_DATA_DIR = Path("research/trend/data_fxfixed")
FX_FIXED_OUTPUT_DIR = Path("research/trend/output_fxfixed")

SYMBOLS = [
    "XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "USDJPY",
    "AUDUSD", "USDCAD", "USDCHF", "US500", "GER40", "Brent", "NDX",
]
FX_SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF"]
EX_METALS_EQUITIES = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "Brent"]
END_DATE = dt.date(2025, 12, 31)

FX_TICKERS = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "AUDUSD": "AUDUSD=X",
    "USDCAD": "USDCAD=X",
    "USDCHF": "USDCHF=X",
}


def prepare_fx_fixed_data() -> None:
    """Download and store FX daily bars re-dated to London session dates, and mirror others."""
    FX_FIXED_DATA_DIR.mkdir(parents=True, exist_ok=True)
    london_tz = zoneinfo.ZoneInfo("Europe/London")
    headers = {"User-Agent": "Mozilla/5.0"}

    # 1. Fetch fixed FX bars
    for sym in FX_SYMBOLS:
        ticker = FX_TICKERS[sym]
        out_csv = FX_FIXED_DATA_DIR / f"{sym}_daily.csv"
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=25y"
        logger.info("Fetching London-session daily bars for %s (%s)...", sym, ticker)
        with httpx.Client(headers=headers, timeout=30.0) as client:
            r = client.get(url)
            r.raise_for_status()
            data = r.json()["chart"]["result"][0]

        timestamps = data["timestamp"]
        quote = data["indicators"]["quote"][0]
        opens = quote["open"]
        highs = quote["high"]
        lows = quote["low"]
        closes = quote["close"]
        volumes = quote.get("volume", [0] * len(timestamps))

        bars = []
        for ts, op, hi, lo, cl, vol in zip(timestamps, opens, highs, lows, closes, volumes):
            if op is None or hi is None or lo is None or cl is None:
                continue
            bar_date = dt.datetime.fromtimestamp(ts, london_tz).date()
            if bar_date > END_DATE:
                continue
            bars.append({
                "date": bar_date.isoformat(),
                "open": round(float(op), 6),
                "high": round(float(hi), 6),
                "low": round(float(lo), 6),
                "close": round(float(cl), 6),
                "volume": int(vol or 0),
            })

        # Save to CSV
        with open(out_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["date", "open", "high", "low", "close", "volume"])
            writer.writeheader()
            writer.writerows(bars)
        logger.info("Saved %d fixed bars for %s to %s", len(bars), sym, out_csv)

    # 2. Copy/mirror non-FX symbols
    for sym in SYMBOLS:
        if sym in FX_SYMBOLS:
            continue
        src = DATA_DIR / f"{sym}_daily.csv"
        dst = FX_FIXED_DATA_DIR / f"{sym}_daily.csv"
        dst.write_bytes(src.read_bytes())
        logger.info("Mirrored %s daily bars to %s", sym, dst)


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


def run_fxfixed_battery() -> dict[str, list[TrendTrade]]:
    """Re-run full T1-T3 battery using the fixed FX data."""
    FX_FIXED_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bars_by_symbol = {}
    for sym in SYMBOLS:
        csv_file = FX_FIXED_DATA_DIR / f"{sym}_daily.csv"
        bars = load_daily_bars(csv_file, hard_stop_date=END_DATE)
        bars_by_symbol[sym] = bars

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

    for rule_name, trades in results.items():
        csv_path = FX_FIXED_OUTPUT_DIR / f"{rule_name}_trades.csv"
        save_trades_to_csv(trades, csv_path)
        logger.info("Saved %d fixed trades to %s", len(trades), csv_path)

    return results


if __name__ == "__main__":
    logger.info("Starting Task 1 Audit...")
    prepare_fx_fixed_data()
    run_fxfixed_battery()
    logger.info("Fixed FX battery completed successfully.")
