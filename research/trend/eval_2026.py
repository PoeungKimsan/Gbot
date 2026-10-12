"""Evaluation of Sealed 2026 Data for Qualified Trend Rules.

Protocol Mandate:
- Sealed 2026 opens once, ONLY for rules qualifying under (a) SURVIVOR or (b) GROSS-OF-FINANCING SURVIVOR.
- Evaluation runs at the financing level that qualified them.
- If no rule qualifies, 2026 data remains strictly unopened.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

from research.trend.indicators import load_daily_bars
from research.trend.models import TrendTrade
from research.trend.strategies import run_t2_sma_cross, run_t3_tsmom

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("trend.eval_2026")

DATA_DIR_2026 = Path("research/trend/data/sealed_2026")
OUTPUT_DIR = Path("research/trend/output")
SYMBOLS = [
    "XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "USDJPY",
    "AUDUSD", "USDCAD", "USDCHF", "US500", "GER40", "Brent", "NDX",
]
END_2026 = dt.date(2026, 12, 31)


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


def run_sealed_2026_evaluation() -> dict[str, Any]:
    # 1. Load verdict ladder to ensure eligibility
    with open(OUTPUT_DIR / "statistics_summary.json", encoding="utf-8") as f:
        stats = json.load(f)
    with open(OUTPUT_DIR / "null_distributions_summary.json", encoding="utf-8") as f:
        nulls = json.load(f)

    qualified_rules = {}
    for r in ["T1_DONCHIAN", "T2_SMA_CROSS", "T3_TSMOM"]:
        st = stats[r]
        nl = nulls[r]
        # Check (a) SURVIVOR at 2%
        pass_fin2 = (
            st["pooled"]["n"] >= 200
            and st["pooled"]["mean_net_r"] > 0
            and st["bootstrap"]["passes_holm_5pct"]
            and nl["passes_both_nulls"]
            and st["by_sub_period"]["2010_2017"]["mean_net_r"] > 0
            and st["by_sub_period"]["2018_2025"]["mean_net_r"] > 0
            and st["ex_metals"]["mean_net_r"] > 0
        )
        # Check (b) GROSS-OF-FINANCING SURVIVOR at 0%
        pass_fin0 = (
            st["pooled_fin0"]["n"] >= 200
            and st["pooled_fin0"]["mean_net_r"] > 0
            and st["bootstrap_fin0"]["passes_holm_5pct"]
            and nl["fin0"]["passes_both_nulls"]
            and st["by_sub_period_fin0"]["2010_2017"]["mean_net_r"] > 0
            and st["by_sub_period_fin0"]["2018_2025"]["mean_net_r"] > 0
            and st["ex_metals_fin0"]["mean_net_r"] > 0
        )
        if pass_fin2:
            qualified_rules[r] = {"tier": "SURVIVOR", "qualifying_financing": 0.02}
        elif pass_fin0:
            qualified_rules[r] = {"tier": "GROSS-OF-FINANCING SURVIVOR", "qualifying_financing": 0.00}

    logger.info("Qualified Rules for 2026 Evaluation: %s", qualified_rules)

    if not qualified_rules:
        logger.info("No rules qualified for 2026 unsealing. 2026 data remains unopened.")
        return {"unsealed": False, "reason": "No rule qualified under verdict ladder"}

    runners = {
        "T2_SMA_CROSS": run_t2_sma_cross,
        "T3_TSMOM": run_t3_tsmom,
    }

    eval_2026_summary: dict[str, Any] = {
        "unsealed": True,
        "timestamp_utc": dt.datetime.now(dt.UTC).isoformat(),
        "qualified_rules": qualified_rules,
        "rules_evaluated": {},
    }

    for rule_name, meta in qualified_rules.items():
        runner = runners.get(rule_name)
        if not runner:
            continue
        qual_fin = meta["qualifying_financing"]
        logger.info("Evaluating %s on 2026 data at financing rate %.1f%%...", rule_name, qual_fin * 100)

        all_trades: list[TrendTrade] = []
        fresh_2026_entries: list[TrendTrade] = []
        carried_into_2026: list[TrendTrade] = []

        for sym in SYMBOLS:
            csv_path = DATA_DIR_2026 / f"{sym}_daily_2026.csv"
            bars = load_daily_bars(csv_path, hard_stop_date=None)
            trades = runner(sym, bars, start_date=None, end_date=END_2026)
            for t in trades:
                if t.entry_date >= dt.date(2026, 1, 1):
                    fresh_2026_entries.append(t)
                    all_trades.append(t)
                elif t.exit_date >= dt.date(2026, 1, 1):
                    carried_into_2026.append(t)
                    all_trades.append(t)

        save_trades_to_csv(all_trades, OUTPUT_DIR / f"{rule_name}_2026_all_trades.csv")
        save_trades_to_csv(fresh_2026_entries, OUTPUT_DIR / f"{rule_name}_2026_fresh_trades.csv")

        # Compute metrics
        net_key = "net_r_fin0" if qual_fin == 0.0 else "net_r"

        # Fresh 2026 entries metrics
        n_fresh = len(fresh_2026_entries)
        if n_fresh > 0:
            fresh_net_rs = [getattr(t, net_key) for t in fresh_2026_entries]
            fresh_wins = [r for r in fresh_net_rs if r > 0]
            fresh_mean_r = float(sum(fresh_net_rs) / n_fresh)
            fresh_win_rate = float(len(fresh_wins) / n_fresh)
        else:
            fresh_mean_r = 0.0
            fresh_win_rate = 0.0

        # Carried trades metrics
        n_carried = len(carried_into_2026)
        if n_carried > 0:
            carried_net_rs = [getattr(t, net_key) for t in carried_into_2026]
            carried_wins = [r for r in carried_net_rs if r > 0]
            carried_mean_r = float(sum(carried_net_rs) / n_carried)
            carried_win_rate = float(len(carried_wins) / n_carried)
        else:
            carried_mean_r = 0.0
            carried_win_rate = 0.0

        eval_2026_summary["rules_evaluated"][rule_name] = {
            "tier": meta["tier"],
            "qualifying_financing": qual_fin,
            "fresh_2026_entries": {
                "n": n_fresh,
                "mean_net_r": round(fresh_mean_r, 4),
                "win_rate": round(fresh_win_rate, 4),
                "trades": [
                    {
                        "symbol": t.symbol,
                        "direction": t.direction,
                        "entry_date": t.entry_date.isoformat(),
                        "exit_date": t.exit_date.isoformat(),
                        "exit_reason": t.exit_reason,
                        "holding_days": t.holding_days,
                        "gross_r": round(t.gross_r, 4),
                        "net_r_fin0": round(t.net_r_fin0, 4),
                        "net_r_fin2": round(t.net_r, 4),
                    }
                    for t in fresh_2026_entries
                ],
            },
            "carried_positions_exiting_2026": {
                "n": n_carried,
                "mean_net_r": round(carried_mean_r, 4),
                "win_rate": round(carried_win_rate, 4),
                "trades": [
                    {
                        "symbol": t.symbol,
                        "direction": t.direction,
                        "entry_date": t.entry_date.isoformat(),
                        "exit_date": t.exit_date.isoformat(),
                        "exit_reason": t.exit_reason,
                        "holding_days": t.holding_days,
                        "gross_r": round(t.gross_r, 4),
                        "net_r_fin0": round(t.net_r_fin0, 4),
                        "net_r_fin2": round(t.net_r, 4),
                    }
                    for t in carried_into_2026
                ],
            },
        }

    out_file = OUTPUT_DIR / "sealed_2026_evaluation.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(eval_2026_summary, f, indent=2)
    logger.info("Saved sealed 2026 evaluation to %s", out_file)
    return eval_2026_summary


if __name__ == "__main__":
    run_sealed_2026_evaluation()
