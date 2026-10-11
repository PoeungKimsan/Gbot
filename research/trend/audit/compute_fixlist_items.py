"""Compute all numbers for the Fix List audit items.

Covers:
1. Power calc: N = (1.96*sd/mean)^2 and cluster-adjusted using month-block bootstrap CI half-widths.
2. Top-10: sum concentration_top10_trades.csv and reconcile with 89.6% / 96.8%.
3. Reconcile concentration_groups.csv with by-market table (T2 metals vs gold+silver, T3 FX+Brent).
4. Verbatim prereg definitions of Null A and Null B.
5. Definition of long price return; compute rule equity curve vs buy-and-hold (return, max DD, vol) per market.
6. Check which T3 list the forward regression test compares against.
7. Simulated P(DD > 6R in 32 trades) and P(net > 0) under zero edge and historical edge (100k iterations).
8. Verify --asof-today --dry logic for forward.report.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
import numpy as np

OUTPUT_DIR = Path("research/trend/output")
AUDIT_DIR = Path("research/trend/audit")
DATA_DIR = Path("research/trend/data")

# ------------------------------------------------------------------------------
# 1. Power Calc
# ------------------------------------------------------------------------------
def compute_power_calc():
    with open(OUTPUT_DIR / "statistics_summary.json", encoding="utf-8") as f:
        stats = json.load(f)

    res = {}
    for rule in ["T2_SMA_CROSS", "T3_TSMOM"]:
        with open(OUTPUT_DIR / f"{rule}_trades.csv", encoding="utf-8") as f:
            trades = list(csv.DictReader(f))
        
        n_obs = len(trades)
        # 0% carry
        vals_0 = np.array([float(t["net_r_fin0"]) for t in trades])
        mu_0 = np.mean(vals_0)
        sd_0_sample = np.std(vals_0, ddof=1)
        sd_0_pop = np.std(vals_0, ddof=0)
        n_iid_0_sample = (1.96 * sd_0_sample / mu_0) ** 2
        n_iid_0_pop = (1.96 * sd_0_pop / mu_0) ** 2
        
        # 2% carry
        vals_2 = np.array([float(t["net_r"]) for t in trades])
        mu_2 = np.mean(vals_2)
        sd_2_sample = np.std(vals_2, ddof=1)
        sd_2_pop = np.std(vals_2, ddof=0)
        n_iid_2_sample = (1.96 * sd_2_sample / mu_2) ** 2
        n_iid_2_pop = (1.96 * sd_2_pop / mu_2) ** 2

        # Bootstrap half-widths from statistics_summary.json
        boot_0 = stats[rule]["bootstrap_fin0"]
        boot_2 = stats[rule]["bootstrap"]
        hw_boot_0 = (boot_0["ci_95_high"] - boot_0["ci_95_low"]) / 2.0
        hw_boot_2 = (boot_2["ci_95_high"] - boot_2["ci_95_low"]) / 2.0
        
        hw_iid_0 = 1.96 * sd_0_sample / np.sqrt(n_obs)
        hw_iid_2 = 1.96 * sd_2_sample / np.sqrt(n_obs)
        
        vif_0 = (hw_boot_0 / hw_iid_0) ** 2
        vif_2 = (hw_boot_2 / hw_iid_2) ** 2
        
        n_cluster_0 = n_obs * ((hw_boot_0 / mu_0) ** 2)
        n_cluster_2 = n_obs * ((hw_boot_2 / mu_2) ** 2)
        
        # Pace: trades per year
        years_span = 23.167
        pace = n_obs / years_span
        
        res[rule] = {
            "n_obs": n_obs,
            "pace": pace,
            "fin0": {
                "mu": mu_0,
                "sd_sample": sd_0_sample,
                "sd_pop": sd_0_pop,
                "n_iid_sample": n_iid_0_sample,
                "n_iid_pop": n_iid_0_pop,
                "years_iid": n_iid_0_sample / pace,
                "hw_boot": hw_boot_0,
                "hw_iid": hw_iid_0,
                "vif": vif_0,
                "n_cluster": n_cluster_0,
                "years_cluster": n_cluster_0 / pace,
            },
            "fin2": {
                "mu": mu_2,
                "sd_sample": sd_2_sample,
                "sd_pop": sd_2_pop,
                "n_iid_sample": n_iid_2_sample,
                "n_iid_pop": n_iid_2_pop,
                "years_iid": n_iid_2_sample / pace,
                "hw_boot": hw_boot_2,
                "hw_iid": hw_iid_2,
                "vif": vif_2,
                "n_cluster": n_cluster_2,
                "years_cluster": n_cluster_2 / pace,
            }
        }
    return res

# ------------------------------------------------------------------------------
# 2. Top-10 Trade Sum
# ------------------------------------------------------------------------------
def compute_top10_sum():
    with open(AUDIT_DIR / "concentration_top10_trades.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    
    t2_rows = [r for r in rows if r["rule"] == "T2_SMA_CROSS"]
    t3_rows = [r for r in rows if r["rule"] == "T3_TSMOM"]
    
    t2_sum_0 = sum(float(r["net_r_0"]) for r in t2_rows)
    t2_sum_2 = sum(float(r["net_r_2"]) for r in t2_rows)
    t3_sum_0 = sum(float(r["net_r_0"]) for r in t3_rows)
    t3_sum_2 = sum(float(r["net_r_2"]) for r in t3_rows)
    
    # Load total net R from trades CSV
    with open(OUTPUT_DIR / "T2_SMA_CROSS_trades.csv", encoding="utf-8") as f:
        t2_all = list(csv.DictReader(f))
    with open(OUTPUT_DIR / "T3_TSMOM_trades.csv", encoding="utf-8") as f:
        t3_all = list(csv.DictReader(f))
        
    t2_tot_0 = sum(float(r["net_r_fin0"]) for r in t2_all)
    t2_tot_2 = sum(float(r["net_r"]) for r in t2_all)
    t3_tot_0 = sum(float(r["net_r_fin0"]) for r in t3_all)
    t3_tot_2 = sum(float(r["net_r"]) for r in t3_all)
    
    return {
        "T2": {
            "top10_sum_0": t2_sum_0,
            "top10_sum_2": t2_sum_2,
            "total_0": t2_tot_0,
            "total_2": t2_tot_2,
            "share_0_pct": (t2_sum_0 / t2_tot_0) * 100,
            "share_2_pct": (t2_sum_2 / t2_tot_2) * 100,
        },
        "T3": {
            "top10_sum_0": t3_sum_0,
            "top10_sum_2": t3_sum_2,
            "total_0": t3_tot_0,
            "total_2": t3_tot_2,
            "share_0_pct": (t3_sum_0 / t3_tot_0) * 100,
            "share_2_pct": (t3_sum_2 / t3_tot_2) * 100,
        }
    }

# ------------------------------------------------------------------------------
# 3. Market Groups Reconciliation
# ------------------------------------------------------------------------------
def compute_group_reconciliation():
    mapping = {
        "Metals": ["XAUUSD", "XAGUSD"],
        "Equities": ["US500", "GER40", "NDX"],
        "FX": ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF"],
        "Brent": ["Brent"],
    }
    
    res = {}
    for rule in ["T2_SMA_CROSS", "T3_TSMOM"]:
        with open(OUTPUT_DIR / f"{rule}_trades.csv", encoding="utf-8") as f:
            trades = list(csv.DictReader(f))
        
        sym_stats = {}
        for sym in ["XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "US500", "GER40", "Brent", "NDX"]:
            sub = [t for t in trades if t["symbol"] == sym]
            s0 = sum(float(t["net_r_fin0"]) for t in sub)
            s2 = sum(float(t["net_r"]) for t in sub)
            sym_stats[sym] = {"n": len(sub), "sum_0": s0, "sum_2": s2, "mean_0": s0 / len(sub) if sub else 0.0}
            
        grp_stats = {}
        for grp, syms in mapping.items():
            sub = [t for t in trades if t["symbol"] in syms]
            s0 = sum(float(t["net_r_fin0"]) for t in sub)
            s2 = sum(float(t["net_r"]) for t in sub)
            grp_stats[grp] = {"n": len(sub), "sum_0": s0, "sum_2": s2, "mean_0": s0 / len(sub) if sub else 0.0}
            
        # FX + Brent combined
        fx_brent_sub = [t for t in trades if t["symbol"] in mapping["FX"] + mapping["Brent"]]
        fx_brent_s0 = sum(float(t["net_r_fin0"]) for t in fx_brent_sub)
        fx_brent_s2 = sum(float(t["net_r"]) for t in fx_brent_sub)
        fx_brent_stats = {
            "n": len(fx_brent_sub),
            "sum_0": fx_brent_s0,
            "sum_2": fx_brent_s2,
            "mean_0": fx_brent_s0 / len(fx_brent_sub) if fx_brent_sub else 0.0,
            "mean_2": fx_brent_s2 / len(fx_brent_sub) if fx_brent_sub else 0.0,
        }
        
        res[rule] = {
            "by_market": sym_stats,
            "by_group": grp_stats,
            "fx_plus_brent": fx_brent_stats,
        }
    return res

# ------------------------------------------------------------------------------
# 5. Rule Equity Curve vs Buy-and-Hold
# ------------------------------------------------------------------------------
def compute_equity_curve_vs_bh():
    from research.trend.indicators import load_daily_bars
    import datetime as dt
    
    END_DATE = dt.date(2025, 12, 31)
    SYMBOLS = ["XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "US500", "GER40", "Brent", "NDX"]
    
    rows = []
    for sym in SYMBOLS:
        bars = load_daily_bars(DATA_DIR / f"{sym}_daily.csv", hard_stop_date=END_DATE)
        start_date = bars[252].date
        eval_bars = [b for b in bars if b.date >= start_date and b.date <= END_DATE]
        
        # B&H metrics
        closes = np.array([b.close for b in eval_bars])
        daily_ret = np.diff(closes) / closes[:-1]
        bh_total_ret = ((closes[-1] - closes[0]) / closes[0]) * 100
        bh_ann_vol = np.std(daily_ret, ddof=1) * np.sqrt(252) * 100
        
        # B&H drawdown
        peak = np.maximum.accumulate(closes)
        dd = (closes - peak) / peak
        bh_max_dd = np.min(dd) * 100  # percentage
        
        # Strategy metrics for T2 and T3
        for rule in ["T2_SMA_CROSS", "T3_TSMOM"]:
            with open(OUTPUT_DIR / f"{rule}_trades.csv", encoding="utf-8") as f:
                trades = [t for t in csv.DictReader(f) if t["symbol"] == sym]
            
            n_tr = len(trades)
            net_0_vals = [float(t["net_r_fin0"]) for t in trades]
            net_2_vals = [float(t["net_r"]) for t in trades]
            cum_r_0 = sum(net_0_vals)
            cum_r_2 = sum(net_2_vals)
            
            # Cumulative curve in R
            cum_curve_0 = np.cumsum([0.0] + net_0_vals)
            cum_curve_2 = np.cumsum([0.0] + net_2_vals)
            
            peak_0 = np.maximum.accumulate(cum_curve_0)
            dd_0 = cum_curve_0 - peak_0
            max_dd_r_0 = np.min(dd_0)
            
            peak_2 = np.maximum.accumulate(cum_curve_2)
            dd_2 = cum_curve_2 - peak_2
            max_dd_r_2 = np.min(dd_2)
            
            r_vol_0 = np.std(net_0_vals, ddof=1) if n_tr > 1 else 0.0
            r_vol_2 = np.std(net_2_vals, ddof=1) if n_tr > 1 else 0.0
            
            # Long price return definition
            long_trades = [t for t in trades if t["direction"] == "LONG"]
            long_price_ret_sum = sum(
                (float(t["exit_price"]) - float(t["entry_price"])) / float(t["entry_price"]) * 100
                for t in long_trades
            )
            
            rows.append({
                "symbol": sym,
                "rule": rule,
                "n_trades": n_tr,
                "bh_total_return_pct": round(float(bh_total_ret), 2),
                "bh_max_dd_pct": round(float(bh_max_dd), 2),
                "bh_ann_vol_pct": round(float(bh_ann_vol), 2),
                "long_price_ret_sum_pct": round(float(long_price_ret_sum), 2),
                "rule_cum_net_r_0": round(float(cum_r_0), 2),
                "rule_max_dd_r_0": round(float(max_dd_r_0), 2),
                "rule_trade_r_sd_0": round(float(r_vol_0), 2),
                "rule_cum_net_r_2": round(float(cum_r_2), 2),
                "rule_max_dd_r_2": round(float(max_dd_r_2), 2),
                "rule_trade_r_sd_2": round(float(r_vol_2), 2),
            })
    return rows

# ------------------------------------------------------------------------------
# 7. Simulated Probabilities in 32 Trades
# ------------------------------------------------------------------------------
def compute_32_trade_simulations():
    np.random.seed(42)
    n_sims = 100000
    n_trades = 32
    
    res = {}
    for rule in ["T2_SMA_CROSS", "T3_TSMOM"]:
        with open(OUTPUT_DIR / f"{rule}_trades.csv", encoding="utf-8") as f:
            trades = list(csv.DictReader(f))
            
        vals_0 = np.array([float(t["net_r_fin0"]) for t in trades])
        vals_2 = np.array([float(t["net_r"]) for t in trades])
        
        # 1. Under Zero Edge: demeaned empirical distribution (mean=0)
        zero_0 = vals_0 - np.mean(vals_0)
        zero_2 = vals_2 - np.mean(vals_2)
        
        # Simulations under Zero Edge
        draws_z0 = np.random.choice(zero_0, size=(n_sims, n_trades), replace=True)
        draws_z2 = np.random.choice(zero_2, size=(n_sims, n_trades), replace=True)
        
        # Cumulative curves & DD
        cum_z0 = np.cumsum(draws_z0, axis=1)
        peaks_z0 = np.maximum.accumulate(np.column_stack([np.zeros(n_sims), cum_z0]), axis=1)
        dds_z0 = np.column_stack([np.zeros(n_sims), cum_z0]) - peaks_z0
        mdd_z0 = np.abs(np.min(dds_z0, axis=1))
        
        p_dd_gt_6_z0 = np.mean(mdd_z0 > 6.0)
        p_net_gt_0_z0 = np.mean(cum_z0[:, -1] > 0.0)

        cum_z2 = np.cumsum(draws_z2, axis=1)
        peaks_z2 = np.maximum.accumulate(np.column_stack([np.zeros(n_sims), cum_z2]), axis=1)
        dds_z2 = np.column_stack([np.zeros(n_sims), cum_z2]) - peaks_z2
        mdd_z2 = np.abs(np.min(dds_z2, axis=1))
        
        p_dd_gt_6_z2 = np.mean(mdd_z2 > 6.0)
        p_net_gt_0_z2 = np.mean(cum_z2[:, -1] > 0.0)

        # 2. Under Historical Edge (empirical distribution)
        draws_h0 = np.random.choice(vals_0, size=(n_sims, n_trades), replace=True)
        draws_h2 = np.random.choice(vals_2, size=(n_sims, n_trades), replace=True)
        
        cum_h0 = np.cumsum(draws_h0, axis=1)
        peaks_h0 = np.maximum.accumulate(np.column_stack([np.zeros(n_sims), cum_h0]), axis=1)
        dds_h0 = np.column_stack([np.zeros(n_sims), cum_h0]) - peaks_h0
        mdd_h0 = np.abs(np.min(dds_h0, axis=1))
        
        p_dd_gt_6_h0 = np.mean(mdd_h0 > 6.0)
        p_net_gt_0_h0 = np.mean(cum_h0[:, -1] > 0.0)

        cum_h2 = np.cumsum(draws_h2, axis=1)
        peaks_h2 = np.maximum.accumulate(np.column_stack([np.zeros(n_sims), cum_h2]), axis=1)
        dds_h2 = np.column_stack([np.zeros(n_sims), cum_h2]) - peaks_h2
        mdd_h2 = np.abs(np.min(dds_h2, axis=1))
        
        p_dd_gt_6_h2 = np.mean(mdd_h2 > 6.0)
        p_net_gt_0_h2 = np.mean(cum_h2[:, -1] > 0.0)
        
        res[rule] = {
            "zero_edge_0": {"p_dd_gt_6": float(p_dd_gt_6_z0), "p_net_gt_0": float(p_net_gt_0_z0)},
            "zero_edge_2": {"p_dd_gt_6": float(p_dd_gt_6_z2), "p_net_gt_0": float(p_net_gt_0_z2)},
            "historical_edge_0": {"p_dd_gt_6": float(p_dd_gt_6_h0), "p_net_gt_0": float(p_net_gt_0_h0)},
            "historical_edge_2": {"p_dd_gt_6": float(p_dd_gt_6_h2), "p_net_gt_0": float(p_net_gt_0_h2)},
        }
    return res

if __name__ == "__main__":
    print("Running calculations...")
    p_calc = compute_power_calc()
    print("Power Calc:", json.dumps(p_calc, indent=2))
    
    t10 = compute_top10_sum()
    print("Top 10 Sum:", json.dumps(t10, indent=2))
    
    grp = compute_group_reconciliation()
    print("Group Recon T2 Metals Sum 0%:", grp["T2_SMA_CROSS"]["by_group"]["Metals"]["sum_0"])
    print("Group Recon T3 FX+Brent Sum 0%:", grp["T3_TSMOM"]["fx_plus_brent"]["sum_0"], "Mean 0%:", grp["T3_TSMOM"]["fx_plus_brent"]["mean_0"])
    
    sims = compute_32_trade_simulations()
    print("32-Trade Sims:", json.dumps(sims, indent=2))
    
    eq_rows = compute_equity_curve_vs_bh()
    with open(AUDIT_DIR / "rule_equity_vs_bh.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(eq_rows[0].keys()))
        writer.writeheader()
        writer.writerows(eq_rows)
    print("Saved rule_equity_vs_bh.csv successfully.")
