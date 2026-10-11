"""Comprehensive audit script for Tasks 1, 3, and 4 under canonical fixed FX calendar.

Produces all CSVs in research/trend/audit/canonical_fxfixed/
"""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path
import numpy as np

from forward.feed import load_bars_from_db
from forward.config import SYMBOLS
from research.trend.indicators import load_daily_bars
from research.trend.models import TrendTrade
from research.trend.stats import block_bootstrap_calendar_months
from research.trend.strategies import run_t1_donchian, run_t2_sma_cross, run_t3_tsmom

DATA_FXFIXED_DIR = Path("research/trend/data_fxfixed")
OUTPUT_FXFIXED_DIR = Path("research/trend/output_fxfixed")
DATA_OLD_DIR = Path("research/trend/data")
OUTPUT_OLD_DIR = Path("research/trend/output")
AUDIT_CANONICAL_DIR = Path("research/trend/audit/canonical_fxfixed")

AUDIT_CANONICAL_DIR.mkdir(parents=True, exist_ok=True)
END_HISTORICAL = dt.date(2025, 12, 31)
END_ASOF_2026 = dt.date(2026, 10, 9)


# ==============================================================================
# Task 1: Silver Trades 2020-03-01 to 2020-10-31
# ==============================================================================
def run_task1_silver() -> list[dict]:
    rows = []
    # Using fixed output
    for rule in ["T1_DONCHIAN", "T2_SMA_CROSS", "T3_TSMOM"]:
        with open(OUTPUT_FXFIXED_DIR / f"{rule}_trades.csv", encoding="utf-8") as f:
            trades = list(csv.DictReader(f))
        for t in trades:
            if t["symbol"] == "XAGUSD":
                ed = t["entry_date"]
                xd = t["exit_date"]
                if ("2020-03-01" <= ed <= "2020-10-31") or ("2020-03-01" <= xd <= "2020-10-31"):
                    rows.append({
                        "rule": rule,
                        "trade_id": t["trade_id"],
                        "direction": t["direction"],
                        "entry_date": ed,
                        "exit_date": xd,
                        "entry_price": f"{float(t['entry_price']):.4f}",
                        "exit_price": f"{float(t['exit_price']):.4f}",
                        "stop_loss": f"{float(t['stop_loss']):.4f}",
                        "stop_distance": f"{float(t['stop_distance']):.4f}",
                        "gross_r": f"{float(t['gross_r']):+.4f}",
                        "net_r_0": f"{float(t['net_r_fin0']):+.4f}",
                        "net_r_2": f"{float(t['net_r']):+.4f}",
                        "exit_reason": t["exit_reason"],
                        "holding_days": t["holding_days"],
                    })

    csv_path = AUDIT_CANONICAL_DIR / "silver_trades_2020.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Task 1: Saved {len(rows)} silver trades to {csv_path}")
    return rows


