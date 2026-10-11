"""Export Task 1 audit tables to CSV."""

import csv
import json
from pathlib import Path

AUDIT_DIR = Path("research/trend/audit")
summary_file = AUDIT_DIR / "task1_audit_summary.json"

with open(summary_file, encoding="utf-8") as f:
    data = json.load(f)

# 1. Beta Check T2 & T3
SYMBOLS = [
    "XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "USDJPY",
    "AUDUSD", "USDCAD", "USDCHF", "US500", "GER40", "Brent", "NDX",
]

for rule, csv_name in [("T2_SMA_CROSS", "beta_check_t2.csv"), ("T3_TSMOM", "beta_check_t3.csv")]:
    rows = []
    rule_dict = data["task_1a_beta_check"][rule]
    for sym in SYMBOLS:
        m = rule_dict[sym]
        rows.append({
            "symbol": sym,
            "total_days": m["total_days"],
            "pct_long": m["pct_long"],
            "pct_short": m["pct_short"],
            "pct_flat": m["pct_flat"],
            "bh_return_pct": m["bh_return_pct"],
            "long_trades_n": m["long_trades_n"],
            "long_price_ret_pct": m["long_price_ret_pct"],
            "long_mean_net_r_0": m["long_mean_net_r_0"],
            "long_mean_net_r_2": m["long_mean_net_r_2"],
            "low_n_flag": "LOW N" if m["long_trades_n"] < 200 else "OK",
        })
    with open(AUDIT_DIR / csv_name, "w", newline="", encoding="utf-8") as f_out:
        writer = csv.DictWriter(f_out, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

# 2. Long-Only Null Comparison
long_null_rows = []
for rule in ["T2_SMA_CROSS", "T3_TSMOM"]:
    null_info = data["task_1a_beta_check"][rule]["_LONG_ONLY_NULL"]
    # All 12 markets
    long_null_rows.append({
        "rule": rule,
        "universe": "ALL_12_MARKETS",
        "carry": "0%",
        "actual_long_n": null_info["n_long_trades"],
        "actual_long_mean_net_r": null_info["actual_long_net_r_0"],
        "null_long_mean_net_r": null_info["null_mean_0"],
        "null_ci_lower_95": null_info["null_ci_0"][0],
        "null_ci_upper_95": null_info["null_ci_0"][1],
        "null_p95": null_info["null_p95_0"],
        "p_val": null_info["p_value_0"],
        "low_n_flag": "LOW N" if null_info["n_long_trades"] < 200 else "OK",
    })
    long_null_rows.append({
        "rule": rule,
        "universe": "ALL_12_MARKETS",
        "carry": "2%",
        "actual_long_n": null_info["n_long_trades"],
        "actual_long_mean_net_r": null_info["actual_long_net_r_2"],
        "null_long_mean_net_r": null_info["null_mean_2"],
        "null_ci_lower_95": null_info["null_ci_2"][0],
        "null_ci_upper_95": null_info["null_ci_2"][1],
        "null_p95": null_info["null_p95_2"],
        "p_val": null_info["p_value_2"],
        "low_n_flag": "LOW N" if null_info["n_long_trades"] < 200 else "OK",
    })
    # Ex-metals-and-equities
    ex = null_info["ex_metals_equities"]
    long_null_rows.append({
        "rule": rule,
        "universe": "EX_METALS_EQUITIES",
        "carry": "0%",
        "actual_long_n": ex["n_long"],
        "actual_long_mean_net_r": ex["actual_long_net_r_0"],
        "null_long_mean_net_r": "N/A",
        "null_ci_lower_95": "N/A",
        "null_ci_upper_95": "N/A",
        "null_p95": "N/A",
        "p_val": "N/A",
        "low_n_flag": "LOW N" if ex["n_long"] < 200 else "OK",
    })
    long_null_rows.append({
        "rule": rule,
        "universe": "EX_METALS_EQUITIES",
        "carry": "2%",
        "actual_long_n": ex["n_long"],
        "actual_long_mean_net_r": ex["actual_long_net_r_2"],
        "null_long_mean_net_r": "N/A",
        "null_ci_lower_95": "N/A",
        "null_ci_upper_95": "N/A",
        "null_p95": "N/A",
        "p_val": "N/A",
        "low_n_flag": "LOW N" if ex["n_long"] < 200 else "OK",
    })

with open(AUDIT_DIR / "long_null_comparison.csv", "w", newline="", encoding="utf-8") as f_out:
    writer = csv.DictWriter(f_out, fieldnames=list(long_null_rows[0].keys()))
    writer.writeheader()
    writer.writerows(long_null_rows)

# 3. Win Rate Audit
win_rows = []
for rule, w in data["task_1b_win_rates"].items():
    rec0 = w["reconciliation_0"]
    rec2 = w["reconciliation_2"]
    win_rows.append({
        "rule": rule,
        "n_trades": w["n"],
        "win_rate_gross_pct": w["gross_win_rate"],
        "win_rate_net_0_pct": w["net_0_win_rate"],
        "win_rate_net_2_pct": w["net_2_win_rate"],
        "rec0_mean_net_r": rec0["mean_net_r"],
        "rec0_avg_winner_r": rec0["avg_winner_r"],
        "rec0_avg_loser_r": rec0["avg_loser_r"],
        "rec0_implied_win_rate_pct": rec0["implied_win_rate"],
        "rec2_mean_net_r": rec2["mean_net_r"],
        "rec2_avg_winner_r": rec2["avg_winner_r"],
        "rec2_avg_loser_r": rec2["avg_loser_r"],
        "rec2_implied_win_rate_pct": rec2["implied_win_rate"],
        "gap_net0_vs_net2_pct": round(w["net_0_win_rate"] - w["net_2_win_rate"], 2),
        "low_n_flag": "LOW N" if w["n"] < 200 else "OK",
    })
with open(AUDIT_DIR / "win_rate_audit.csv", "w", newline="", encoding="utf-8") as f_out:
    writer = csv.DictWriter(f_out, fieldnames=list(win_rows[0].keys()))
    writer.writeheader()
    writer.writerows(win_rows)

# 4. Weekend FX Comparison
fx_rows = []
for rule, comp in data["task_1c_weekend_comparison"].items():
    p = comp["pooled"]
    fx_rows.append({
        "rule": rule,
        "market": "POOLED_ALL",
        "old_n": p["old"]["n"],
        "old_net_r_0": p["old"]["mean_net_0"],
        "old_net_r_2": p["old"]["mean_net_2"],
        "fixed_n": p["fixed"]["n"],
        "fixed_net_r_0": p["fixed"]["mean_net_0"],
        "fixed_net_r_2": p["fixed"]["mean_net_2"],
        "delta_n": p["fixed"]["n"] - p["old"]["n"],
        "delta_net_r_0": round(p["fixed"]["mean_net_0"] - p["old"]["mean_net_0"], 4),
        "delta_net_r_2": round(p["fixed"]["mean_net_2"] - p["old"]["mean_net_2"], 4),
        "low_n_flag": "LOW N" if p["fixed"]["n"] < 200 else "OK",
    })
    for sym, m in comp["by_market"].items():
        fx_rows.append({
            "rule": rule,
            "market": sym,
            "old_n": m["old_n"],
            "old_net_r_0": m["old_net_0"],
            "old_net_r_2": m["old_net_2"],
            "fixed_n": m["fixed_n"],
            "fixed_net_r_0": m["fixed_net_0"],
            "fixed_net_r_2": m["fixed_net_2"],
            "delta_n": m["fixed_n"] - m["old_n"],
            "delta_net_r_0": round(m["fixed_net_0"] - m["old_net_0"], 4),
            "delta_net_r_2": round(m["fixed_net_2"] - m["old_net_2"], 4),
            "low_n_flag": "LOW N" if m["fixed_n"] < 200 else "OK",
        })
with open(AUDIT_DIR / "weekend_fx_comparison.csv", "w", newline="", encoding="utf-8") as f_out:
    writer = csv.DictWriter(f_out, fieldnames=list(fx_rows[0].keys()))
    writer.writeheader()
    writer.writerows(fx_rows)

# 5. Brent T1 Audit
b = data["task_1d_brent"]
brent_rows = [
    {
        "symbol": "Brent",
        "rule": "T1_DONCHIAN",
        "side": "LONG",
        "n": b["long"]["n"],
        "sum_net_r_0": b["long"]["sum_net_r_0"],
        "mean_net_r_0": b["long"]["mean_net_r_0"],
        "sum_net_r_2": b["long"]["sum_net_r_2"],
        "mean_net_r_2": b["long"]["mean_net_r_2"],
        "low_n_flag": "LOW N",
    },
    {
        "symbol": "Brent",
        "rule": "T1_DONCHIAN",
        "side": "SHORT",
        "n": b["short"]["n"],
        "sum_net_r_0": b["short"]["sum_net_r_0"],
        "mean_net_r_0": b["short"]["mean_net_r_0"],
        "sum_net_r_2": b["short"]["sum_net_r_2"],
        "mean_net_r_2": b["short"]["mean_net_r_2"],
        "low_n_flag": "LOW N",
    },
]
with open(AUDIT_DIR / "brent_t1_audit.csv", "w", newline="", encoding="utf-8") as f_out:
    writer = csv.DictWriter(f_out, fieldnames=list(brent_rows[0].keys()))
    writer.writeheader()
    writer.writerows(brent_rows)

# 6. Concentration Groups
group_rows = []
for rule in ["T2_SMA_CROSS", "T3_TSMOM"]:
    c = data["task_1e_concentration"][rule]
    for grp, gm in c["group_shares"].items():
        group_rows.append({
            "rule": rule,
            "group": grp,
            "n_trades": gm["n"],
            "sum_net_r_0": gm["sum_net_0"],
            "pct_share_net_r_0": gm["share_net_0_pct"],
            "sum_net_r_2": gm["sum_net_2"],
            "pct_share_net_r_2": gm["share_net_2_pct"],
            "low_n_flag": "LOW N" if gm["n"] < 200 else "OK",
        })
with open(AUDIT_DIR / "concentration_groups.csv", "w", newline="", encoding="utf-8") as f_out:
    writer = csv.DictWriter(f_out, fieldnames=list(group_rows[0].keys()))
    writer.writeheader()
    writer.writerows(group_rows)

# 7. Concentration Top-10 Trades
top10_rows = []
for rule in ["T2_SMA_CROSS", "T3_TSMOM"]:
    for rank, t in enumerate(data["task_1e_concentration"][rule]["top10_trades"], start=1):
        top10_rows.append({
            "rule": rule,
            "rank": rank,
            "trade_id": t["trade_id"],
            "symbol": t["symbol"],
            "direction": t["direction"],
            "entry_date": t["entry_date"],
            "exit_date": t["exit_date"],
            "holding_days": t["holding_days"],
            "net_r_0": t["net_r_0"],
            "net_r_2": t["net_r_2"],
        })
with open(AUDIT_DIR / "concentration_top10_trades.csv", "w", newline="", encoding="utf-8") as f_out:
    writer = csv.DictWriter(f_out, fieldnames=list(top10_rows[0].keys()))
    writer.writeheader()
    writer.writerows(top10_rows)

# 8. Concentration Sensitivity
sens_rows = []
for rule in ["T2_SMA_CROSS", "T3_TSMOM"]:
    c = data["task_1e_concentration"][rule]
    sens_rows.append({
        "rule": rule,
        "best_market": c["best_market"],
        "best_market_sum_net_0": c["best_market_sum_0"],
        "ex_best_market_n": c["ex_best_market"]["n"],
        "ex_best_market_mean_net_0": c["ex_best_market"]["mean_net_0"],
        "ex_best_market_mean_net_2": c["ex_best_market"]["mean_net_2"],
        "best_year": c["best_year"],
        "best_year_sum_net_0": c["best_year_sum_0"],
        "ex_best_year_n": c["ex_best_year"]["n"],
        "ex_best_year_mean_net_0": c["ex_best_year"]["mean_net_0"],
        "ex_best_year_mean_net_2": c["ex_best_year"]["mean_net_2"],
    })
with open(AUDIT_DIR / "concentration_sensitivity.csv", "w", newline="", encoding="utf-8") as f_out:
    writer = csv.DictWriter(f_out, fieldnames=list(sens_rows[0].keys()))
    writer.writeheader()
    writer.writerows(sens_rows)

print("Exported all Task 1 audit CSVs successfully.")
