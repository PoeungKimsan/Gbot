"""Step 3: Portfolio Simulation.

Combines Judas and Silver Bullet trade CSVs.
Reports:
- Net and Gross R per year
- Max Drawdown (peak-to-trough in R)
- Average trades per day
- Daily PnL correlation between Judas and Silver Bullet
- Comparison of daily-loss rules:
  * Unconstrained (no rule)
  * Daily-loss rule (-2R/day kill-switch)
  * Weekly-loss rule (-5R/week kill-switch)
  * Drawdown-from-peak kill switch (-10R)
"""

from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path
import sys
from typing import Any
import numpy as np

sys.stdout.reconfigure(line_buffering=True)


def parse_date(date_str: str) -> dt.date:
    # Handles ISO timestamp or date
    if "T" in date_str:
        return dt.datetime.fromisoformat(date_str).date()
    elif " " in date_str:
        return dt.datetime.strptime(date_str.split()[0], "%Y-%m-%d").date()
    else:
        return dt.date.fromisoformat(date_str[:10])


def load_judas_trades(csv_path: str) -> list[dict[str, Any]]:
    trades = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            entry_time = row.get("entry_time") or row.get("timestamp") or ""
            d = parse_date(entry_time)
            gross_r = float(row.get("gross_r", row.get("r_multiple", 0.0)))
            net_r = float(row.get("net_r", gross_r))
            trades.append({
                "strategy": "Judas",
                "entry_time": entry_time,
                "date": d,
                "year": d.year,
                "gross_r": gross_r,
                "net_r": net_r,
            })
    return trades


def load_sb_trades(csv_path: str) -> list[dict[str, Any]]:
    trades = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            entry_time = row.get("entry_time") or row.get("timestamp") or ""
            d = parse_date(entry_time)
            gross_r = float(row.get("gross_r", row.get("r_multiple", 0.0)))
            net_r = float(row.get("net_r", gross_r))
            trades.append({
                "strategy": "SilverBullet",
                "symbol": row.get("symbol", "XAUUSD"),
                "entry_time": entry_time,
                "date": d,
                "year": d.year,
                "gross_r": gross_r,
                "net_r": net_r,
            })
    return trades


def simulate_rules(trades: list[dict[str, Any]]) -> dict[str, Any]:
    # Sort trades chronologically
    sorted_trades = sorted(trades, key=lambda t: t["entry_time"])

    # 1. Unconstrained
    uncon_trades = sorted_trades

    # 2. Daily loss limit (-2R / day)
    d2_trades = []
    daily_cum: dict[dt.date, float] = {}
    for t in sorted_trades:
        d = t["date"]
        cur = daily_cum.get(d, 0.0)
        if cur <= -2.0:
            # Latched for rest of day
            continue
        d2_trades.append(t)
        daily_cum[d] = cur + t["net_r"]

    # 3. Weekly loss limit (-5R / week)
    w5_trades = []
    weekly_cum: dict[tuple[int, int], float] = {} # (year, week_num) -> cum_r
    for t in sorted_trades:
        d = t["date"]
        wk = (d.year, d.isocalendar()[1])
        cur = weekly_cum.get(wk, 0.0)
        if cur <= -5.0:
            # Latched for rest of week
            continue
        w5_trades.append(t)
        weekly_cum[wk] = cur + t["net_r"]

    # 4. Trailing drawdown from peak kill-switch (-10R)
    dd10_trades = []
    cum = 0.0
    peak = 0.0
    halted = False
    for t in sorted_trades:
        if halted:
            break
        dd10_trades.append(t)
        cum += t["net_r"]
        if cum > peak:
            peak = cum
        if cum - peak <= -10.0:
            halted = True

    def compute_stats(tr_list: list[dict[str, Any]]) -> dict[str, Any]:
        if not tr_list:
            return {
                "n": 0,
                "gross_r": 0.0,
                "net_r": 0.0,
                "max_dd": 0.0,
                "r_per_year": {},
            }
        n = len(tr_list)
        gross_sum = float(sum(t["gross_r"] for t in tr_list))
        net_sum = float(sum(t["net_r"] for t in tr_list))

        # Yearly breakdown
        years = sorted(set(t["year"] for t in tr_list))
        yearly = {}
        for y in years:
            y_trs = [t for t in tr_list if t["year"] == y]
            yearly[str(y)] = {
                "n": len(y_trs),
                "gross_r": round(float(sum(t["gross_r"] for t in y_trs)), 4),
                "net_r": round(float(sum(t["net_r"] for t in y_trs)), 4),
            }

        # Max drawdown (peak to trough)
        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in tr_list:
            equity += t["net_r"]
            if equity > peak:
                peak = equity
            dd = peak - equity
            if dd > max_dd:
                max_dd = dd

        return {
            "n": n,
            "gross_r": round(gross_sum, 4),
            "net_r": round(net_sum, 4),
            "max_dd": round(max_dd, 4),
            "r_per_year": yearly,
        }

    return {
        "unconstrained": compute_stats(uncon_trades),
        "daily_limit_2r": compute_stats(d2_trades),
        "weekly_limit_5r": compute_stats(w5_trades),
        "peak_dd_killswitch_10r": compute_stats(dd10_trades),
    }


