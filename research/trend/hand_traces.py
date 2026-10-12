"""Deterministic Hand-Traces for Trend Rules (5 per rule against raw daily bars)."""

from __future__ import annotations

import csv
import datetime as dt
import json
import logging
from pathlib import Path
from typing import Any

from research.trend.costs import compute_costs
from research.trend.indicators import load_daily_bars

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("trend.hand_traces")

DATA_DIR = Path("research/trend/data")
OUTPUT_DIR = Path("research/trend/output")
RULES = ["T1_DONCHIAN", "T2_SMA_CROSS", "T3_TSMOM"]
END_DATE = dt.date(2025, 12, 31)


def generate_hand_traces() -> dict[str, list[dict[str, Any]]]:
    # Preload bars for lookup
    symbols = [
        "XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "USDJPY",
        "AUDUSD", "USDCAD", "USDCHF", "US500", "GER40", "Brent", "NDX",
    ]
    bars_by_sym = {s: load_daily_bars(DATA_DIR / f"{s}_daily.csv", hard_stop_date=END_DATE) for s in symbols}
    date_to_bar = {s: {b.date: b for b in bars_by_sym[s]} for s in symbols}

    traces_by_rule: dict[str, list[dict[str, Any]]] = {}

    for rule in RULES:
        csv_p = OUTPUT_DIR / f"{rule}_trades.csv"
        trades: list[dict[str, Any]] = []
        with open(csv_p, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                trades.append(row)

        # Select 5 diverse trades: trade 1, trade 5, trade 15, a stopped trade, a channel/cross/sign exit
        selected = []
        stopped_found = False
        normal_exit_found = False

        # First grab index 0 and 2
        if len(trades) > 0:
            selected.append(trades[0])
        if len(trades) > 5:
            selected.append(trades[5])
        if len(trades) > 12:
            selected.append(trades[12])

        # Then find specific exit reasons
        for t in trades[15:]:
            if len(selected) >= 5:
                break
            if t["is_stop"].lower() == "true" and not stopped_found:
                selected.append(t)
                stopped_found = True
            elif t["is_stop"].lower() == "false" and not normal_exit_found:
                selected.append(t)
                normal_exit_found = True

        while len(selected) < 5 and len(selected) < len(trades):
            candidate = trades[len(selected) * 7 % len(trades)]
            if candidate not in selected:
                selected.append(candidate)

        rule_traces = []
        for t in selected:
            sym = t["symbol"]
            e_date = dt.date.fromisoformat(t["entry_date"])
            x_date = dt.date.fromisoformat(t["exit_date"])
            e_bar = date_to_bar[sym].get(e_date)
            x_bar = date_to_bar[sym].get(x_date)

            # Signal bar is the bar immediately prior to entry
            sym_bars = bars_by_sym[sym]
            e_idx = next((i for i, b in enumerate(sym_bars) if b.date == e_date), 0)
            sig_bar = sym_bars[e_idx - 1] if e_idx > 0 else e_bar

            # Verify costs from scratch
            e_p = float(t["entry_price"])
            x_p = float(t["exit_price"])
            s_dist = float(t["stop_distance"])
            is_st = t["is_stop"].lower() == "true"
            dir_ = t["direction"]

            recalc = compute_costs(
                symbol=sym,
                direction=dir_,
                entry_price=e_p,
                exit_price=x_p,
                stop_distance=s_dist,
                is_stop=is_st,
                entry_date=e_date,
                exit_date=x_date,
            )

            trace_entry = {
                "trade_id": t["trade_id"],
                "rule": rule,
                "symbol": sym,
                "direction": dir_,
                "signal_bar": {
                    "date": sig_bar.date.isoformat(),
                    "close": sig_bar.close,
                    "atr20": round(sig_bar.atr20, 5),
                    "sma50": round(sig_bar.sma50, 4),
                    "sma200": round(sig_bar.sma200, 4),
                    "donchian_high55": round(sig_bar.donchian_high55, 4),
                    "donchian_low55": round(sig_bar.donchian_low55, 4),
                    "donchian_high20": round(sig_bar.donchian_high20, 4),
                    "donchian_low20": round(sig_bar.donchian_low20, 4),
                    "return_252": round(sig_bar.return_252, 4),
                },
                "entry_bar": {
                    "date": e_date.isoformat(),
                    "open": round(e_bar.open, 5) if e_bar else e_p,
                    "high": round(e_bar.high, 5) if e_bar else e_p,
                    "low": round(e_bar.low, 5) if e_bar else e_p,
                    "close": round(e_bar.close, 5) if e_bar else e_p,
                },
                "stop_loss_price": float(t["stop_loss"]),
                "stop_distance_pts": s_dist,
                "stop_distance_in_r": 1.0,
                "exit_bar": {
                    "date": x_date.isoformat(),
                    "open": round(x_bar.open, 5) if x_bar else x_p,
                    "high": round(x_bar.high, 5) if x_bar else x_p,
                    "low": round(x_bar.low, 5) if x_bar else x_p,
                    "close": round(x_bar.close, 5) if x_bar else x_p,
                },
                "exit_price": x_p,
                "exit_reason": t["exit_reason"],
                "is_stop": is_st,
                "holding_days": recalc["holding_days"],
                "gross_pnl_pts": round(recalc["gross_pnl_pts"], 5),
                "gross_r": round(recalc["gross_r"], 4),
                "spread_cost_pts": round(recalc["spread_cost_pts"], 5),
                "slippage_cost_pts": round(recalc["slippage_cost_pts"], 5),
                "financing_cost_pts": round(recalc["financing_cost_pts"], 5),
                "drag_spread_r": round(recalc["drag_spread_r"], 4),
                "drag_slippage_r": round(recalc["drag_slippage_r"], 4),
                "drag_financing_r": round(recalc["drag_financing_r"], 4),
                "net_r": round(recalc["net_r"], 4),
                "net_r_fin0": round(recalc["net_r_fin0"], 4),
                "matches_csv_net_r": abs(recalc["net_r"] - float(t["net_r"])) < 1e-4,
            }
            rule_traces.append(trace_entry)

        traces_by_rule[rule] = rule_traces

    out_json = OUTPUT_DIR / "hand_traces_audit.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(traces_by_rule, f, indent=2)
    logger.info("Saved 15 hand traces (5 per rule) to %s", out_json)

    return traces_by_rule


if __name__ == "__main__":
    generate_hand_traces()
