"""Statistical analysis and block bootstrap resampling for trend research."""

from __future__ import annotations

import csv
import datetime as dt
import json
import logging
from pathlib import Path
from typing import Any

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("trend.stats")

OUTPUT_DIR = Path("research/trend/output")
RULES = ["T1_DONCHIAN", "T2_SMA_CROSS", "T3_TSMOM"]
BOOTSTRAP_ROUNDS = 10_000
BOOTSTRAP_SEED = 42


def load_trades_csv(csv_path: Path) -> list[dict[str, Any]]:
    trades = []
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            parsed = dict(row)
            parsed["entry_date"] = dt.date.fromisoformat(row["entry_date"])
            parsed["exit_date"] = dt.date.fromisoformat(row["exit_date"])
            parsed["entry_price"] = float(row["entry_price"])
            parsed["stop_loss"] = float(row["stop_loss"])
            parsed["stop_distance"] = float(row["stop_distance"])
            parsed["exit_price"] = float(row["exit_price"])
            parsed["holding_days"] = int(row["holding_days"])
            parsed["gross_pnl_pts"] = float(row["gross_pnl_pts"])
            parsed["gross_r"] = float(row["gross_r"])
            parsed["spread_cost_pts"] = float(row["spread_cost_pts"])
            parsed["slippage_cost_pts"] = float(row["slippage_cost_pts"])
            parsed["financing_cost_pts"] = float(row["financing_cost_pts"])
            parsed["drag_spread_r"] = float(row["drag_spread_r"])
            parsed["drag_slippage_r"] = float(row["drag_slippage_r"])
            parsed["drag_financing_r"] = float(row["drag_financing_r"])
            parsed["net_r"] = float(row["net_r"])
            parsed["net_r_fin0"] = float(row["net_r_fin0"])
            parsed["net_r_fin4"] = float(row["net_r_fin4"])
            parsed["net_r_spread2x"] = float(row["net_r_spread2x"])
            parsed["is_stop"] = row["is_stop"].lower() == "true"
            trades.append(parsed)
    return trades


def compute_basic_metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {
            "n": 0,
            "underpowered": True,
            "win_rate": 0.0,
            "mean_gross_r": 0.0,
            "mean_net_r": 0.0,
            "median_net_r": 0.0,
            "profit_factor": 0.0,
            "total_net_r": 0.0,
            "total_gross_r": 0.0,
        }

    net_rs = [t["net_r"] for t in trades]
    gross_rs = [t["gross_r"] for t in trades]
    wins = [r for r in net_rs if r > 0]
    losses = [r for r in net_rs if r < 0]

    win_rate = len(wins) / n
    mean_net_r = float(np.mean(net_rs))
    median_net_r = float(np.median(net_rs))
    mean_gross_r = float(np.mean(gross_rs))

    sum_wins = sum(wins)
    sum_losses = abs(sum(losses))
    profit_factor = (sum_wins / sum_losses) if sum_losses > 0 else (99.0 if sum_wins > 0 else 0.0)

    return {
        "n": n,
        "underpowered": n < 200,
        "win_rate": round(win_rate, 4),
        "mean_gross_r": round(mean_gross_r, 4),
        "mean_net_r": round(mean_net_r, 4),
        "median_net_r": round(median_net_r, 4),
        "profit_factor": round(profit_factor, 4),
        "total_net_r": round(float(sum(net_rs)), 4),
        "total_gross_r": round(float(sum(gross_rs)), 4),
    }