def compute_correlation_and_activity(
    judas_trades: list[dict[str, Any]],
    sb_trades: list[dict[str, Any]],
) -> dict[str, Any]:
    # Group by date
    all_dates = set(t["date"] for t in judas_trades) | set(t["date"] for t in sb_trades)
    sorted_dates = sorted(all_dates)
    
    judas_daily: dict[dt.date, float] = {}
    for t in judas_trades:
        judas_daily[t["date"]] = judas_daily.get(t["date"], 0.0) + t["net_r"]

    sb_daily: dict[dt.date, float] = {}
    for t in sb_trades:
        sb_daily[t["date"]] = sb_daily.get(t["date"], 0.0) + t["net_r"]

    j_vec = [judas_daily.get(d, 0.0) for d in sorted_dates]
    sb_vec = [sb_daily.get(d, 0.0) for d in sorted_dates]

    # Pearson correlation
    if len(sorted_dates) > 1 and np.std(j_vec) > 0 and np.std(sb_vec) > 0:
        corr = float(np.corrcoef(j_vec, sb_vec)[0, 1])
    else:
        corr = 0.0

    total_days = (sorted_dates[-1] - sorted_dates[0]).days + 1 if sorted_dates else 1
    # Trading days approximate (weekdays only)
    weekday_count = sum(1 for d in sorted_dates if d.weekday() < 5)
    trading_days = max(weekday_count, 1)

    total_trades = len(judas_trades) + len(sb_trades)
    trades_per_day = total_trades / trading_days

    return {
        "total_calendar_days": total_days,
        "trading_days": trading_days,
        "total_trades": total_trades,
        "trades_per_day": round(trades_per_day, 4),
        "daily_pnl_correlation": round(corr, 4),
    }


def main() -> None:
    print("=== STEP 3: PORTFOLIO SIMULATION ===")
    out_dir = Path("research/output")

    judas_csv = out_dir / "yearly_fade_trades_2020_2025.csv"
    if not judas_csv.is_file():
        # Fallback to judas_fade_trades_2020_2022
        judas_csv = out_dir / "judas_fade_trades_2020_2022.csv"
    print(f"Loading Judas trades from {judas_csv}...")
    judas_trades = load_judas_trades(str(judas_csv))
    print(f"Loaded {len(judas_trades)} Judas trades.")

    # Try loading multi-instrument SB or fallback to XAUUSD SB
    sb_limit_csv = out_dir / "sb_multi_instrument_limit_trades.csv"
    if not sb_limit_csv.is_file():
        sb_limit_csv = out_dir / "silver_bullet_trades_2020_2022.csv"
    print(f"Loading Silver Bullet trades from {sb_limit_csv}...")
    sb_trades = load_sb_trades(str(sb_limit_csv))
    print(f"Loaded {len(sb_trades)} Silver Bullet trades.")

    # Activity & correlation
    activity = compute_correlation_and_activity(judas_trades, sb_trades)
    print("\nPortfolio Activity & Correlation:")
    print(f"  Total Trades: {activity['total_trades']}")
    print(f"  Trading Days: {activity['trading_days']}")
    print(f"  Trades / Day: {activity['trades_per_day']:.4f}")
    print(f"  Daily Net PnL Correlation (Judas vs SB): {activity['daily_pnl_correlation']:+.4f}")

    # Combined trades
    combined = judas_trades + sb_trades
    sim_results = simulate_rules(combined)

    print("\nRisk Rules Comparison (Combined Judas + Silver Bullet):")
    for rule, res in sim_results.items():
        print(f"  {rule:<24}: N={res['n']:<5} | Gross R={res['gross_r']:+8.2f}R | Net R={res['net_r']:+8.2f}R | Max DD={res['max_dd']:6.2f}R")

    # Save to json
    report = {
        "activity": activity,
        "rules_comparison": sim_results,
    }
    summary_path = out_dir / "step3_portfolio_sim.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"\nSaved Step 3 summary to {summary_path}")


if __name__ == "__main__":
    main()