# ==============================================================================
# Task 3: PERIOD_END vs ex-PERIOD_END Trades
# ==============================================================================
def run_task3_period_end() -> list[dict]:
    rows = []
    for rule in ["T2_SMA_CROSS", "T3_TSMOM"]:
        with open(OUTPUT_FXFIXED_DIR / f"{rule}_trades.csv", encoding="utf-8") as f:
            raw_trades = list(csv.DictReader(f))

        trades = []
        for t in raw_trades:
            t_copy = dict(t)
            t_copy["exit_date"] = dt.date.fromisoformat(t["exit_date"])
            t_copy["net_r"] = float(t["net_r"])
            t_copy["net_r_fin0"] = float(t["net_r_fin0"])
            trades.append(t_copy)

        period_end = [t for t in trades if t["exit_reason"] == "PERIOD_END"]
        ex_period_end = [t for t in trades if t["exit_reason"] != "PERIOD_END"]

        for carry, col in [("0%", "net_r_fin0"), ("2%", "net_r")]:
            tot_r = sum(t[col] for t in trades)
            pe_cnt = len(period_end)
            pe_sum = sum(t[col] for t in period_end)
            pe_pct = (pe_sum / tot_r * 100.0) if tot_r != 0 else 0.0

            ex_n = len(ex_period_end)
            ex_mean = float(np.mean([t[col] for t in ex_period_end]))
            boot = block_bootstrap_calendar_months(ex_period_end, metric_key=col, n_rounds=10_000, seed=42)

            rows.append({
                "rule": rule,
                "carry": carry,
                "period_end_count": pe_cnt,
                "period_end_summed_r": f"{pe_sum:+.4f}",
                "period_end_pct_of_total": f"{pe_pct:.2f}%",
                "ex_period_end_n": ex_n,
                "ex_period_end_mean_r": f"{ex_mean:+.4f}",
                "ex_ci_95_low": f"{boot['ci_95_low']:.4f}",
                "ex_ci_95_high": f"{boot['ci_95_high']:.4f}",
                "bootstrap_p_val": f"{boot['p_value_one_sided']:.4f}",
            })

    csv_path = AUDIT_CANONICAL_DIR / "period_end_audit.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Task 3: Saved PERIOD_END audit to {csv_path}")
    return rows


# ==============================================================================
# Task 4: Regression Test against Old (377) and Fixed (373)
# ==============================================================================
def run_task4_regression_check() -> list[dict]:
    def eval_suite(data_dir: Path, output_dir: Path):
        with open(output_dir / "T2_SMA_CROSS_trades.csv", encoding="utf-8") as f:
            exp_t2 = list(csv.DictReader(f))
        act_t2 = []
        for s in SYMBOLS:
            bars = load_daily_bars(data_dir / f"{s}_daily.csv", hard_stop_date=END_HISTORICAL)
            act_t2.extend(run_t2_sma_cross(s, bars, start_date=None, end_date=END_HISTORICAL))

        t2_pass = (len(act_t2) == len(exp_t2)) and all(
            a.trade_id == e["trade_id"] and a.entry_date.isoformat() == e["entry_date"]
            for a, e in zip(act_t2, exp_t2)
        )

        with open(output_dir / "T3_TSMOM_trades.csv", encoding="utf-8") as f:
            exp_t3 = list(csv.DictReader(f))
        act_t3 = []
        for s in SYMBOLS:
            bars = load_daily_bars(data_dir / f"{s}_daily.csv", hard_stop_date=END_HISTORICAL)
            act_t3.extend(run_t3_tsmom(s, bars, start_date=None, end_date=END_HISTORICAL))

        t3_pass = (len(act_t3) == len(exp_t3)) and all(
            a.trade_id == e["trade_id"] and a.entry_date.isoformat() == e["entry_date"]
            for a, e in zip(act_t3, exp_t3)
        )
        return len(exp_t2), len(act_t2), t2_pass, len(exp_t3), len(act_t3), t3_pass

    # 1. Fixed dataset against fixed output
    t2_exp_f, t2_act_f, t2_ok_f, t3_exp_f, t3_act_f, t3_ok_f = eval_suite(DATA_FXFIXED_DIR, OUTPUT_FXFIXED_DIR)
    # 2. Old dataset against old output
    t2_exp_o, t2_act_o, t2_ok_o, t3_exp_o, t3_act_o, t3_ok_o = eval_suite(DATA_OLD_DIR, OUTPUT_OLD_DIR)
    # 3. Fixed dataset against old output (cross)
    t2_exp_x, t2_act_x, t2_ok_x, t3_exp_x, t3_act_x, t3_ok_x = eval_suite(DATA_FXFIXED_DIR, OUTPUT_OLD_DIR)

    rows = [
        {
            "test_config": "Fixed Calendar (Canonical)",
            "data_source": "data_fxfixed",
            "target_output": "output_fxfixed",
            "t2_expected_n": t2_exp_f,
            "t2_actual_n": t2_act_f,
            "t2_result": "PASS" if t2_ok_f else "FAIL",
            "t3_expected_n": t3_exp_f,
            "t3_actual_n": t3_act_f,
            "t3_result": "PASS" if t3_ok_f else "FAIL",
        },
        {
            "test_config": "Old Unfixed Calendar",
            "data_source": "data",
            "target_output": "output",
            "t2_expected_n": t2_exp_o,
            "t2_actual_n": t2_act_o,
            "t2_result": "PASS" if t2_ok_o else "FAIL",
            "t3_expected_n": t3_exp_o,
            "t3_actual_n": t3_act_o,
            "t3_result": "PASS" if t3_ok_o else "FAIL",
        },
        {
            "test_config": "Cross-Check (Fixed data vs Old output)",
            "data_source": "data_fxfixed",
            "target_output": "output",
            "t2_expected_n": t2_exp_x,
            "t2_actual_n": t2_act_x,
            "t2_result": "PASS" if t2_ok_x else "FAIL",
            "t3_expected_n": t3_exp_x,
            "t3_actual_n": t3_act_x,
            "t3_result": "PASS" if t3_ok_x else "FAIL",
        },
    ]

    csv_path = AUDIT_CANONICAL_DIR / "regression_test_comparison.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Task 4: Saved regression test comparison to {csv_path}")
    return rows


