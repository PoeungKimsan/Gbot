"""Research tool: Fetch daily bars and contract specifications from MetaTrader 5 terminal.

Read-only extraction from MT5 into research/trend/data_mt5/.
"""

from __future__ import annotations

import csv
import datetime as dt
from collections import defaultdict
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5
import yaml

DATA_DIR = Path("research/trend/data_mt5")
CONFIG_PATH = Path("config/trend_symbols.yaml")


def load_config() -> dict[str, Any]:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def fetch_all() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not mt5.initialize():
        raise RuntimeError(f"Failed to initialize MetaTrader 5: {mt5.last_error()}")

    try:
        cfg = load_config()
        symbols_map = cfg.get("symbols", {})

        now_utc = dt.datetime.now(dt.timezone.utc)
        utc_from = dt.datetime(2015, 1, 1, tzinfo=dt.timezone.utc)

        spec_rows: list[dict[str, Any]] = []
        quality_rows: list[dict[str, Any]] = []

        print(f"Fetching MT5 data for {len(symbols_map)} symbols...")

        for research_sym, meta in symbols_map.items():
            broker_sym = meta["broker_symbol"]
            category = meta.get("category", "")
            exchange_tz = meta.get("exchange_tz", "UTC")

            # Enable symbol in market watch
            mt5.symbol_select(broker_sym, True)
            info = mt5.symbol_info(broker_sym)
            if info is None:
                print(f"Warning: Symbol {broker_sym} not found in MT5!")
                continue

            # Record symbol specification snapshot
            spec_rows.append({
                "research_symbol": research_sym,
                "broker_symbol": broker_sym,
                "category": category,
                "description": info.description,
                "contract_size": info.trade_contract_size,
                "digits": info.digits,
                "tick_size": info.trade_tick_size,
                "tick_value": info.trade_tick_value,
                "min_lot": info.volume_min,
                "lot_step": info.volume_step,
                "swap_long": info.swap_long,
                "swap_short": info.swap_short,
                "swap_mode": info.swap_mode,
                "exchange_tz": exchange_tz,
            })

            # Fetch D1 rates
            rates = mt5.copy_rates_range(broker_sym, mt5.TIMEFRAME_D1, utc_from, now_utc)
            if rates is None or len(rates) == 0:
                print(f"Warning: No rates returned for {broker_sym}")
                continue

            # Filter out today's forming bar if incomplete
            # In MT5 D1, the bar time is the start of the day (00:00:00)
            today_utc_date = now_utc.date()
            bar_rows: list[dict[str, Any]] = []
            by_year = defaultdict(lambda: {"total": 0, "close_eq_open": 0, "zero_vol": 0})

            for r in rates:
                bar_dt = dt.datetime.fromtimestamp(r["time"], tz=dt.timezone.utc)
                bar_date = bar_dt.date()

                # If bar date is today or later, skip forming bar
                if bar_date >= today_utc_date:
                    continue

                op = round(float(r["open"]), info.digits)
                hi = round(float(r["high"]), info.digits)
                lo = round(float(r["low"]), info.digits)
                cl = round(float(r["close"]), info.digits)
                vol = int(r["tick_volume"])

                # Sanitize invariant
                hi = max(hi, op, cl)
                lo = min(lo, op, cl)

                bar_rows.append({
                    "date": bar_date.isoformat(),
                    "open": op,
                    "high": hi,
                    "low": lo,
                    "close": cl,
                    "volume": vol,
                })

                yr = bar_date.year
                by_year[yr]["total"] += 1
                if op == cl:
                    by_year[yr]["close_eq_open"] += 1
                if vol == 0:
                    by_year[yr]["zero_vol"] += 1

            # Save CSV for this symbol
            csv_path = DATA_DIR / f"{research_sym}.csv"
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=["date", "open", "high", "low", "close", "volume"])
                writer.writeheader()
                writer.writerows(bar_rows)

            print(f"Saved {len(bar_rows)} D1 bars for {research_sym} ({broker_sym}) -> {csv_path}")

            # Summarize quality
            for yr in sorted(by_year.keys()):
                tot = by_year[yr]["total"]
                eq = by_year[yr]["close_eq_open"]
                zv = by_year[yr]["zero_vol"]
                pct_eq = round((eq / tot) * 100.0, 2)
                quality_rows.append({
                    "symbol": research_sym,
                    "broker_symbol": broker_sym,
                    "year": yr,
                    "total_bars": tot,
                    "close_eq_open_count": eq,
                    "close_eq_open_pct": f"{pct_eq}%",
                    "zero_vol_count": zv,
                })

        # Save symbol specifications
        spec_csv = DATA_DIR / "symbol_specs.csv"
        with open(spec_csv, "w", newline="", encoding="utf-8") as f:
            if spec_rows:
                writer = csv.DictWriter(f, fieldnames=list(spec_rows[0].keys()))
                writer.writeheader()
                writer.writerows(spec_rows)
        print(f"Saved symbol specifications -> {spec_csv}")

        # Save data quality report
        quality_csv = DATA_DIR / "data_quality_report.csv"
        with open(quality_csv, "w", newline="", encoding="utf-8") as f:
            if quality_rows:
                writer = csv.DictWriter(f, fieldnames=list(quality_rows[0].keys()))
                writer.writeheader()
                writer.writerows(quality_rows)
        print(f"Saved data quality report -> {quality_csv}")

    finally:
        mt5.shutdown()


if __name__ == "__main__":
    fetch_all()
