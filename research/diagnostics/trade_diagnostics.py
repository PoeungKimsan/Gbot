"""Observational trade diagnostics on existing baseline trades (2023-2025).

Calculates:
1. Share of existing baseline trades reaching +1R, +2R, +3R MFE before exit.
2. Share of stop-outs occurring within 1x broker spread distance ($0.20) of the stop loss.

STRICT RULE: Strictly observational. Do not tune from it.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import logging
from decimal import Decimal
from pathlib import Path
from typing import Any

from research.data.loader import load_all_m5_bars

logger = logging.getLogger("research.diagnostics")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def run_trade_diagnostics(
    trades_csv_path: str | Path = "research/output/20261011_070320/trades_backtest.csv",
    cache_dir: str | Path = "research/data/cache",
    spread_dollars: Decimal = Decimal("0.20"),
) -> dict[str, Any]:
    """Calculate MFE reach and spread-vulnerability distribution."""
    trades_path = Path(trades_csv_path)
    if not trades_path.is_file():
        logger.warning("Trades file %s does not exist; skipping diagnostics.", trades_path)
        return {
            "timestamp_utc": dt.datetime.now(dt.UTC).isoformat(),
            "status": "NO_TRADES_FILE",
            "message": f"Trades file {trades_path} not found.",
        }

    logger.info("Loading trades from %s...", trades_path)
    trades: list[dict[str, str]] = []
    with trades_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            trades.append(row)

    logger.info("Loaded %d trades. Loading bars from cache...", len(trades))
    bars = load_all_m5_bars(cache_dir=cache_dir, years=["2023", "2024", "2025"])
    if not bars:
        logger.info("No bars loaded from %s (pruned).", cache_dir)
        return {
            "timestamp_utc": dt.datetime.now(dt.UTC).isoformat(),
            "status": "NO_BAR_DATA",
            "message": (
                "Historical M5 bar cache was pruned; see research/reports/ for archived reports."
            ),
        }

    # Index bars by timestamp for fast range slicing
    bar_timestamps = [b.timestamp_utc for b in bars]

    judas_trades = [t for t in trades if "Judas" in t["strategy"]]
    sb_trades = [t for t in trades if "Silver" in t["strategy"]]

    def _analyze_subset(subset: list[dict[str, str]], name: str) -> dict[str, Any]:
        if not subset:
            return {}

        n_total = len(subset)
        reached_1r = 0
        reached_2r = 0
        reached_3r = 0

        # Analysis of stop-outs
        stop_outs = [t for t in subset if "STOP" in t["exit_reason"]]
        n_stop_outs = len(stop_outs)
        stops_within_spread = 0

        for t in subset:
            entry_time = dt.datetime.fromisoformat(t["entry_time"])
            exit_time = dt.datetime.fromisoformat(t["exit_time"])
            side = t["side"]
            entry = Decimal(t["entry_price"])
            sl = Decimal(t["stop_loss"])
            risk = abs(entry - sl)
            if risk == Decimal("0"):
                continue

            # Find bars between entry and exit
            start_i = None
            end_i = None
            # Binary search or scan
            import bisect
            start_i = bisect.bisect_left(bar_timestamps, entry_time)
            end_i = bisect.bisect_right(bar_timestamps, exit_time)

            trade_bars = bars[start_i:end_i]
            max_favorable = Decimal("0")
            max_adverse_past_stop = Decimal("0")

            for b in trade_bars:
                mid = b.mid_ohlc
                if side == "BUY":
                    fav = mid.high - entry
                    if fav > max_favorable:
                        max_favorable = fav
                    if mid.low <= sl:
                        adv_past = sl - mid.low
                        if adv_past > max_adverse_past_stop:
                            max_adverse_past_stop = adv_past
                else:  # SELL
                    fav = entry - mid.low
                    if fav > max_favorable:
                        max_favorable = fav
                    if mid.high >= sl:
                        adv_past = mid.high - sl
                        if adv_past > max_adverse_past_stop:
                            max_adverse_past_stop = adv_past

            mfe_r = float(max_favorable / risk)
            if mfe_r >= 1.0:
                reached_1r += 1
            if mfe_r >= 2.0:
                reached_2r += 1
            if mfe_r >= 3.0:
                reached_3r += 1

            if "STOP" in t["exit_reason"] and max_adverse_past_stop <= spread_dollars:
                stops_within_spread += 1

        pct_1r = reached_1r / n_total if n_total > 0 else 0.0
        pct_2r = reached_2r / n_total if n_total > 0 else 0.0
        pct_3r = reached_3r / n_total if n_total > 0 else 0.0
        pct_stop_within_spread = stops_within_spread / n_stop_outs if n_stop_outs > 0 else 0.0

        return {
            "strategy": name,
            "total_trades": n_total,
            "share_reaching_1r_mfe": round(pct_1r, 4),
            "count_reaching_1r": reached_1r,
            "share_reaching_2r_mfe": round(pct_2r, 4),
            "count_reaching_2r": reached_2r,
            "share_reaching_3r_mfe": round(pct_3r, 4),
            "count_reaching_3r": reached_3r,
            "total_stop_outs": n_stop_outs,
            "stops_within_spread_distance": stops_within_spread,
            "share_stops_within_spread": round(pct_stop_within_spread, 4),
        }

    judas_diag = _analyze_subset(judas_trades, "Asian Judas Swing")
    sb_diag = _analyze_subset(sb_trades, "ICT Silver Bullet")

    diag_report = {
        "timestamp_utc": dt.datetime.now(dt.UTC).isoformat(),
        "spread_distance_assumed": str(spread_dollars),
        "judas_swing": judas_diag,
        "silver_bullet": sb_diag,
    }

    out_path = Path("research/output/baseline_diagnostics.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(diag_report, indent=2), encoding="utf-8")
    logger.info("Diagnostics report written to %s", out_path)
    return diag_report


if __name__ == "__main__":
    report = run_trade_diagnostics()
    print(json.dumps(report, indent=2))