# ==============================================================================
# Task 4: Canonical Long/Short Breakdown
# ==============================================================================
def run_task4_long_short_canonical() -> list[dict]:
    rows = []
    for rule in ["T1_DONCHIAN", "T2_SMA_CROSS", "T3_TSMOM"]:
        with open(OUTPUT_FXFIXED_DIR / f"{rule}_trades.csv", encoding="utf-8") as f:
            trades = list(csv.DictReader(f))
        for direction in ["LONG", "SHORT"]:
            sub = [t for t in trades if t["direction"] == direction]
            n = len(sub)
            gross = np.mean([float(t["gross_r"]) for t in sub]) if n else 0.0
            net0 = np.mean([float(t["net_r_fin0"]) for t in sub]) if n else 0.0
            net2 = np.mean([float(t["net_r"]) for t in sub]) if n else 0.0
            rows.append({
                "rule": rule,
                "side": direction,
                "n": n,
                "gross_mean_r": f"{gross:+.4f}",
                "net_mean_r_0": f"{net0:+.4f}",
                "net_mean_r_2": f"{net2:+.4f}",
                "low_n_flag": "[LOW N]" if n < 200 else "OK",
            })

    csv_path = AUDIT_CANONICAL_DIR / "long_short_canonical.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Task 4: Saved canonical long/short to {csv_path}")
    return rows


# ==============================================================================
# Task 4: Canonical Sigma (ddof=1)
# ==============================================================================
def run_task4_sigma_canonical() -> list[dict]:
    rows = []
    for rule in ["T1_DONCHIAN", "T2_SMA_CROSS", "T3_TSMOM"]:
        with open(OUTPUT_FXFIXED_DIR / f"{rule}_trades.csv", encoding="utf-8") as f:
            trades = list(csv.DictReader(f))
        gross = [float(t["gross_r"]) for t in trades]
        net0 = [float(t["net_r_fin0"]) for t in trades]
        net2 = [float(t["net_r"]) for t in trades]
        net4 = [float(t["net_r_fin4"]) for t in trades]
        n = len(trades)
        rows.append({
            "rule": rule,
            "n": n,
            "sigma_gross": f"{np.std(gross, ddof=1):.4f}",
            "sigma_net_0": f"{np.std(net0, ddof=1):.4f}",
            "sigma_net_2": f"{np.std(net2, ddof=1):.4f}",
            "sigma_net_4": f"{np.std(net4, ddof=1):.4f}",
            "low_n_flag": "[LOW N]" if n < 200 else "OK",
        })

    csv_path = AUDIT_CANONICAL_DIR / "sigma_canonical.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Task 4: Saved canonical sigma to {csv_path}")
    return rows


