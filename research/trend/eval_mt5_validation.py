"""Evaluation engine for trend_v1.3 on MT5 broker data."""

from __future__ import annotations

import csv
import datetime as dt
import math
from pathlib import Path
from typing import Any

import numpy as np

from research.trend.costs import FROZEN_SPREAD_TABLE, STOP_SLIPPAGE_MULTIPLIER
from research.trend.indicators import load_daily_bars
from research.trend.models import TrendTrade
from research.trend.strategies import run_t2_sma_cross, run_t3_tsmom

DATA_DIR = Path("research/trend/data_mt5")
OUTPUT_DIR = Path("research/trend/output_mt5_validation")
SPECS_PATH = DATA_DIR / "symbol_specs.csv"

SEED = 42
BOOTSTRAP_ROUNDS = 10_000


def load_specs() -> dict[str, dict[str, Any]]:
    specs = {}
    with open(SPECS_PATH, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            sym = r["research_symbol"]
            digits = int(r["digits"])
            point_size = 10.0 ** (-digits)
            specs[sym] = {
                "broker_symbol": r["broker_symbol"],
                "category": r["category"],
                "digits": digits,
                "point_size": point_size,
                "swap_long": float(r["swap_long"]),
                "swap_short": float(r["swap_short"]),
                "swap_mode": int(r["swap_mode"]),
            }
    return specs


def compute_broker_swap_r(trade: TrendTrade, spec: dict[str, Any]) -> tuple[float, float, float]:
    """Returns (drag_swap_r, net_r_fin0, net_r_broker_swap)."""
    spread = FROZEN_SPREAD_TABLE[trade.symbol] * 1.5
    slippage = (STOP_SLIPPAGE_MULTIPLIER * spread) if trade.is_stop else 0.0

    drag_spread_r = spread / trade.stop_distance
    drag_slip_r = slippage / trade.stop_distance

    net_r_fin0 = trade.gross_r - drag_spread_r - drag_slip_r

    # Broker point swap
    swap_points = spec["swap_long"] if trade.direction == "LONG" else spec["swap_short"]
    point_size = spec["point_size"]
    # Negative swap means cost to account
    swap_cost_per_day_pts = abs(swap_points) * point_size if swap_points < 0 else 0.0
    total_swap_drag_pts = swap_cost_per_day_pts * trade.holding_days
    drag_swap_r = total_swap_drag_pts / trade.stop_distance

    net_r_broker_swap = trade.gross_r - drag_spread_r - drag_slip_r - drag_swap_r
    return drag_swap_r, net_r_fin0, net_r_broker_swap


def month_block_bootstrap(trades: list[dict[str, Any]], key: str = "net_r_broker_swap") -> tuple[float, float]:
    """Month-block bootstrap 95% CI."""
    if not trades:
        return 0.0, 0.0

    # Group trades by exit month
    by_month: dict[str, list[float]] = {}
    for t in trades:
        m = t["exit_date"][:7]
        by_month.setdefault(m, []).append(t[key])

    months = list(by_month.keys())
    m_count = len(months)
    if m_count < 2:
        vals = [t[key] for t in trades]
        return float(np.mean(vals)), float(np.mean(vals))

    rng = np.random.default_rng(SEED)
    means = np.empty(BOOTSTRAP_ROUNDS)

    for b in range(BOOTSTRAP_ROUNDS):
        sample_months = rng.choice(months, size=m_count, replace=True)
        sample_trades = [r for sm in sample_months for r in by_month[sm]]
        means[b] = np.mean(sample_trades) if sample_trades else 0.0

    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def run_evaluation() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    specs = load_specs()

    all_symbols = list(specs.keys())

    # Load bars for each symbol
    bars_by_sym = {}
    for sym in all_symbols:
        csv_file = DATA_DIR / f"{sym}.csv"
        bars_by_sym[sym] = load_daily_bars(csv_file)

    rules = [
        ("T2_SMA_CROSS", run_t2_sma_cross),
        ("T3_TSMOM", run_t3_tsmom),
    ]

    master_results = []
    leave_out_results = []

    for rule_name, rule_fn in rules:
        raw_trades: list[TrendTrade] = []
        for sym in all_symbols:
            bars = bars_by_sym[sym]
            trades = rule_fn(sym, bars, start_date=bars[0].date, end_date=bars[-1].date)
            raw_trades.extend(trades)

        # Process trades into rich dicts
        processed: list[dict[str, Any]] = []
        for t in raw_trades:
            spec = specs[t.symbol]
            drag_swap_r, fin0_r, swap_r = compute_broker_swap_r(t, spec)
            processed.append({
                "trade_id": t.trade_id,
                "rule": rule_name,
                "symbol": t.symbol,
                "category": spec["category"],
                "direction": t.direction,
                "entry_date": t.entry_date.isoformat(),
                "exit_date": t.exit_date.isoformat(),
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "stop_distance": t.stop_distance,
                "exit_reason": t.exit_reason,
                "holding_days": t.holding_days,
                "gross_r": round(t.gross_r, 4),
                "drag_swap_r": round(drag_swap_r, 4),
                "net_r_fin0": round(fin0_r, 4),
                "net_r_broker_swap": round(swap_r, 4),
                "is_period_end": t.exit_reason == "PERIOD_END",
            })

        # Save trade log
        with open(OUTPUT_DIR / f"{rule_name}_trades_mt5.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(processed[0].keys()))
            writer.writeheader()
            writer.writerows(processed)

        # Evaluate across subsets
        for sample_name, sample_trades in [
            ("Full List", processed),
            ("Realized-Only", [t for t in processed if not t["is_period_end"]]),
        ]:
            for carry_name, key in [
                ("0% Carry", "net_r_fin0"),
                ("Broker Swaps", "net_r_broker_swap"),
            ]:
                if not sample_trades:
                    continue

                vals = [t[key] for t in sample_trades]
                n = len(vals)
                mean_r = float(np.mean(vals))
                sum_r = float(np.sum(vals))
                ci_lo, ci_hi = month_block_bootstrap(sample_trades, key=key)

                # Sub-periods
                t_sub1 = [t[key] for t in sample_trades if t["exit_date"] < "2022-01-01"]
                t_sub2 = [t[key] for t in sample_trades if t["exit_date"] >= "2022-01-01"]
                mean_sub1 = float(np.mean(t_sub1)) if t_sub1 else 0.0
                mean_sub2 = float(np.mean(t_sub2)) if t_sub2 else 0.0

                # Ex-metals
                t_ex_metals = [t[key] for t in sample_trades if t["category"] != "metals"]
                mean_ex_metals = float(np.mean(t_ex_metals)) if t_ex_metals else 0.0

                # Criteria evaluation
                crit1_pass = mean_r > 0 and ci_lo > 0
                crit2_pass = mean_sub1 > 0 and mean_sub2 > 0

                master_results.append({
                    "rule": rule_name,
                    "sample": sample_name,
                    "carry_model": carry_name,
                    "N": n,
                    "mean_net_r": round(mean_r, 4),
                    "sum_net_r": round(sum_r, 4),
                    "ci_95": f"[{ci_lo:+.4f}, {ci_hi:+.4f}]",
                    "ci_lower": round(ci_lo, 4),
                    "sub_2018_2021": round(mean_sub1, 4),
                    "sub_2022_2026": round(mean_sub2, 4),
                    "ex_metals": round(mean_ex_metals, 4),
                    "crit1_stat_edge": crit1_pass,
                    "crit2_temporal_pos": crit2_pass,
                })

                # Leave-out analysis for Realized-Only
                if sample_name == "Realized-Only":
                    sorted_trades = sorted(sample_trades, key=lambda t: t[key], reverse=True)
                    for drop_k in [0, 1, 3, 10]:
                        rem_trades = sorted_trades[drop_k:]
                        if not rem_trades:
                            continue
                        rem_vals = [t[key] for t in rem_trades]
                        rem_mean = float(np.mean(rem_vals))
                        rem_ci_lo, rem_ci_hi = month_block_bootstrap(rem_trades, key=key)
                        leave_out_results.append({
                            "rule": rule_name,
                            "carry_model": carry_name,
                            "dropped_k": drop_k,
                            "remaining_N": len(rem_trades),
                            "mean_net_r": round(rem_mean, 4),
                            "ci_95": f"[{rem_ci_lo:+.4f}, {rem_ci_hi:+.4f}]",
                            "ci_lower": round(rem_ci_lo, 4),
                            "ci_lower_gt_0": rem_ci_lo > 0,
                        })

    # Save summary table
    with open(OUTPUT_DIR / "battery_summary.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(master_results[0].keys()))
        writer.writeheader()
        writer.writerows(master_results)

    # Save leave out table
    with open(OUTPUT_DIR / "leave_out_sensitivity.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(leave_out_results[0].keys()))
        writer.writeheader()
        writer.writerows(leave_out_results)

    print("Phase 2 evaluation completed!")


if __name__ == "__main__":
    run_evaluation()
