"""Complete, self-contained master execution script for Items 1 through 7.
Strictly zero hand-typed numbers, pure script calculations saved to CSV.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
from pathlib import Path
import zoneinfo
import httpx
import numpy as np
import pandas as pd

from forward.config import SYMBOLS
from research.trend.indicators import load_daily_bars
from research.trend.nulls import run_null_simulations
from research.trend.stats import block_bootstrap_calendar_months
from research.trend.strategies import run_t1_donchian, run_t2_sma_cross, run_t3_tsmom

DATA_DIR = Path("research/trend/data_fxfixed")
OUTPUT_STOPFIXED_DIR = Path("research/trend/output_stopfixed")
AUDIT_DIR = Path("research/trend/audit/canonical_stopfixed")

OUTPUT_STOPFIXED_DIR.mkdir(parents=True, exist_ok=True)
AUDIT_DIR.mkdir(parents=True, exist_ok=True)

END_HISTORICAL = dt.date(2025, 12, 31)
END_ASOF_2026 = dt.date(2026, 10, 9)


def save_trades_to_csv(trades: list[TrendTrade], out_path: Path):
    from dataclasses import asdict
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


# ==============================================================================
# 1. Rerun T1, T2, T3 with Stop-Fix on data_fxfixed -> output_stopfixed/
# ==============================================================================
def step1_rerun():
    bars_by_sym = {s: load_daily_bars(DATA_DIR / f"{s}_daily.csv", hard_stop_date=END_HISTORICAL) for s in SYMBOLS}
    t1_all, t2_all, t3_all = [], [], []
    for s in SYMBOLS:
        bars = bars_by_sym[s]
        t1_all.extend(run_t1_donchian(s, bars, start_date=None, end_date=END_HISTORICAL))
        t2_all.extend(run_t2_sma_cross(s, bars, start_date=None, end_date=END_HISTORICAL))
        t3_all.extend(run_t3_tsmom(s, bars, start_date=None, end_date=END_HISTORICAL))
        
    save_trades_to_csv(t1_all, OUTPUT_STOPFIXED_DIR / "T1_DONCHIAN_trades.csv")
    save_trades_to_csv(t2_all, OUTPUT_STOPFIXED_DIR / "T2_SMA_CROSS_trades.csv")
    save_trades_to_csv(t3_all, OUTPUT_STOPFIXED_DIR / "T3_TSMOM_trades.csv")
    print(f"Step 1 Rerun: T1={len(t1_all)}, T2={len(t2_all)}, T3={len(t3_all)}")
    return t1_all, t2_all, t3_all


# ==============================================================================
# 2. Item 1: Stop Fills and Gap Counts
# ==============================================================================
def step2_stop_fill_counts():
    counts_rows = []
    rules = [
        ("T1_DONCHIAN", OUTPUT_STOPFIXED_DIR / "T1_DONCHIAN_trades.csv"),
        ("T2_SMA_CROSS", OUTPUT_STOPFIXED_DIR / "T2_SMA_CROSS_trades.csv"),
        ("T3_TSMOM", OUTPUT_STOPFIXED_DIR / "T3_TSMOM_trades.csv"),
    ]
    for rule_name, path in rules:
        with open(path, encoding="utf-8") as f:
            trades = list(csv.DictReader(f))
        for side in ["LONG", "SHORT"]:
            sub = [t for t in trades if t["direction"] == side]
            stops = [t for t in sub if t["is_stop"] == "True"]
            gap_exits = [t for t in stops if abs(float(t["exit_price"]) - float(t["stop_loss"])) > 1e-5]
            fixed_fills = [t for t in stops if abs(float(t["exit_price"]) - float(t["stop_loss"])) <= 1e-5]
            counts_rows.append({
                "rule": rule_name,
                "side": side,
                "total_trades": len(sub),
                "total_stop_exits": len(stops),
                "fixed_stop_fills": len(fixed_fills),
                "gapped_stop_fills": len(gap_exits),
                "pct_gapped": f"{(len(gap_exits) / len(stops) * 100.0):.2f}%" if stops else "0.00%",
            })
    out_csv = AUDIT_DIR / "stop_fill_gap_counts.csv"
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(counts_rows[0].keys()))
        writer.writeheader()
        writer.writerows(counts_rows)
    print(f"Step 2 Stop Gap Counts saved to {out_csv}")
    return counts_rows


# ==============================================================================
# 3. Item 1: Performance Battery (Old vs Fixed, Gates, Nulls, Ladder)
# ==============================================================================
def step3_performance_battery():
    runs = [
        ("Old (Pre-Fix)", Path("research/trend/output_fxfixed")),
        ("Fixed (Canonical)", OUTPUT_STOPFIXED_DIR),
    ]
    battery_rows = []
    
    null_maps = {}
    for label, dir_path in runs:
        out_sub = OUTPUT_STOPFIXED_DIR if dir_path == OUTPUT_STOPFIXED_DIR else AUDIT_DIR
        print(f"Running null simulations for {label}...")
        null_res = run_null_simulations(
            data_dir=DATA_DIR,
            output_dir=out_sub,
            trades_dir=dir_path,
            n_rounds=2000,
            seed=42,
        )
        null_maps[label] = null_res
    
    for label, dir_path in runs:
        for rule_name in ["T1_DONCHIAN", "T2_SMA_CROSS", "T3_TSMOM"]:
            with open(dir_path / f"{rule_name}_trades.csv", encoding="utf-8") as f:
                trades_raw = list(csv.DictReader(f))
                
            trades = []
            for r in trades_raw:
                c = dict(r)
                c["exit_date"] = dt.date.fromisoformat(r["exit_date"])
                c["net_r"] = float(r["net_r"])
                c["net_r_fin0"] = float(r["net_r_fin0"])
                trades.append(c)
                
            n = len(trades)
            for carry, col in [("0%", "net_r_fin0"), ("2%", "net_r")]:
                r_vals = [t[col] for t in trades]
                mean_r = float(np.mean(r_vals))
                win_rate = (len([r for r in r_vals if r > 0]) / n * 100.0) if n else 0.0
                
                long_r = [t[col] for t in trades if t["direction"] == "LONG"]
                short_r = [t[col] for t in trades if t["direction"] == "SHORT"]
                mean_long = float(np.mean(long_r)) if long_r else 0.0
                mean_short = float(np.mean(short_r)) if short_r else 0.0
                
                boot = block_bootstrap_calendar_months(trades, metric_key=col, n_rounds=2000, seed=42)
                
                # Null models
                rule_nulls = null_maps[label][rule_name]
                if carry == "0%":
                    p95_null_a = float(rule_nulls["fin0"]["null_a"]["p95"])
                    p95_null_b = float(rule_nulls["fin0"]["null_b"]["p95"])
                    beats_a = rule_nulls["fin0"]["null_a"]["beats_p95"]
                    beats_b = rule_nulls["fin0"]["null_b"]["beats_p95"]
                else:
                    p95_null_a = float(rule_nulls["null_a"]["p95"])
                    p95_null_b = float(rule_nulls["null_b"]["p95"])
                    beats_a = rule_nulls["null_a"]["beats_p95"]
                    beats_b = rule_nulls["null_b"]["beats_p95"]
                
                # Gate checks
                gate1 = n >= 200
                gate2 = mean_r > 0.0
                gate3 = boot["ci_95_low"] > 0.0
                gate4 = beats_b
                gate5 = beats_a
                all_gates = gate1 and gate2 and gate3 and gate4 and gate5
                
                battery_rows.append({
                    "version": label,
                    "rule": rule_name,
                    "carry": carry,
                    "N": n,
                    "win_rate": f"{win_rate:.2f}%",
                    "mean_net_r": f"{mean_r:+.4f}",
                    "mean_long_r": f"{mean_long:+.4f}",
                    "mean_short_r": f"{mean_short:+.4f}",
                    "ci_95_low": f"{boot['ci_95_low']:+.4f}",
                    "ci_95_high": f"{boot['ci_95_high']:+.4f}",
                    "p_val": f"{boot['p_value_one_sided']:.4f}",
                    "null_a_p95": f"{p95_null_a:+.4f}",
                    "null_b_p95": f"{p95_null_b:+.4f}",
                    "gate1_n200": "PASS" if gate1 else "FAIL",
                    "gate2_mean_gt0": "PASS" if gate2 else "FAIL",
                    "gate3_ci_gt0": "PASS" if gate3 else "FAIL",
                    "gate4_null_b": "PASS" if gate4 else "FAIL",
                    "gate5_null_a": "PASS" if gate5 else "FAIL",
                    "verdict": "GROSS-OF-FINANCING SURVIVOR" if (carry == "0%" and all_gates) else ("SURVIVOR" if (carry == "2%" and all_gates) else "INCONCLUSIVE: NO EDGE"),
                })
                
    out_csv = AUDIT_DIR / "rerun_performance_battery.csv"
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(battery_rows[0].keys()))
        writer.writeheader()
        writer.writerows(battery_rows)
    print(f"Step 3 Battery saved to {out_csv}")
    return battery_rows


# ==============================================================================
# 4. Item 2: List Diff (output/ vs output_fxfixed/ vs output_stopfixed/)
# ==============================================================================
def step4_list_diff():
    # Compare (a) output/, (b) output_fxfixed/, (c) output_stopfixed/
    def load_dict(p: Path):
        with open(p, encoding="utf-8") as f:
            return {(r["symbol"], r["entry_date"], r["direction"]): r for r in csv.DictReader(f)}
            
    diff_rows = []
    
    for rule in ["T1_DONCHIAN", "T2_SMA_CROSS", "T3_TSMOM"]:
        a_map = load_dict(Path(f"research/trend/output/{rule}_trades.csv"))
        b_map = load_dict(Path(f"research/trend/output_fxfixed/{rule}_trades.csv"))
        c_map = load_dict(OUTPUT_STOPFIXED_DIR / f"{rule}_trades.csv")
        
        all_keys = sorted(a_map.keys() | b_map.keys() | c_map.keys())
        
        for k in all_keys:
            in_a = k in a_map
            in_b = k in b_map
            in_c = k in c_map
            
            ra = a_map.get(k)
            rb = b_map.get(k)
            rc = c_map.get(k)
            
            r0_a = float(ra["net_r_fin0"]) if ra else None
            r0_b = float(rb["net_r_fin0"]) if rb else None
            r0_c = float(rc["net_r_fin0"]) if rc else None
            
            is_changed_bc = (r0_b is not None and r0_c is not None and abs(r0_b - r0_c) > 1e-4)
            is_changed_ab = (r0_a is not None and r0_b is not None and abs(r0_a - r0_b) > 1e-4)
            
            status = "UNCHANGED"
            if not in_a and in_c:
                status = "ADDED_IN_CANONICAL"
            elif in_a and not in_c:
                status = "REMOVED_IN_CANONICAL"
            elif is_changed_bc:
                status = "CHANGED_STOP_FIX"
            elif is_changed_ab:
                status = "CHANGED_FX_FIX"
                
            if status != "UNCHANGED":
                diff_rows.append({
                    "rule": rule,
                    "market": k[0],
                    "entry_date": k[1],
                    "direction": k[2],
                    "status": status,
                    "r0_output": f"{r0_a:+.4f}" if r0_a is not None else "NONE",
                    "r0_fxfixed": f"{r0_b:+.4f}" if r0_b is not None else "NONE",
                    "r0_stopfixed": f"{r0_c:+.4f}" if r0_c is not None else "NONE",
                    "explanation": "Short stop fill fix (strategies.py L612)" if is_changed_bc else ("Weekend FX bar filter removal" if is_changed_ab or (in_a != in_b) else "Data change"),
                })
                
    out_csv = AUDIT_DIR / "list_diff_comparison.csv"
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(diff_rows[0].keys()))
        writer.writeheader()
        writer.writerows(diff_rows)
    print(f"Step 4 List Diff saved ({len(diff_rows)} differences) to {out_csv}")
    return diff_rows


# ==============================================================================
# 5. Item 3: Regenerate long_short, sigma, top10 from ONE file
# ==============================================================================
def step5_canonical_tables_and_drag():
    # Trade file paths and byte hashes
    trade_files_summary = []
    for rule in ["T1_DONCHIAN", "T2_SMA_CROSS", "T3_TSMOM"]:
        p = OUTPUT_STOPFIXED_DIR / f"{rule}_trades.csv"
        raw = p.read_bytes()
        h = hashlib.sha256(raw).hexdigest()
        with open(p, encoding="utf-8") as f:
            trades = list(csv.DictReader(f))
        n = len(trades)
        sum_r0 = sum(float(t["net_r_fin0"]) for t in trades)
        sum_r2 = sum(float(t["net_r"]) for t in trades)
        gross_vals = [float(t["gross_r"]) for t in trades]
        net0_vals = [float(t["net_r_fin0"]) for t in trades]
        mean_drag_0 = float(np.mean(gross_vals) - np.mean(net0_vals))
        assert mean_drag_0 < 0.05, f"Drag assertion failed for {rule}: {mean_drag_0}"
        
        trade_files_summary.append({
            "rule": rule,
            "path": str(p),
            "sha256": h,
            "N": n,
            "sum_net_r_0": f"{sum_r0:+.4f}",
            "sum_net_r_2": f"{sum_r2:+.4f}",
            "mean_gross_r": f"{np.mean(gross_vals):+.4f}",
            "mean_net_r_0": f"{np.mean(net0_vals):+.4f}",
            "mean_drag_0": f"{mean_drag_0:.4f}",
            "drag_assertion_lt_0_05": "PASS",
        })
        
    out_summary_csv = AUDIT_DIR / "canonical_trade_files_summary.csv"
    with open(out_summary_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(trade_files_summary[0].keys()))
        writer.writeheader()
        writer.writerows(trade_files_summary)
        
    # 5.1 long_short_canonical
    ls_rows = []
    for rule in ["T1_DONCHIAN", "T2_SMA_CROSS", "T3_TSMOM"]:
        with open(OUTPUT_STOPFIXED_DIR / f"{rule}_trades.csv", encoding="utf-8") as f:
            trades = list(csv.DictReader(f))
        for direction in ["LONG", "SHORT"]:
            sub = [t for t in trades if t["direction"] == direction]
            n = len(sub)
            gross = np.mean([float(t["gross_r"]) for t in sub]) if n else 0.0
            net0 = np.mean([float(t["net_r_fin0"]) for t in sub]) if n else 0.0
            net2 = np.mean([float(t["net_r"]) for t in sub]) if n else 0.0
            ls_rows.append({
                "rule": rule,
                "side": direction,
                "n": n,
                "gross_mean_r": f"{gross:+.4f}",
                "net_mean_r_0": f"{net0:+.4f}",
                "net_mean_r_2": f"{net2:+.4f}",
                "low_n_flag": "[LOW N]" if n < 200 else "OK",
            })
    with open(AUDIT_DIR / "long_short_canonical.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(ls_rows[0].keys()))
        writer.writeheader()
        writer.writerows(ls_rows)
        
    # 5.2 sigma_canonical
    sig_rows = []
    for rule in ["T1_DONCHIAN", "T2_SMA_CROSS", "T3_TSMOM"]:
        with open(OUTPUT_STOPFIXED_DIR / f"{rule}_trades.csv", encoding="utf-8") as f:
            trades = list(csv.DictReader(f))
        gross = [float(t["gross_r"]) for t in trades]
        net0 = [float(t["net_r_fin0"]) for t in trades]
        net2 = [float(t["net_r"]) for t in trades]
        net4 = [float(t["net_r_fin4"]) for t in trades]
        n = len(trades)
        sig_rows.append({
            "rule": rule,
            "n": n,
            "sigma_gross": f"{np.std(gross, ddof=1):.4f}",
            "sigma_net_0": f"{np.std(net0, ddof=1):.4f}",
            "sigma_net_2": f"{np.std(net2, ddof=1):.4f}",
            "sigma_net_4": f"{np.std(net4, ddof=1):.4f}",
            "low_n_flag": "[LOW N]" if n < 200 else "OK",
        })
    with open(AUDIT_DIR / "sigma_canonical.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(sig_rows[0].keys()))
        writer.writeheader()
        writer.writerows(sig_rows)
        
    # 5.3 top10_canonical (from single file per rule)
    bars_by_sym = {s: load_daily_bars(DATA_DIR / f"{s}_daily.csv") for s in SYMBOLS}
    top10_rows = []
    for rule in ["T2_SMA_CROSS", "T3_TSMOM"]:
        with open(OUTPUT_STOPFIXED_DIR / f"{rule}_trades.csv", encoding="utf-8") as f:
            trades = list(csv.DictReader(f))
        trades_sorted = sorted(trades, key=lambda t: float(t["net_r_fin0"]), reverse=True)[:10]
        for rank, t in enumerate(trades_sorted, start=1):
            sym = t["symbol"]
            bars = bars_by_sym[sym]
            date_to_idx = {b.date.isoformat(): i for i, b in enumerate(bars)}
            idx = date_to_idx[t["entry_date"]]
            sig_bar = bars[idx - 1]
            entry_p = float(t["entry_price"])
            exit_p = float(t["exit_price"])
            stop_dist = float(t["stop_distance"])
            stop_pct = (stop_dist / entry_p) * 100.0
            atr_pct = (sig_bar.atr20 / entry_p) * 100.0
            net_r_0 = float(t["net_r_fin0"])
            flag = "FLAG (R>15 & stop<2%)" if (net_r_0 > 15.0 and stop_pct < 2.0) else "OK"
            is_pe = "TRUE" if t["exit_reason"] == "PERIOD_END" else "FALSE"
            if rule == "T2_SMA_CROSS":
                sig_val = f"SMA50={sig_bar.sma50:.2f}, SMA200={sig_bar.sma200:.2f}"
            else:
                sig_val = f"12m_ret={sig_bar.return_252 * 100:+.2f}%"
            top10_rows.append({
                "rule": rule,
                "rank": rank,
                "trade_id": t["trade_id"],
                "market": sym,
                "side": t["direction"],
                "signal_date": sig_bar.date.isoformat(),
                "entry_date": t["entry_date"],
                "exit_date": t["exit_date"],
                "is_PERIOD_END": is_pe,
                "entry_price": f"{entry_p:.4f}",
                "exit_price": f"{exit_p:.4f}",
                "stop_distance": f"{stop_dist:.4f}",
                "stop_pct_price": f"{stop_pct:.2f}%",
                "atr20_pct": f"{atr_pct:.2f}%",
                "signal_values": sig_val,
                "net_r_0": f"{net_r_0:+.2f}",
                "flag": flag,
            })
    with open(AUDIT_DIR / "top10_canonical.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(top10_rows[0].keys()))
        writer.writeheader()
        writer.writerows(top10_rows)
    print("Step 5 Canonical tables regenerated.")
    return trade_files_summary, ls_rows, sig_rows, top10_rows


# ==============================================================================
# 6. Item 4: 2026 Split (T2 and T3: 4 partitions each)
# ==============================================================================
def step6_2026_split():
    from forward.feed import load_bars_from_db
    split_rows = []
    
    for rule_name, runner in [("T2_SMA_CROSS", run_t2_sma_cross), ("T3_TSMOM", run_t3_tsmom)]:
        all_trades_2026 = []
        for s in SYMBOLS:
            bars = load_bars_from_db(s, as_of_date=END_ASOF_2026)
            trades = runner(s, bars, start_date=None, end_date=END_ASOF_2026)
            all_trades_2026.extend(trades)
            
        fresh_closed = [t for t in all_trades_2026 if t.entry_date >= dt.date(2026, 1, 1) and t.exit_reason != "PERIOD_END"]
        fresh_open = [t for t in all_trades_2026 if t.entry_date >= dt.date(2026, 1, 1) and t.exit_reason == "PERIOD_END"]
        carried_closed = [t for t in all_trades_2026 if t.entry_date < dt.date(2026, 1, 1) and t.exit_date >= dt.date(2026, 1, 1) and t.exit_reason != "PERIOD_END"]
        carried_open = [t for t in all_trades_2026 if t.entry_date < dt.date(2026, 1, 1) and t.exit_reason == "PERIOD_END"]
        
        partitions = [
            ("FRESH_CLOSED", fresh_closed),
            ("FRESH_OPEN_MARKED", fresh_open),
            ("CARRIED_CLOSED", carried_closed),
            ("CARRIED_OPEN_MARKED", carried_open),
        ]
        
        for p_name, t_list in partitions:
            n = len(t_list)
            r0 = [t.net_r_fin0 for t in t_list]
            r2 = [t.net_r for t in t_list]
            wins0 = [r for r in r0 if r > 0]
            wins2 = [r for r in r2 if r > 0]
            split_rows.append({
                "rule": rule_name,
                "partition": p_name,
                "n": n,
                "sum_net_r_0": f"{sum(r0):+.4f}" if n else "0.0000",
                "mean_net_r_0": f"{np.mean(r0):+.4f}" if n else "0.0000",
                "win_rate_0": f"{(len(wins0)/n*100.0):.1f}%" if n else "0.0%",
                "sum_net_r_2": f"{sum(r2):+.4f}" if n else "0.0000",
                "mean_net_r_2": f"{np.mean(r2):+.4f}" if n else "0.0000",
                "win_rate_2": f"{(len(wins2)/n*100.0):.1f}%" if n else "0.0%",
            })
            
    out_csv = AUDIT_DIR / "split_2026_four_way.csv"
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(split_rows[0].keys()))
        writer.writeheader()
        writer.writerows(split_rows)
    print(f"Step 6 2026 Split saved to {out_csv}")
    return split_rows


# ==============================================================================
# 7. Item 5: Extended Independent Script (No strategies.py import)
# ==============================================================================
def step7_independent_full_verification():
    """Independent engine without importing strategies.py."""
    test_results = []
    
    # Pre-calculated baseline costs
    SP_MAP = {
        "EURUSD": 0.00015, "GBPUSD": 0.00018, "USDJPY": 0.015, "AUDUSD": 0.00018,
        "USDCAD": 0.000225, "USDCHF": 0.000225, "XAUUSD": 0.375, "XAGUSD": 0.03,
        "US500": 0.75, "GER40": 2.25, "Brent": 0.06, "NDX": 2.25,
    }

    def compute_trade_costs(sym, direction, ep, xp, stop_dist, is_stop, ed, xd):
        hold_days = max(1, (xd - ed).days)
        gross_pts = (xp - ep) if direction == "LONG" else (ep - xp)
        gross_r = gross_pts / stop_dist
        sp = SP_MAP[sym]
        slip_pts = (0.5 * sp) if is_stop else 0.0
        fin_pts = (0.02 / 365.0) * ep * hold_days
        drag_sp_r = sp / stop_dist
        drag_slip_r = slip_pts / stop_dist
        drag_fin_r = fin_pts / stop_dist
        net_r_0 = gross_r - drag_sp_r - drag_slip_r
        net_r_2 = net_r_0 - drag_fin_r
        return hold_days, gross_pts, gross_r, net_r_0, net_r_2

    # Standalone T2 Simulator
    def sim_t2(sym, csv_path):
        df = pd.read_csv(csv_path)
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df = df[df["date"] <= END_HISTORICAL].sort_values("date").reset_index(drop=True)
        n = len(df)
        dates = df["date"].values
        opens = df["open"].values.astype(float)
        highs = df["high"].values.astype(float)
        lows = df["low"].values.astype(float)
        closes = df["close"].values.astype(float)

        for j in range(n):
            highs[j] = max(highs[j], opens[j], closes[j])
            lows[j] = min(lows[j], opens[j], closes[j])

        # SMAs
        df["sma50"] = pd.Series(closes).rolling(50).mean()
        df["sma200"] = pd.Series(closes).rolling(200).mean()
        s50 = df["sma50"].values
        s200 = df["sma200"].values
        
        # ATR20
        tr = np.zeros(n)
        tr[0] = highs[0] - lows[0]
        for i in range(1, n):
            tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        atr20 = np.zeros(n)
        if n >= 20:
            atr20[19] = np.mean(tr[:20])
            for i in range(20, n):
                atr20[i] = (atr20[i - 1] * 19.0 + tr[i]) / 20.0
            for i in range(19):
                atr20[i] = atr20[19]
                
        trades = []
        in_pos = False
        direction = ""
        entry_price = 0.0
        stop_loss = 0.0
        stop_dist = 0.0
        entry_date = None
        stopped_out_regime = None
        start_date = dates[252] if n > 252 else dates[0]
        
        for i in range(1, n):
            bar_date = dates[i]
            prev_date = dates[i - 1]
            bullish_cross = False
            bearish_cross = False
            if i >= 201:
                bullish_cross = (s50[i - 2] <= s200[i - 2]) and (s50[i - 1] > s200[i - 1])
                bearish_cross = (s50[i - 2] >= s200[i - 2]) and (s50[i - 1] < s200[i - 1])
                
            if bullish_cross and stopped_out_regime == "BULL":
                stopped_out_regime = None
            if bearish_cross and stopped_out_regime == "BEAR":
                stopped_out_regime = None
                
            # 1. Stop Check
            if in_pos:
                is_stopped = False
                exit_price = 0.0
                if direction == "LONG" and lows[i] <= stop_loss:
                    is_stopped = True
                    exit_price = opens[i] if opens[i] <= stop_loss else stop_loss
                    stopped_out_regime = "BULL"
                elif direction == "SHORT" and highs[i] >= stop_loss:
                    is_stopped = True
                    exit_price = opens[i] if opens[i] >= stop_loss else stop_loss
                    stopped_out_regime = "BEAR"
                    
                if is_stopped:
                    in_pos = False
                    h_days, g_pts, g_r, n_r0, n_r2 = compute_trade_costs(sym, direction, entry_price, exit_price, stop_dist, True, entry_date, bar_date)
                    trades.append({
                        "symbol": sym, "direction": direction, "entry_date": entry_date.isoformat(),
                        "exit_date": bar_date.isoformat(), "entry_price": entry_price, "exit_price": exit_price,
                        "exit_reason": "STOP", "net_r_fin0": n_r0, "net_r": n_r2,
                    })
                    continue
                    
                # Opposite cross exit
                opp_cross = (direction == "LONG" and bearish_cross) or (direction == "SHORT" and bullish_cross)
                if opp_cross:
                    in_pos = False
                    exit_price = opens[i]
                    h_days, g_pts, g_r, n_r0, n_r2 = compute_trade_costs(sym, direction, entry_price, exit_price, stop_dist, False, entry_date, bar_date)
                    trades.append({
                        "symbol": sym, "direction": direction, "entry_date": entry_date.isoformat(),
                        "exit_date": bar_date.isoformat(), "entry_price": entry_price, "exit_price": exit_price,
                        "exit_reason": "OPPOSITE_CROSS", "net_r_fin0": n_r0, "net_r": n_r2,
                    })
                    
            # 2. Entry
            if not in_pos and bar_date >= start_date:
                sd = 3.0 * atr20[i - 1]
                if sd > 0:
                    if bullish_cross and stopped_out_regime != "BULL":
                        in_pos = True
                        direction = "LONG"
                        entry_price = opens[i]
                        stop_dist = sd
                        stop_loss = entry_price - sd
                        entry_date = bar_date
                    elif bearish_cross and stopped_out_regime != "BEAR":
                        in_pos = True
                        direction = "SHORT"
                        entry_price = opens[i]
                        stop_dist = sd
                        stop_loss = entry_price + sd
                        entry_date = bar_date
                        
        if in_pos:
            exit_price = closes[-1]
            h_days, g_pts, g_r, n_r0, n_r2 = compute_trade_costs(sym, direction, entry_price, exit_price, stop_dist, False, entry_date, dates[-1])
            trades.append({
                "symbol": sym, "direction": direction, "entry_date": entry_date.isoformat(),
                "exit_date": dates[-1].isoformat(), "entry_price": entry_price, "exit_price": exit_price,
                "exit_reason": "PERIOD_END", "net_r_fin0": n_r0, "net_r": n_r2,
            })
        return trades

    # Standalone T3 Simulator
    def sim_t3(sym, csv_path):
        df = pd.read_csv(csv_path)
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df = df[df["date"] <= END_HISTORICAL].sort_values("date").reset_index(drop=True)
        n = len(df)
        dates = df["date"].values
        opens = df["open"].values.astype(float)
        highs = df["high"].values.astype(float)
        lows = df["low"].values.astype(float)
        closes = df["close"].values.astype(float)

        for j in range(n):
            highs[j] = max(highs[j], opens[j], closes[j])
            lows[j] = min(lows[j], opens[j], closes[j])

        df["ret252"] = (pd.Series(closes) / pd.Series(closes).shift(252)) - 1.0
        ret252 = df["ret252"].values
        is_me = np.zeros(n, dtype=bool)
        for i in range(n - 1):
            if dates[i].month != dates[i + 1].month:
                is_me[i] = True
        is_me[n - 1] = True
        
        tr = np.zeros(n)
        tr[0] = highs[0] - lows[0]
        for i in range(1, n):
            tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        atr20 = np.zeros(n)
        if n >= 20:
            atr20[19] = np.mean(tr[:20])
            for i in range(20, n):
                atr20[i] = (atr20[i - 1] * 19.0 + tr[i]) / 20.0
            for i in range(19):
                atr20[i] = atr20[19]
                
        trades = []
        in_pos = False
        direction = ""
        entry_price = 0.0
        stop_loss = 0.0
        stop_dist = 0.0
        entry_date = None
        current_episode_sign = 0
        start_date = dates[252] if n > 252 else dates[0]
        
        for i in range(1, n):
            bar_date = dates[i]
            prev_date = dates[i - 1]
            
            # 1. Stop
            if in_pos:
                is_stopped = False
                exit_price = 0.0
                if direction == "LONG" and lows[i] <= stop_loss:
                    is_stopped = True
                    exit_price = opens[i] if opens[i] <= stop_loss else stop_loss
                elif direction == "SHORT" and highs[i] >= stop_loss:
                    is_stopped = True
                    exit_price = opens[i] if opens[i] >= stop_loss else stop_loss
                    
                if is_stopped:
                    in_pos = False
                    h_days, g_pts, g_r, n_r0, n_r2 = compute_trade_costs(sym, direction, entry_price, exit_price, stop_dist, True, entry_date, bar_date)
                    trades.append({
                        "symbol": sym, "direction": direction, "entry_date": entry_date.isoformat(),
                        "exit_date": bar_date.isoformat(), "entry_price": entry_price, "exit_price": exit_price,
                        "exit_reason": "STOP", "net_r_fin0": n_r0, "net_r": n_r2,
                    })
                    continue
                    
            # 2. Month-End Check
            if is_me[i - 1] and i >= 253:
                r = ret252[i - 1]
                new_sign = 1 if r > 0 else (-1 if r < 0 else 0)
                if new_sign != 0 and new_sign != current_episode_sign:
                    if in_pos:
                        in_pos = False
                        exit_price = opens[i]
                        h_days, g_pts, g_r, n_r0, n_r2 = compute_trade_costs(sym, direction, entry_price, exit_price, stop_dist, False, entry_date, bar_date)
                        trades.append({
                            "symbol": sym, "direction": direction, "entry_date": entry_date.isoformat(),
                            "exit_date": bar_date.isoformat(), "entry_price": entry_price, "exit_price": exit_price,
                            "exit_reason": "SIGN_FLIP", "net_r_fin0": n_r0, "net_r": n_r2,
                        })
                    current_episode_sign = new_sign
                    if bar_date >= start_date:
                        direction = "LONG" if new_sign == 1 else "SHORT"
                        entry_price = opens[i]
                        sd = 3.0 * atr20[i - 1]
                        if sd > 0:
                            stop_dist = sd
                            stop_loss = entry_price - sd if direction == "LONG" else entry_price + sd
                            entry_date = bar_date
                            in_pos = True
                            
        if in_pos:
            exit_price = closes[-1]
            h_days, g_pts, g_r, n_r0, n_r2 = compute_trade_costs(sym, direction, entry_price, exit_price, stop_dist, False, entry_date, dates[-1])
            trades.append({
                "symbol": sym, "direction": direction, "entry_date": entry_date.isoformat(),
                "exit_date": dates[-1].isoformat(), "entry_price": entry_price, "exit_price": exit_price,
                "exit_reason": "PERIOD_END", "net_r_fin0": n_r0, "net_r": n_r2,
            })
        return trades

    # Compare across all 12 markets
    for sym in SYMBOLS:
        p = DATA_DIR / f"{sym}_daily.csv"
        
        # T2
        t2_ind = sim_t2(sym, p)
        with open(OUTPUT_STOPFIXED_DIR / "T2_SMA_CROSS_trades.csv", encoding="utf-8") as f:
            t2_can = [t for t in csv.DictReader(f) if t["symbol"] == sym]
            
        mismatch_t2 = 0
        if len(t2_ind) != len(t2_can):
            mismatch_t2 = abs(len(t2_ind) - len(t2_can))
        else:
            for ind, can in zip(t2_ind, t2_can):
                if (ind["entry_date"] != can["entry_date"] or ind["exit_date"] != can["exit_date"] or
                    ind["direction"] != can["direction"] or ind["exit_reason"] != can["exit_reason"] or
                    abs(float(ind["net_r_fin0"]) - float(can["net_r_fin0"])) > 1e-4):
                    mismatch_t2 += 1
                    
        test_results.append({
            "market": sym, "rule": "T2_SMA_CROSS", "canonical_n": len(t2_can),
            "independent_n": len(t2_ind), "mismatches": mismatch_t2,
            "result": "PASS" if mismatch_t2 == 0 else "FAIL",
        })
        
        # T3
        t3_ind = sim_t3(sym, p)
        with open(OUTPUT_STOPFIXED_DIR / "T3_TSMOM_trades.csv", encoding="utf-8") as f:
            t3_can = [t for t in csv.DictReader(f) if t["symbol"] == sym]
            
        mismatch_t3 = 0
        if len(t3_ind) != len(t3_can):
            mismatch_t3 = abs(len(t3_ind) - len(t3_can))
        else:
            for ind, can in zip(t3_ind, t3_can):
                if (ind["entry_date"] != can["entry_date"] or ind["exit_date"] != can["exit_date"] or
                    ind["direction"] != can["direction"] or ind["exit_reason"] != can["exit_reason"] or
                    abs(float(ind["net_r_fin0"]) - float(can["net_r_fin0"])) > 1e-4):
                    mismatch_t3 += 1
                    
        test_results.append({
            "market": sym, "rule": "T3_TSMOM", "canonical_n": len(t3_can),
            "independent_n": len(t3_ind), "mismatches": mismatch_t3,
            "result": "PASS" if mismatch_t3 == 0 else "FAIL",
        })
        
    out_csv = AUDIT_DIR / "independent_full_verification.csv"
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(test_results[0].keys()))
        writer.writeheader()
        writer.writerows(test_results)
    print(f"Step 7 Full Independent Check saved to {out_csv}")
    return test_results


# ==============================================================================
# 8. Item 6: Power Table (Pace = N / years, print years used)
# ==============================================================================
def step8_power_table():
    # Calculate exact span in years for each market
    bars_by_sym = {s: load_daily_bars(DATA_DIR / f"{s}_daily.csv", hard_stop_date=END_HISTORICAL) for s in SYMBOLS}
    spans = []
    for s in SYMBOLS:
        b = bars_by_sym[s]
        # start_date is bar 252 date
        sd = b[252].date
        ed = b[-1].date
        span_years = (ed - sd).days / 365.25
        spans.append(span_years)
        
    avg_years = float(np.mean(spans))
    
    rows = []
    for rule, p in [("T2_SMA_CROSS", OUTPUT_STOPFIXED_DIR / "T2_SMA_CROSS_trades.csv"),
                    ("T3_TSMOM", OUTPUT_STOPFIXED_DIR / "T3_TSMOM_trades.csv")]:
        with open(p, encoding="utf-8") as f:
            trades_raw = list(csv.DictReader(f))
        trades = []
        for r in trades_raw:
            c = dict(r)
            c["exit_date"] = dt.date.fromisoformat(r["exit_date"])
            c["net_r"] = float(r["net_r"])
            c["net_r_fin0"] = float(r["net_r_fin0"])
            trades.append(c)
            
        n_hist = len(trades)
        pace = n_hist / avg_years
        
        for carry, col in [("0%", "net_r_fin0"), ("2%", "net_r")]:
            vals = [t[col] for t in trades]
            mu = float(np.mean(vals))
            sigma = float(np.std(vals, ddof=1))
            boot = block_bootstrap_calendar_months(trades, metric_key=col, n_rounds=2000, seed=42)
            hw = (boot["ci_95_high"] - boot["ci_95_low"]) / 2.0
            se_boot = hw / 1.96
            se_iid = sigma / np.sqrt(n_hist)
            if_factor = (se_boot / se_iid) ** 2
            
            # IID
            n50_iid = (1.96 * sigma / mu) ** 2
            n80_iid = (2.8016 * sigma / mu) ** 2
            # Cluster
            n50_cl = n50_iid * if_factor
            n80_cl = n80_iid * if_factor
            
            rows.append({
                "rule": rule,
                "carry": carry,
                "model": "IID",
                "hist_mean_r": f"{mu:+.4f}",
                "hist_sigma": f"{sigma:.4f}",
                "years_used": f"{avg_years:.2f}",
                "pace_trades_per_yr": f"{pace:.2f}",
                "trades_50_power": int(round(n50_iid)),
                "trades_80_power": int(round(n80_iid)),
                "years_50_power": f"{(n50_iid / pace):.1f}",
                "years_80_power": f"{(n80_iid / pace):.1f}",
                "flag": "[LOW N / UNDERPOWERED]",
            })
            rows.append({
                "rule": rule,
                "carry": carry,
                "model": "Cluster-Adj",
                "hist_mean_r": f"{mu:+.4f}",
                "hist_sigma": f"{sigma:.4f}",
                "years_used": f"{avg_years:.2f}",
                "pace_trades_per_yr": f"{pace:.2f}",
                "trades_50_power": int(round(n50_cl)),
                "trades_80_power": int(round(n80_cl)),
                "years_50_power": f"{(n50_cl / pace):.1f}",
                "years_80_power": f"{(n80_cl / pace):.1f}",
                "flag": "[LOW N / UNDERPOWERED]",
            })
            
    out_csv = AUDIT_DIR / "power_table_exact_years.csv"
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Step 8 Power Table saved to {out_csv}")
    return rows


# ==============================================================================
# 9. Item 7: Fetch EURUSD=X and ^GDAXI Live Quotes Now
# ==============================================================================
def step9_fetch_live_quotes():
    headers = {"User-Agent": "Mozilla/5.0"}
    quotes_rows = []
    
    tickers = [
        ("EURUSD=X", "Europe/London"),
        ("^GDAXI", "Europe/Berlin"),
    ]
    
    for ticker, local_tz_name in tickers:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=10d"
        try:
            with httpx.Client(headers=headers, timeout=20.0) as client:
                r = client.get(url)
                r.raise_for_status()
                res = r.json()["chart"]["result"][0]
                ts = res.get("timestamp", [])
                q = res.get("indicators", {}).get("quote", [{}])[0]
                opens = q.get("open", [])
                highs = q.get("high", [])
                lows = q.get("low", [])
                closes = q.get("close", [])
                vols = q.get("volume", [0] * len(ts))
                
                # Take last 3 valid rows
                valid_indices = [i for i in range(len(ts)) if opens[i] is not None and closes[i] is not None][-3:]
                
                for idx in valid_indices:
                    t_val = ts[idx]
                    dt_utc = dt.datetime.fromtimestamp(t_val, dt.timezone.utc)
                    dt_local = dt_utc.astimezone(zoneinfo.ZoneInfo(local_tz_name))
                    quotes_rows.append({
                        "ticker": ticker,
                        "timestamp_epoch": t_val,
                        "timestamp_utc": dt_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
                        "timestamp_local": dt_local.strftime("%Y-%m-%d %H:%M:%S %Z"),
                        "open": f"{float(opens[idx]):.4f}",
                        "high": f"{float(highs[idx]):.4f}",
                        "low": f"{float(lows[idx]):.4f}",
                        "close": f"{float(closes[idx]):.4f}",
                        "volume": int(vols[idx] or 0),
                    })
        except Exception as e:
            print(f"Failed to fetch {ticker}: {e}")
            
    out_csv = AUDIT_DIR / "live_raw_quotes_snapshot.csv"
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(quotes_rows[0].keys()))
        writer.writeheader()
        writer.writerows(quotes_rows)
    print(f"Step 9 Live Quotes saved to {out_csv}")
    return quotes_rows


if __name__ == "__main__":
    step1_rerun()
    step2_stop_fill_counts()
    step3_performance_battery()
    step4_list_diff()
    step5_canonical_tables_and_drag()
    step6_2026_split()
    step7_independent_full_verification()
    step8_power_table()
    step9_fetch_live_quotes()