# ==============================================================================
# Task 4: Canonical Top-10 Trades (at 0% carry)
# ==============================================================================
def run_task4_top10_canonical() -> list[dict]:
    bars_by_sym = {s: load_daily_bars(DATA_FXFIXED_DIR / f"{s}_daily.csv") for s in SYMBOLS}
    rows = []

    for rule in ["T2_SMA_CROSS", "T3_TSMOM"]:
        with open(OUTPUT_FXFIXED_DIR / f"{rule}_trades.csv", encoding="utf-8") as f:
            trades = list(csv.DictReader(f))

        # Sort descending by net_r_fin0
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

            if rule == "T2_SMA_CROSS":
                sig_val = f"SMA50={sig_bar.sma50:.2f}, SMA200={sig_bar.sma200:.2f}"
            else:
                sig_val = f"12m_ret={sig_bar.return_252 * 100:+.2f}%"

            rows.append({
                "rule": rule,
                "rank": rank,
                "trade_id": t["trade_id"],
                "market": sym,
                "side": t["direction"],
                "signal_date": sig_bar.date.isoformat(),
                "entry_date": t["entry_date"],
                "exit_date": t["exit_date"],
                "entry_price": f"{entry_p:.4f}",
                "exit_price": f"{exit_p:.4f}",
                "stop_distance": f"{stop_dist:.4f}",
                "stop_pct_price": f"{stop_pct:.2f}%",
                "atr20_pct": f"{atr_pct:.2f}%",
                "signal_values": sig_val,
                "net_r_0": f"{net_r_0:+.2f}",
                "flag": flag,
            })

    csv_path = AUDIT_CANONICAL_DIR / "top10_canonical.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Task 4: Saved canonical top-10 to {csv_path}")
    return rows


# ==============================================================================
# Task 4: 2026 Fresh vs Carried Split on Canonical Fixed Data
# ==============================================================================
def run_task4_2026_split() -> list[dict]:
    rows = []
    runners = [("T2_SMA_CROSS", run_t2_sma_cross), ("T3_TSMOM", run_t3_tsmom)]

    for rule_name, runner in runners:
        fresh = []
        carried = []

        for sym in SYMBOLS:
            bars = load_bars_from_db(sym, as_of_date=END_ASOF_2026)
            trades = runner(sym, bars, start_date=None, end_date=END_ASOF_2026)
            for t in trades:
                if t.entry_date >= dt.date(2026, 1, 1):
                    fresh.append(t)
                elif t.exit_date >= dt.date(2026, 1, 1):
                    carried.append(t)

        for partition, t_list in [("FRESH_2026", fresh), ("CARRIED_INTO_2026", carried)]:
            n = len(t_list)
            r0 = [t.net_r_fin0 for t in t_list]
            r2 = [t.net_r for t in t_list]
            wins0 = [r for r in r0 if r > 0]
            wins2 = [r for r in r2 if r > 0]

            rows.append({
                "rule": rule_name,
                "partition": partition,
                "n": n,
                "mean_net_r_0": f"{np.mean(r0):+.4f}" if n else "0.0000",
                "win_rate_0": f"{len(wins0)/n*100:.1f}%" if n else "0.0%",
                "mean_net_r_2": f"{np.mean(r2):+.4f}" if n else "0.0000",
                "win_rate_2": f"{len(wins2)/n*100:.1f}%" if n else "0.0%",
                "sum_net_r_0": f"{sum(r0):+.4f}" if n else "0.0000",
                "sum_net_r_2": f"{sum(r2):+.4f}" if n else "0.0000",
                "low_n_flag": "[LOW N]" if n < 200 else "OK",
            })

    csv_path = AUDIT_CANONICAL_DIR / "split_2026_canonical.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Task 4: Saved 2026 fresh/carried split to {csv_path}")
    return rows


if __name__ == "__main__":
    run_task1_silver()
    run_task3_period_end()
    run_task4_regression_check()
    run_task4_long_short_canonical()
    run_task4_sigma_canonical()
    run_task4_top10_canonical()
    run_task4_2026_split()