def compute_metrics_fin0(trades: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {
            "n": 0,
            "underpowered": True,
            "win_rate": 0.0,
            "mean_net_r": 0.0,
            "median_net_r": 0.0,
            "profit_factor": 0.0,
            "total_net_r": 0.0,
        }

    net_rs = [t["net_r_fin0"] for t in trades]
    wins = [r for r in net_rs if r > 0]
    losses = [r for r in net_rs if r < 0]

    win_rate = len(wins) / n
    mean_net_r = float(np.mean(net_rs))
    median_net_r = float(np.median(net_rs))

    sum_wins = sum(wins)
    sum_losses = abs(sum(losses))
    profit_factor = (sum_wins / sum_losses) if sum_losses > 0 else (99.0 if sum_wins > 0 else 0.0)

    return {
        "n": n,
        "underpowered": n < 200,
        "win_rate": round(win_rate, 4),
        "mean_net_r": round(mean_net_r, 4),
        "median_net_r": round(median_net_r, 4),
        "profit_factor": round(profit_factor, 4),
        "total_net_r": round(float(sum(net_rs)), 4),
    }


def compute_drag_metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {
            "n": 0,
            "avg_holding_days": 0.0,
            "avg_spread_drag_r": 0.0,
            "avg_slippage_drag_r": 0.0,
            "avg_financing_drag_r": 0.0,
            "avg_total_drag_r": 0.0,
        }

    holding_days = [t["holding_days"] for t in trades]
    spread_drags = [t["drag_spread_r"] for t in trades]
    slip_drags = [t["drag_slippage_r"] for t in trades]
    fin_drags = [t["drag_financing_r"] for t in trades]
    tot_drags = [s + sl + f for s, sl, f in zip(spread_drags, slip_drags, fin_drags)]

    return {
        "n": n,
        "avg_holding_days": round(float(np.mean(holding_days)), 2),
        "avg_spread_drag_r": round(float(np.mean(spread_drags)), 4),
        "avg_slippage_drag_r": round(float(np.mean(slip_drags)), 4),
        "avg_financing_drag_r": round(float(np.mean(fin_drags)), 4),
        "avg_total_drag_r": round(float(np.mean(tot_drags)), 4),
    }


def block_bootstrap_calendar_months(
    trades: list[dict[str, Any]],
    metric_key: str = "net_r",
    n_rounds: int = BOOTSTRAP_ROUNDS,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """Bootstrap resampling calendar months (pooling all markets' trades that close in that month)."""
    if not trades:
        return {"p_value_one_sided": 1.0, "ci_95_low": 0.0, "ci_95_high": 0.0}

    # Group trades by exit (year, month)
    months_dict: dict[tuple[int, int], list[float]] = {}
    for t in trades:
        m_key = (t["exit_date"].year, t["exit_date"].month)
        if m_key not in months_dict:
            months_dict[m_key] = []
        months_dict[m_key].append(t[metric_key])

    month_keys = list(months_dict.keys())
    k_months = len(month_keys)

    rng = np.random.default_rng(seed)
    boot_means = np.empty(n_rounds, dtype=np.float64)

    for b in range(n_rounds):
        sampled_indices = rng.integers(0, k_months, size=k_months)
        sampled_trades: list[float] = []
        for idx in sampled_indices:
            sampled_trades.extend(months_dict[month_keys[idx]])
        boot_means[b] = np.mean(sampled_trades)

    p_value = float(np.mean(boot_means <= 0.0))
    ci_low = float(np.percentile(boot_means, 2.5))
    ci_high = float(np.percentile(boot_means, 97.5))

    return {
        "p_value_one_sided": round(p_value, 5),
        "ci_95_low": round(ci_low, 4),
        "ci_95_high": round(ci_high, 4),
    }


def holm_bonferroni(p_values: dict[str, float], alpha: float = 0.05) -> dict[str, Any]:
    """Holm-Bonferroni correction across the 3 rules."""
    sorted_rules = sorted(p_values.keys(), key=lambda r: p_values[r])
    m = len(sorted_rules)
    adj_p: dict[str, float] = {}
    passes: dict[str, bool] = {}

    cum_max = 0.0
    for i, r in enumerate(sorted_rules):
        raw_p = p_values[r]
        factor = m - i
        adj = min(1.0, raw_p * factor)
        cum_max = max(cum_max, adj)
        adj_p[r] = round(cum_max, 5)
        passes[r] = cum_max < alpha

    return {"adjusted_p_values": adj_p, "passes_holm_5pct": passes}


def run_statistics() -> dict[str, Any]:
    all_trades: dict[str, list[dict[str, Any]]] = {}
    for rule in RULES:
        csv_p = OUTPUT_DIR / f"{rule}_trades.csv"
        all_trades[rule] = load_trades_csv(csv_p)

    stats_summary: dict[str, Any] = {}
    raw_p_values_fin2: dict[str, float] = {}
    raw_p_values_fin0: dict[str, float] = {}

    for rule, trades in all_trades.items():
        base_metrics = compute_basic_metrics(trades)
        base_metrics_fin0 = compute_metrics_fin0(trades)
        drag_pooled = compute_drag_metrics(trades)

        # Bootstrap at primary 2% financing and at 0% financing
        boot_fin2 = block_bootstrap_calendar_months(trades, metric_key="net_r", seed=BOOTSTRAP_SEED)
        boot_fin0 = block_bootstrap_calendar_months(trades, metric_key="net_r_fin0", seed=BOOTSTRAP_SEED)

        raw_p_values_fin2[rule] = boot_fin2["p_value_one_sided"]
        raw_p_values_fin0[rule] = boot_fin0["p_value_one_sided"]

        # Breakdowns
        # 1. By Market (Performance & Component Drag)
        by_market = {}
        by_market_fin0 = {}
        drag_by_market = {}
        symbols = sorted(list(set(t["symbol"] for t in trades)))
        for s in symbols:
            s_trades = [t for t in trades if t["symbol"] == s]
            by_market[s] = compute_basic_metrics(s_trades)
            by_market_fin0[s] = compute_metrics_fin0(s_trades)
            drag_by_market[s] = compute_drag_metrics(s_trades)

        # 2. By Side (Long vs Short)
        by_side = {
            "LONG": compute_basic_metrics([t for t in trades if t["direction"] == "LONG"]),
            "SHORT": compute_basic_metrics([t for t in trades if t["direction"] == "SHORT"]),
        }
        by_side_fin0 = {
            "LONG": compute_metrics_fin0([t for t in trades if t["direction"] == "LONG"]),
            "SHORT": compute_metrics_fin0([t for t in trades if t["direction"] == "SHORT"]),
        }

        # 3. By Sub-period (earliest-2009 report-only, 2010-2017 Gate 5, 2018-2025 Gate 5)
        sub_earliest_2009 = [t for t in trades if t["entry_date"] < dt.date(2010, 1, 1)]
        sub_2010_2017 = [
            t for t in trades if dt.date(2010, 1, 1) <= t["entry_date"] < dt.date(2018, 1, 1)
        ]
        sub_2018_2025 = [t for t in trades if t["entry_date"] >= dt.date(2018, 1, 1)]
        by_period = {
            "earliest_2009": compute_basic_metrics(sub_earliest_2009),
            "2010_2017": compute_basic_metrics(sub_2010_2017),
            "2018_2025": compute_basic_metrics(sub_2018_2025),
        }
        by_period_fin0 = {
            "earliest_2009": compute_metrics_fin0(sub_earliest_2009),
            "2010_2017": compute_metrics_fin0(sub_2010_2017),
            "2018_2025": compute_metrics_fin0(sub_2018_2025),
        }

        # 4. Ex-Metals (ex-XAUUSD and ex-XAGUSD)
        ex_metals = [t for t in trades if t["symbol"] not in ("XAUUSD", "XAGUSD")]
        by_ex_metals = compute_basic_metrics(ex_metals)
        by_ex_metals_fin0 = compute_metrics_fin0(ex_metals)

        # 5. Cost Sensitivities
        sensitivities = {
            "fin_0pct": round(float(np.mean([t["net_r_fin0"] for t in trades])), 4),
            "fin_4pct": round(float(np.mean([t["net_r_fin4"] for t in trades])), 4),
            "spread_2x": round(float(np.mean([t["net_r_spread2x"] for t in trades])), 4),
        }

        stats_summary[rule] = {
            "pooled": base_metrics,
            "pooled_fin0": base_metrics_fin0,
            "bootstrap": boot_fin2,
            "bootstrap_fin0": boot_fin0,
            "drag_breakdown_pooled": drag_pooled,
            "drag_breakdown_by_market": drag_by_market,
            "by_market": by_market,
            "by_market_fin0": by_market_fin0,
            "by_side": by_side,
            "by_side_fin0": by_side_fin0,
            "by_sub_period": by_period,
            "by_sub_period_fin0": by_period_fin0,
            "ex_metals": by_ex_metals,
            "ex_metals_fin0": by_ex_metals_fin0,
            "cost_sensitivities": sensitivities,
        }

    # Holm correction within each financing level across the 3 rules
    holm_res_fin2 = holm_bonferroni(raw_p_values_fin2)
    holm_res_fin0 = holm_bonferroni(raw_p_values_fin0)

    for rule in RULES:
        stats_summary[rule]["bootstrap"]["holm_adj_p_value"] = holm_res_fin2["adjusted_p_values"][rule]
        stats_summary[rule]["bootstrap"]["passes_holm_5pct"] = holm_res_fin2["passes_holm_5pct"][rule]
        stats_summary[rule]["bootstrap_fin0"]["holm_adj_p_value"] = holm_res_fin0["adjusted_p_values"][rule]
        stats_summary[rule]["bootstrap_fin0"]["passes_holm_5pct"] = holm_res_fin0["passes_holm_5pct"][rule]

    # Save to JSON
    out_json = OUTPUT_DIR / "statistics_summary.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(stats_summary, f, indent=2)
    logger.info("Saved statistical analysis to %s", out_json)

    return stats_summary


if __name__ == "__main__":
    run_statistics()
