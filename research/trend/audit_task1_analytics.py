"""Compute detailed analytics and outputs for Task 1 Audit."""

from __future__ import annotations

import csv
import datetime as dt
import json
import logging
from pathlib import Path

import numpy as np

from research.trend.costs import compute_costs
from research.trend.indicators import load_daily_bars
from research.trend.models import DailyBar, TrendTrade

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("audit_task1_analytics")

DATA_DIR = Path("research/trend/data")
OUTPUT_DIR = Path("research/trend/output")
AUDIT_DIR = Path("research/trend/audit")
FX_FIXED_OUTPUT_DIR = Path("research/trend/output_fxfixed")
AUDIT_DIR.mkdir(parents=True, exist_ok=True)

SYMBOLS = [
    "XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "USDJPY",
    "AUDUSD", "USDCAD", "USDCHF", "US500", "GER40", "Brent", "NDX",
]
EX_METALS_EQUITIES = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "Brent"]
GROUPS = {
    "Metals": ["XAUUSD", "XAGUSD"],
    "Equities": ["US500", "GER40", "NDX"],
    "FX": ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF"],
    "Brent": ["Brent"],
}
END_DATE = dt.date(2025, 12, 31)


def load_trades(csv_path: Path) -> list[dict]:
    with open(csv_path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


# Load baseline trades
trades_baseline = {
    "T1_DONCHIAN": load_trades(OUTPUT_DIR / "T1_DONCHIAN_trades.csv"),
    "T2_SMA_CROSS": load_trades(OUTPUT_DIR / "T2_SMA_CROSS_trades.csv"),
    "T3_TSMOM": load_trades(OUTPUT_DIR / "T3_TSMOM_trades.csv"),
}

# Load fixed FX trades
trades_fxfixed = {
    "T1_DONCHIAN": load_trades(FX_FIXED_OUTPUT_DIR / "T1_DONCHIAN_trades.csv"),
    "T2_SMA_CROSS": load_trades(FX_FIXED_OUTPUT_DIR / "T2_SMA_CROSS_trades.csv"),
    "T3_TSMOM": load_trades(FX_FIXED_OUTPUT_DIR / "T3_TSMOM_trades.csv"),
}


# ==============================================================================
# 1a. Beta Check & Long-Only Holding-Period Matched Null
# ==============================================================================
def run_1a_beta_check() -> dict:
    results_1a = {}

    for rule in ["T2_SMA_CROSS", "T3_TSMOM"]:
        rule_data = {}
        tr_list = trades_baseline[rule]

        for sym in SYMBOLS:
            bars = load_daily_bars(DATA_DIR / f"{sym}_daily.csv", hard_stop_date=END_DATE)
            start_date = bars[252].date
            eval_bars = [b for b in bars if b.date >= start_date and b.date <= END_DATE]
            total_days = len(eval_bars)

            sym_trades = [t for t in tr_list if t["symbol"] == sym]

            # Construct daily exposure array
            # Each bar is long, short, or flat
            long_days = 0
            short_days = 0

            # Map trades to date ranges
            for bar in eval_bars:
                # Find if bar is inside an active trade
                # An active trade holds from entry_date to exit_date
                # If exited on this day, it was held during the day up to exit
                in_long = any(
                    t["direction"] == "LONG" and t["entry_date"] <= bar.date.isoformat() <= t["exit_date"]
                    for t in sym_trades
                )
                in_short = any(
                    t["direction"] == "SHORT" and t["entry_date"] <= bar.date.isoformat() <= t["exit_date"]
                    for t in sym_trades
                )
                if in_long:
                    long_days += 1
                elif in_short:
                    short_days += 1

            flat_days = total_days - (long_days + short_days)
            pct_long = (long_days / total_days) * 100 if total_days > 0 else 0.0
            pct_short = (short_days / total_days) * 100 if total_days > 0 else 0.0
            pct_flat = (flat_days / total_days) * 100 if total_days > 0 else 0.0

            # Buy-and-hold price return
            bh_return = ((eval_bars[-1].close - eval_bars[0].close) / eval_bars[0].close) * 100

            # Rule's long-side price return
            # Sum of percentage price change of long trades: (exit - entry) / entry
            long_trades = [t for t in sym_trades if t["direction"] == "LONG"]
            if long_trades:
                long_price_ret = sum(
                    (float(t["exit_price"]) - float(t["entry_price"])) / float(t["entry_price"]) * 100
                    for t in long_trades
                )
                long_net_r_0 = np.mean([float(t["net_r_fin0"]) for t in long_trades])
                long_net_r_2 = np.mean([float(t["net_r"]) for t in long_trades])
            else:
                long_price_ret = 0.0
                long_net_r_0 = 0.0
                long_net_r_2 = 0.0

            rule_data[sym] = {
                "total_days": total_days,
                "pct_long": round(pct_long, 2),
                "pct_short": round(pct_short, 2),
                "pct_flat": round(pct_flat, 2),
                "bh_return_pct": round(bh_return, 2),
                "long_trades_n": len(long_trades),
                "long_price_ret_pct": round(long_price_ret, 2),
                "long_mean_net_r_0": round(float(long_net_r_0), 4),
                "long_mean_net_r_2": round(float(long_net_r_2), 4),
            }

        # Long-only holding-period-matched null (1,000 draws)
        # For each market, simulate 1,000 synthetic portfolios matching long trade holding durations and stops
        null_res = run_long_only_null(rule, tr_list)
        rule_data["_LONG_ONLY_NULL"] = null_res
        results_1a[rule] = rule_data

    return results_1a


def run_long_only_null(rule: str, trades: list[dict], n_draws: int = 1000) -> dict:
    """Run Long-only, holding-period-matched null draws."""
    np.random.seed(42)
    # Collect all long trades
    long_trades = [t for t in trades if t["direction"] == "LONG"]
    actual_long_net_r_0 = np.mean([float(t["net_r_fin0"]) for t in long_trades])
    actual_long_net_r_2 = np.mean([float(t["net_r"]) for t in long_trades])

    # Pre-load bars per symbol
    bars_by_sym = {s: load_daily_bars(DATA_DIR / f"{s}_daily.csv", hard_stop_date=END_DATE) for s in SYMBOLS}

    draw_means_0 = []
    draw_means_2 = []

    # Map long trades by symbol to preserve market composition & durations
    trades_by_sym = {}
    for t in long_trades:
        trades_by_sym.setdefault(t["symbol"], []).append(t)

    for _ in range(n_draws):
        sim_r_0 = []
        sim_r_2 = []
        for sym, sym_longs in trades_by_sym.items():
            bars = bars_by_sym[sym]
            start_idx = 252
            end_idx = len(bars) - 1

            for lt in sym_longs:
                hold_days = int(lt["holding_days"])
                # Pick random entry bar such that entry + hold_days <= end_idx
                max_entry = max(start_idx, end_idx - max(1, hold_days))
                entry_idx = np.random.randint(start_idx, max_entry + 1)
                exit_idx = min(end_idx, entry_idx + hold_days)

                entry_bar = bars[entry_idx]
                exit_bar = bars[exit_idx]
                atr = bars[entry_idx - 1].atr20 if entry_idx > 0 else entry_bar.high - entry_bar.low
                stop_dist = 3.0 * atr if "SMA" in rule or "TSMOM" in rule else 2.0 * atr
                if stop_dist <= 0:
                    stop_dist = entry_bar.close * 0.02

                # Evaluate if stopped during holding
                stop_price = entry_bar.open - stop_dist
                is_stop = False
                exit_p = exit_bar.open
                actual_exit_date = exit_bar.date

                for k in range(entry_idx, exit_idx + 1):
                    b = bars[k]
                    if b.low <= stop_price:
                        is_stop = True
                        exit_p = b.open if b.open <= stop_price else stop_price
                        actual_exit_date = b.date
                        break

                costs = compute_costs(
                    symbol=sym,
                    direction="LONG",
                    entry_price=entry_bar.open,
                    exit_price=exit_p,
                    stop_distance=stop_dist,
                    is_stop=is_stop,
                    entry_date=entry_bar.date,
                    exit_date=actual_exit_date,
                )
                sim_r_0.append(costs["net_r_fin0"])
                sim_r_2.append(costs["net_r"])

        draw_means_0.append(float(np.mean(sim_r_0)))
        draw_means_2.append(float(np.mean(sim_r_2)))

    # Compute percentiles and CI
    ci_0 = (np.percentile(draw_means_0, 2.5), np.percentile(draw_means_0, 97.5))
    ci_2 = (np.percentile(draw_means_2, 2.5), np.percentile(draw_means_2, 97.5))
    p_val_0 = np.mean([dm >= actual_long_net_r_0 for dm in draw_means_0])
    p_val_2 = np.mean([dm >= actual_long_net_r_2 for dm in draw_means_2])

    # Also compute for ex-metals-and-equities subset
    long_ex = [t for t in long_trades if t["symbol"] in EX_METALS_EQUITIES]
    actual_ex_0 = np.mean([float(t["net_r_fin0"]) for t in long_ex]) if long_ex else 0.0
    actual_ex_2 = np.mean([float(t["net_r"]) for t in long_ex]) if long_ex else 0.0

    return {
        "n_long_trades": len(long_trades),
        "actual_long_net_r_0": round(float(actual_long_net_r_0), 4),
        "actual_long_net_r_2": round(float(actual_long_net_r_2), 4),
        "null_mean_0": round(float(np.mean(draw_means_0)), 4),
        "null_ci_0": [round(float(ci_0[0]), 4), round(float(ci_0[1]), 4)],
        "null_p95_0": round(float(np.percentile(draw_means_0, 95)), 4),
        "p_value_0": round(float(p_val_0), 4),
        "null_mean_2": round(float(np.mean(draw_means_2)), 4),
        "null_ci_2": [round(float(ci_2[0]), 4), round(float(ci_2[1]), 4)],
        "null_p95_2": round(float(np.percentile(draw_means_2, 95)), 4),
        "p_value_2": round(float(p_val_2), 4),
        "ex_metals_equities": {
            "n_long": len(long_ex),
            "actual_long_net_r_0": round(float(actual_ex_0), 4),
            "actual_long_net_r_2": round(float(actual_ex_2), 4),
        },
    }


# ==============================================================================
# 1b. Win Rates & Reconciliation
# ==============================================================================
def run_1b_win_rates() -> dict:
    results_1b = {}
    for rule, tr_list in trades_baseline.items():
        n = len(tr_list)
        gross_wins = [float(t["gross_r"]) > 0 for t in tr_list]
        net0_wins = [float(t["net_r_fin0"]) > 0 for t in tr_list]
        net2_wins = [float(t["net_r"]) > 0 for t in tr_list]

        vals_0 = np.array([float(t["net_r_fin0"]) for t in tr_list])
        vals_2 = np.array([float(t["net_r"]) for t in tr_list])

        wins_0 = vals_0[vals_0 > 0]
        losses_0 = vals_0[vals_0 <= 0]
        mean_0 = np.mean(vals_0)
        avg_w0 = np.mean(wins_0)
        avg_l0 = np.mean(losses_0)
        implied_p0 = (mean_0 - avg_l0) / (avg_w0 - avg_l0)

        wins_2 = vals_2[vals_2 > 0]
        losses_2 = vals_2[vals_2 <= 0]
        mean_2 = np.mean(vals_2)
        avg_w2 = np.mean(wins_2)
        avg_l2 = np.mean(losses_2)
        implied_p2 = (mean_2 - avg_l2) / (avg_w2 - avg_l2)

        results_1b[rule] = {
            "n": n,
            "gross_win_rate": round(float(np.mean(gross_wins) * 100), 2),
            "gross_win_count": int(np.sum(gross_wins)),
            "net_0_win_rate": round(float(np.mean(net0_wins) * 100), 2),
            "net_0_win_count": int(np.sum(net0_wins)),
            "net_2_win_rate": round(float(np.mean(net2_wins) * 100), 2),
            "net_2_win_count": int(np.sum(net2_wins)),
            "reconciliation_0": {
                "mean_net_r": round(float(mean_0), 4),
                "avg_winner_r": round(float(avg_w0), 4),
                "avg_loser_r": round(float(avg_l0), 4),
                "implied_win_rate": round(float(implied_p0 * 100), 2),
            },
            "reconciliation_2": {
                "mean_net_r": round(float(mean_2), 4),
                "avg_winner_r": round(float(avg_w2), 4),
                "avg_loser_r": round(float(avg_l2), 4),
                "implied_win_rate": round(float(implied_p2 * 100), 2),
            },
        }
    return results_1b


# ==============================================================================
# 1c. Weekend-Bar Bug Comparison (Old vs Fixed)
# ==============================================================================
def run_1c_comparison() -> dict:
    comp = {}
    for rule in ["T1_DONCHIAN", "T2_SMA_CROSS", "T3_TSMOM"]:
        old_tr = trades_baseline[rule]
        fix_tr = trades_fxfixed[rule]

        rule_comp = {
            "pooled": {
                "old": {
                    "n": len(old_tr),
                    "mean_net_0": round(float(np.mean([float(t["net_r_fin0"]) for t in old_tr])), 4),
                    "mean_net_2": round(float(np.mean([float(t["net_r"]) for t in old_tr])), 4),
                },
                "fixed": {
                    "n": len(fix_tr),
                    "mean_net_0": round(float(np.mean([float(t["net_r_fin0"]) for t in fix_tr])), 4),
                    "mean_net_2": round(float(np.mean([float(t["net_r"]) for t in fix_tr])), 4),
                },
            },
            "by_market": {},
        }

        for sym in SYMBOLS:
            o_sym = [t for t in old_tr if t["symbol"] == sym]
            f_sym = [t for t in fix_tr if t["symbol"] == sym]
            rule_comp["by_market"][sym] = {
                "old_n": len(o_sym),
                "old_net_0": round(float(np.mean([float(t["net_r_fin0"]) for t in o_sym])), 4) if o_sym else 0.0,
                "old_net_2": round(float(np.mean([float(t["net_r"]) for t in o_sym])), 4) if o_sym else 0.0,
                "fixed_n": len(f_sym),
                "fixed_net_0": round(float(np.mean([float(t["net_r_fin0"]) for t in f_sym])), 4) if f_sym else 0.0,
                "fixed_net_2": round(float(np.mean([float(t["net_r"]) for t in f_sym])), 4) if f_sym else 0.0,
            }

        comp[rule] = rule_comp
    return comp


# ==============================================================================
# 1d. Brent T1 Verification
# ==============================================================================
def run_1d_brent() -> dict:
    t1_trades = trades_baseline["T1_DONCHIAN"]
    brent_trades = [t for t in t1_trades if t["symbol"] == "Brent"]
    longs = [t for t in brent_trades if t["direction"] == "LONG"]
    shorts = [t for t in brent_trades if t["direction"] == "SHORT"]

    l_net0 = [float(t["net_r_fin0"]) for t in longs]
    s_net0 = [float(t["net_r_fin0"]) for t in shorts]
    l_net2 = [float(t["net_r"]) for t in longs]
    s_net2 = [float(t["net_r"]) for t in shorts]

    return {
        "symbol": "Brent",
        "rule": "T1_DONCHIAN",
        "total_n": len(brent_trades),
        "long": {
            "n": len(longs),
            "sum_net_r_0": round(float(sum(l_net0)), 6),
            "mean_net_r_0": round(float(np.mean(l_net0)), 6),
            "mean_net_r_0_2dp": f"{np.mean(l_net0):+.2f}R",
            "sum_net_r_2": round(float(sum(l_net2)), 6),
            "mean_net_r_2": round(float(np.mean(l_net2)), 6),
            "mean_net_r_2_2dp": f"{np.mean(l_net2):+.2f}R",
        },
        "short": {
            "n": len(shorts),
            "sum_net_r_0": round(float(sum(s_net0)), 6),
            "mean_net_r_0": round(float(np.mean(s_net0)), 6),
            "mean_net_r_0_2dp": f"{np.mean(s_net0):+.2f}R",
            "sum_net_r_2": round(float(sum(s_net2)), 6),
            "mean_net_r_2": round(float(np.mean(s_net2)), 6),
            "mean_net_r_2_2dp": f"{np.mean(s_net2):+.2f}R",
        },
    }


# ==============================================================================
# 1e. Concentration Analytics (T2 & T3)
# ==============================================================================
def run_1e_concentration() -> dict:
    res = {}
    for rule in ["T2_SMA_CROSS", "T3_TSMOM"]:
        tr_list = trades_baseline[rule]
        total_net_0 = sum(float(t["net_r_fin0"]) for t in tr_list)
        total_net_2 = sum(float(t["net_r"]) for t in tr_list)

        # 1. Market Groups
        group_shares = {}
        for g_name, g_syms in GROUPS.items():
            g_trades = [t for t in tr_list if t["symbol"] in g_syms]
            g_sum_0 = sum(float(t["net_r_fin0"]) for t in g_trades)
            g_sum_2 = sum(float(t["net_r"]) for t in g_trades)
            group_shares[g_name] = {
                "n": len(g_trades),
                "sum_net_0": round(float(g_sum_0), 2),
                "share_net_0_pct": round(float(g_sum_0 / total_net_0 * 100), 2) if total_net_0 != 0 else 0.0,
                "sum_net_2": round(float(g_sum_2), 2),
                "share_net_2_pct": round(float(g_sum_2 / total_net_2 * 100), 2) if total_net_2 != 0 else 0.0,
            }

        # 2. Yearly Shares
        yearly = {}
        for yr in range(2003, 2026):
            yr_str = str(yr)
            yr_trades = [t for t in tr_list if t["exit_date"][:4] == yr_str]
            yr_sum_0 = sum(float(t["net_r_fin0"]) for t in yr_trades)
            yr_sum_2 = sum(float(t["net_r"]) for t in yr_trades)
            yearly[yr_str] = {
                "n": len(yr_trades),
                "sum_net_0": round(float(yr_sum_0), 2),
                "share_0_pct": round(float(yr_sum_0 / total_net_0 * 100), 2) if total_net_0 != 0 else 0.0,
                "sum_net_2": round(float(yr_sum_2), 2),
                "share_2_pct": round(float(yr_sum_2 / total_net_2 * 100), 2) if total_net_2 != 0 else 0.0,
            }

        # Best year
        best_yr = max(yearly.keys(), key=lambda y: yearly[y]["sum_net_0"])

        # Best market
        mkt_sums_0 = {s: sum(float(t["net_r_fin0"]) for t in tr_list if t["symbol"] == s) for s in SYMBOLS}
        best_mkt = max(mkt_sums_0.keys(), key=lambda s: mkt_sums_0[s])

        # Ex-best-market
        ex_best_mkt_tr = [t for t in tr_list if t["symbol"] != best_mkt]
        ex_mkt_mean_0 = np.mean([float(t["net_r_fin0"]) for t in ex_best_mkt_tr])
        ex_mkt_mean_2 = np.mean([float(t["net_r"]) for t in ex_best_mkt_tr])

        # Ex-best-year
        ex_best_yr_tr = [t for t in tr_list if t["exit_date"][:4] != best_yr]
        ex_yr_mean_0 = np.mean([float(t["net_r_fin0"]) for t in ex_best_yr_tr])
        ex_yr_mean_2 = np.mean([float(t["net_r"]) for t in ex_best_yr_tr])

        # Top 10 trades
        sorted_by_r0 = sorted(tr_list, key=lambda t: float(t["net_r_fin0"]), reverse=True)
        top10 = []
        for t in sorted_by_r0[:10]:
            top10.append({
                "trade_id": t["trade_id"],
                "symbol": t["symbol"],
                "direction": t["direction"],
                "entry_date": t["entry_date"],
                "exit_date": t["exit_date"],
                "holding_days": int(t["holding_days"]),
                "net_r_0": round(float(t["net_r_fin0"]), 4),
                "net_r_2": round(float(t["net_r"]), 4),
            })

        res[rule] = {
            "total_net_0": round(float(total_net_0), 2),
            "total_net_2": round(float(total_net_2), 2),
            "group_shares": group_shares,
            "yearly_shares": yearly,
            "best_market": best_mkt,
            "best_market_sum_0": round(float(mkt_sums_0[best_mkt]), 2),
            "ex_best_market": {
                "excluded": best_mkt,
                "n": len(ex_best_mkt_tr),
                "mean_net_0": round(float(ex_mkt_mean_0), 4),
                "mean_net_2": round(float(ex_mkt_mean_2), 4),
            },
            "best_year": best_yr,
            "best_year_sum_0": round(float(yearly[best_yr]["sum_net_0"]), 2),
            "ex_best_year": {
                "excluded": best_yr,
                "n": len(ex_best_yr_tr),
                "mean_net_0": round(float(ex_yr_mean_0), 4),
                "mean_net_2": round(float(ex_yr_mean_2), 4),
            },
            "top10_trades": top10,
        }

    return res


if __name__ == "__main__":
    logger.info("Computing Task 1 Analytics...")
    audit_data = {
        "timestamp_utc": dt.datetime.now(dt.UTC).isoformat(),
        "task_1a_beta_check": run_1a_beta_check(),
        "task_1b_win_rates": run_1b_win_rates(),
        "task_1c_weekend_comparison": run_1c_comparison(),
        "task_1d_brent": run_1d_brent(),
        "task_1e_concentration": run_1e_concentration(),
    }

    out_file = AUDIT_DIR / "task1_audit_summary.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(audit_data, f, indent=2)

    logger.info("Saved Task 1 audit summary to %s", out_file)
