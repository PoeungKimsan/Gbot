"""Follow-up Deep Dive Analysis script.

Covers all 6 requirements:
1. Reconcile Step 0 (<0.10R) with C1 (post-filtered vs re-simulated).
2. Weekday test by period (2015-19, 2020-22, 2023-25) with mean, median, 5% trimmed mean, and $ per trade.
3. List all trades with |R| > 10 with stop distance in $, and top 20 trades R contribution.
4. Rebuilt portfolio sim (XAUUSD-only Judas live core + XAUUSD-only Silver Bullet; unconstrained, -2R/day, -5R/week).
5. Silver Bullet per instrument breakdown (N, date range, spread cost in R, gross, net).
6. Pooled V0 over 2015-2025 for live core class (N, gross, net, 95% bootstrap CI).
"""

from __future__ import annotations

import csv
import datetime as dt
from decimal import Decimal
import json
from pathlib import Path
import pickle
import sys
from typing import Any
from zoneinfo import ZoneInfo
import numpy as np

sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, "engine/src")
sys.path.insert(0, ".")

from engine.market.models import Bar
from engine.strategy.judas_swing import JudasSwingStrategy
from research.backtest.engine import BacktestRunner, SimulatedTrade
from research.data.loader import load_all_m5_bars
from research.evaluation.step1_judas_variants import compute_trade_r

NY_TZ = ZoneInfo("America/New_York")
out_dir = Path("research/output")
out_dir.mkdir(parents=True, exist_ok=True)


def trimmed_mean(arr: np.ndarray, proportiontocut: float = 0.05) -> float:
    if len(arr) == 0:
        return 0.0
    n = len(arr)
    k = int(n * proportiontocut)
    if k == 0 or 2 * k >= n:
        return float(np.mean(arr))
    sorted_arr = np.sort(arr)
    return float(np.mean(sorted_arr[k : n - k]))


def load_xauusd_bars_by_period():
    print("Loading bar data for all periods (2015-2025)...")
    with open("research/data/cache/m5_bars_2015_2016_2017_2018_2019.pkl", "rb") as f:
        bars_15_19 = pickle.load(f)
    with open("research/data/cache/m5_bars_2020_2021_2022.pkl", "rb") as f:
        bars_20_22 = pickle.load(f)
    with open("research/data/cache/m5_bars_2023_2024_2025.pkl", "rb") as f:
        bars_23_25_raw = pickle.load(f)

    seen = set()
    bars_23_25 = []
    for b in sorted(bars_23_25_raw, key=lambda x: x.timestamp_utc):
        if b.timestamp_utc not in seen:
            seen.add(b.timestamp_utc)
            bars_23_25.append(b)

    return bars_15_19, bars_20_22, bars_23_25


def run_judas_core_backtest(bars: list[Bar], min_stop_dist: float = 0.0) -> list[SimulatedTrade]:
    strat = JudasSwingStrategy()
    runner = BacktestRunner(base_spread=Decimal("0.0"), base_slippage=Decimal("0.0"), cost_multiplier=Decimal("0.0"))
    trades = []
    active_t: SimulatedTrade | None = None

    for idx, b in enumerate(bars):
        mid = b.mid_ohlc
        b_time = b.timestamp_utc
        ny_time = b_time.astimezone(NY_TZ)

        # 1. Manage active trade
        if active_t is not None:
            stop_hit = mid.low <= active_t.stop_loss if active_t.side == "BUY" else mid.high >= active_t.stop_loss
            tp_hit = mid.high >= active_t.take_profit if active_t.side == "BUY" else mid.low <= active_t.take_profit
            dynamic_close = ny_time.time() >= dt.time(16, 45)

            if stop_hit or tp_hit or dynamic_close:
                if stop_hit and tp_hit:
                    active_t.exit_price = active_t.stop_loss
                    active_t.exit_time = b_time
                    active_t.exit_reason = "STOP_LOSS (conflict)"
                    active_t.pnl_dollars = (active_t.exit_price - active_t.entry_price) if active_t.side == "BUY" else (active_t.entry_price - active_t.exit_price)
                elif stop_hit:
                    active_t.exit_price = active_t.stop_loss
                    active_t.exit_time = b_time
                    active_t.exit_reason = "STOP_LOSS"
                    active_t.pnl_dollars = (active_t.exit_price - active_t.entry_price) if active_t.side == "BUY" else (active_t.entry_price - active_t.exit_price)
                elif tp_hit:
                    active_t.exit_price = active_t.take_profit
                    active_t.exit_time = b_time
                    active_t.exit_reason = "TAKE_PROFIT"
                    active_t.pnl_dollars = (active_t.exit_price - active_t.entry_price) if active_t.side == "BUY" else (active_t.entry_price - active_t.exit_price)
                else:
                    active_t.exit_price = mid.close
                    active_t.exit_time = b_time
                    active_t.exit_reason = "DYNAMIC_CLOSE_1645"
                    active_t.pnl_dollars = (active_t.exit_price - active_t.entry_price) if active_t.side == "BUY" else (active_t.entry_price - active_t.exit_price)

                trades.append(active_t)
                active_t = None

        # 2. Strategy evaluation
        setup = strat.on_bar(b)
        if setup is not None and active_t is None and ny_time.time() < dt.time(16, 45):
            s_dist = abs(float(setup.entry_price) - float(setup.stop_loss))
            if s_dist >= min_stop_dist:
                active_t = SimulatedTrade(
                    trade_id=f"judas-{len(trades)+1:04d}",
                    strategy="Judas",
                    side=setup.side.value,
                    entry_time=b_time,
                    entry_price=setup.entry_price,
                    stop_loss=setup.stop_loss,
                    take_profit=setup.take_profit,
                    risk_dollars=Decimal(str(s_dist)),
                    reward_dollars=abs(setup.take_profit - setup.entry_price),
                )

    return trades


def process_trade_dicts(trades: list[SimulatedTrade]) -> list[dict[str, Any]]:
    res = []
    for t in trades:
        raw_pnl = float(t.pnl_dollars)
        s_dist = float(t.risk_dollars)
        is_stop = "STOP" in t.exit_reason
        gross_r, net_r, cost_drag = compute_trade_r(raw_pnl, s_dist, is_stop)
        
        # Dollars per trade under standard lot (100 oz, $100 per point)
        dollar_pnl = raw_pnl * 100.0 - (20.0 + (5.0 if is_stop else 0.0))
        
        e_ny = t.entry_time.astimezone(NY_TZ)
        res.append({
            "trade_id": t.trade_id,
            "entry_time": t.entry_time.isoformat(),
            "date": e_ny.date().isoformat(),
            "weekday": e_ny.strftime("%A"),
            "entry_price": float(t.entry_price),
            "stop_loss": float(t.stop_loss),
            "take_profit": float(t.take_profit),
            "exit_time": t.exit_time.isoformat() if t.exit_time else "",
            "exit_price": float(t.exit_price) if t.exit_price else 0.0,
            "exit_reason": t.exit_reason,
            "stop_distance": s_dist,
            "raw_pnl": raw_pnl,
            "dollar_pnl": dollar_pnl,
            "gross_r": gross_r,
            "cost_drag_r": cost_drag,
            "net_r": net_r,
            "is_win": gross_r > 0,
        })
    return res


def main() -> None:
    print("=== FOLLOW-UP DEEP DIVE INVESTIGATION ===")
    bars_15_19, bars_20_22, bars_23_25 = load_xauusd_bars_by_period()
    all_bars_15_25 = bars_15_19 + bars_20_22 + bars_23_25
    bars_20_25 = bars_20_22 + bars_23_25

    # =========================================================================
    # ITEM 1: RECONCILIATION OF STEP 0 (<0.10R) WITH C1
    # =========================================================================
    print("\n" + "=" * 90)
    print("ITEM 1: RECONCILING STEP 0 (<0.10R: N=386, +0.013R) WITH C1 (N~460, -0.17R)")
    print("=" * 90)
    
    # 1. Base V0 trades on 2020-2025
    v0_trades_20_25_raw = run_judas_core_backtest(bars_20_25, min_stop_dist=0.0)
    v0_trades_20_25 = process_trade_dicts(v0_trades_20_25_raw)
    print(f"Total V0 baseline trades (2020-2025): {len(v0_trades_20_25)}")

    # Step 0 cost bucket (<0.10R)
    s0_trades = [t for t in v0_trades_20_25 if t["cost_drag_r"] < 0.10]
    s0_n = len(s0_trades)
    s0_net = float(np.mean([t["net_r"] for t in s0_trades])) if s0_n else 0.0
    print(f"  Step 0 (<0.10R bucket): N={s0_n}, Net Exp={s0_net:+.4f}R")

    # C1 Post-Filter version (stop >= 2.00)
    c1_post_trades = [t for t in v0_trades_20_25 if t["stop_distance"] >= 2.00]
    c1_post_n = len(c1_post_trades)
    c1_post_net = float(np.mean([t["net_r"] for t in c1_post_trades])) if c1_post_n else 0.0
    print(f"  C1 Post-Filter version : N={c1_post_n}, Net Exp={c1_post_net:+.4f}R")

    # C1 Re-Simulated version (skips setups with stop < 2.00 in backtest loop)
    c1_resim_raw = run_judas_core_backtest(bars_20_25, min_stop_dist=2.00)
    c1_resim_trades = process_trade_dicts(c1_resim_raw)
    c1_resim_n = len(c1_resim_trades)
    c1_resim_net = float(np.mean([t["net_r"] for t in c1_resim_trades])) if c1_resim_n else 0.0
    print(f"  C1 Re-Simulated version: N={c1_resim_n}, Net Exp={c1_resim_net:+.4f}R")

    # Decomposition of the difference
    in_between = [t for t in v0_trades_20_25 if 2.00 <= t["stop_distance"] < 2.50]
    ib_wins = sum(1 for t in in_between if t["is_win"])
    ib_losses = sum(1 for t in in_between if not t["is_win"])
    print(f"\n  Decomposition of stop distance in [2.00, 2.50) (N={len(in_between)}):")
    print(f"    Wins = {ib_wins} (drag = $0.20/stop < 0.10R -> INCLUDED in Step 0)")
    print(f"    Losses = {ib_losses} (drag = $0.25/stop >= 0.10R -> EXCLUDED from Step 0)")
    print("  Mechanism: Step 0 evaluated drag ex-post (after knowing if trade stopped out).")
    print("  Winners were charged $0.20 drag while losers were charged $0.25 drag, creating an")
    print("  ex-post survivorship selection bias in Step 0 that falsely inflated its net expectancy.")

    reconcile_item1 = {
        "step0_cost_lt_010r": {"n": s0_n, "net_exp": round(s0_net, 4)},
        "c1_post_filtered": {"n": c1_post_n, "net_exp": round(c1_post_net, 4)},
        "c1_resimulated": {"n": c1_resim_n, "net_exp": round(c1_resim_net, 4)},
        "in_between_stop_200_250": {"n": len(in_between), "wins": ib_wins, "losses": ib_losses},
    }

    # =========================================================================
    # ITEM 2: WEEKDAY TEST ACROSS 3 PERIODS
    # =========================================================================
    print("\n" + "=" * 90)
    print("ITEM 2: WEEKDAY TEST ACROSS PERIODS (2015-19, 2020-22, 2023-25)")
    print("=" * 90)

    v0_15_19 = process_trade_dicts(run_judas_core_backtest(bars_15_19))
    v0_20_22 = process_trade_dicts(run_judas_core_backtest(bars_20_22))
    v0_23_25 = process_trade_dicts(run_judas_core_backtest(bars_23_25))

    periods = [
        ("2015-2019 (Extra Validation)", v0_15_19),
        ("2020-2022 (Development)", v0_20_22),
        ("2023-2025 (Confirmation)", v0_23_25),
    ]

    weekday_table = {}
    print(f"{'Period':<28} | {'Day':<9} | {'N':<4} | {'Mean Net R':<10} | {'Median Net R':<12} | {'5% Trim Mean':<12} | {'Avg $ PnL':<10}")
    print("-" * 95)

    weekdays_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    for p_name, tr_list in periods:
        weekday_table[p_name] = {}
        for wd in weekdays_order:
            sub = [t for t in tr_list if t["weekday"] == wd]
            n = len(sub)
            if n > 0:
                net_r_vals = np.array([t["net_r"] for t in sub])
                mean_r = float(np.mean(net_r_vals))
                med_r = float(np.median(net_r_vals))
                trim_r = trimmed_mean(net_r_vals, proportiontocut=0.05)
                avg_dollar = float(np.mean([t["dollar_pnl"] for t in sub]))
            else:
                mean_r, med_r, trim_r, avg_dollar = 0.0, 0.0, 0.0, 0.0

            weekday_table[p_name][wd] = {
                "n": n,
                "mean_net_r": round(mean_r, 4),
                "median_net_r": round(med_r, 4),
                "trimmed_5pct_net_r": round(trim_r, 4),
                "avg_dollar_pnl": round(avg_dollar, 2),
            }
            print(f"{p_name:<28} | {wd:<9} | {n:<4} | {mean_r:>+9.4f}R | {med_r:>+11.4f}R | {trim_r:>+11.4f}R | ${avg_dollar:>+8.2f}")

    # =========================================================================
    # ITEM 3: OUTLIER TRADES (|R| > 10) AND TOP 20 R CONTRIBUTION
    # =========================================================================
    print("\n" + "=" * 90)
    print("ITEM 3: OUTLIER TRADES (|R| > 10) AND TOP 20 CONTRIBUTION")
    print("=" * 90)

    all_v0_trades = v0_15_19 + v0_20_22 + v0_23_25
    outliers = [t for t in all_v0_trades if abs(t["net_r"]) > 10.0 or abs(t["gross_r"]) > 10.0]
    print(f"Total trades with |R| > 10 across 2015-2025: {len(outliers)}")
    for t in outliers:
        print(f"  Trade {t['trade_id']} | Entry={t['entry_time'][:16]} | Side={t.get('side','?')} | Stop=${t['stop_distance']:.2f} | Gross={t['gross_r']:+.2f}R | Net={t['net_r']:+.2f}R | Exit={t['exit_reason']}")

    # Top 20 trades analysis
    sorted_by_net_r = sorted(all_v0_trades, key=lambda x: x["net_r"], reverse=True)
    top_20 = sorted_by_net_r[:20]
    sum_top_20_r = sum(t["net_r"] for t in top_20)
    total_pos_r = sum(t["net_r"] for t in all_v0_trades if t["net_r"] > 0)
    total_net_r = sum(t["net_r"] for t in all_v0_trades)

    pct_of_pos = (sum_top_20_r / total_pos_r * 100) if total_pos_r > 0 else 0.0
    print(f"\nTop 20 Trades Net R Contribution:")
    print(f"  Sum Net R of Top 20 Trades: {sum_top_20_r:+.2f}R")
    print(f"  Total Positive Net R Across All Trades: {total_pos_r:+.2f}R ({pct_of_pos:.1f}% comes from Top 20)")
    print(f"  Total Net R of Entire Strategy: {total_net_r:+.2f}R")

    # =========================================================================
    # ITEM 4: REBUILT PORTFOLIO SIM (XAUUSD-ONLY JUDAS LIVE CORE + XAUUSD SILVER BULLET)
    # =========================================================================
    print("\n" + "=" * 90)
    print("ITEM 4: REBUILT PORTFOLIO SIM (XAUUSD-ONLY JUDAS CORE + XAUUSD SILVER BULLET)")
    print("=" * 90)

    # Judas live core trades 2020-2025
    judas_portfolio_trades = v0_trades_20_25

    # XAUUSD Silver Bullet trades from 2020-2022 and 2023-2025
    sb_trades_raw = []
    for csv_file in ["silver_bullet_trades_2020_2022.csv", "silver_bullet_trades_2023_2025.csv"]:
        p = out_dir / csv_file
        if p.is_file():
            with open(p, "r", encoding="utf-8") as f:
                r = csv.DictReader(f)
                for row in r:
                    sb_trades_raw.append(row)

    sb_portfolio_trades = []
    for t in sb_trades_raw:
        e_time = t.get("entry_time", "")
        e_dt = dt.datetime.fromisoformat(e_time)
        e_ny = e_dt.astimezone(NY_TZ)
        g_r = float(t.get("gross_r", t.get("r_multiple", 0.0)))
        n_r = float(t.get("net_r", g_r))
        sb_portfolio_trades.append({
            "trade_id": t.get("trade_id", ""),
            "entry_time": e_time,
            "date": e_ny.date(),
            "year": e_ny.year,
            "gross_r": g_r,
            "net_r": n_r,
            "dollar_pnl": float(t.get("pnl_dollars", 0.0)) * 100.0,
        })

    print(f"Loaded {len(judas_portfolio_trades)} XAUUSD Judas trades and {len(sb_portfolio_trades)} XAUUSD Silver Bullet trades.")

    # Combine chronologically
    combined_pf = []
    for t in judas_portfolio_trades:
        e_dt = dt.datetime.fromisoformat(t["entry_time"])
        combined_pf.append({
            "strategy": "Judas",
            "entry_time": t["entry_time"],
            "date": e_dt.astimezone(NY_TZ).date(),
            "year": e_dt.astimezone(NY_TZ).year,
            "gross_r": t["gross_r"],
            "net_r": t["net_r"],
        })
    for t in sb_portfolio_trades:
        combined_pf.append({
            "strategy": "SilverBullet",
            "entry_time": t["entry_time"],
            "date": t["date"],
            "year": t["year"],
            "gross_r": t["gross_r"],
            "net_r": t["net_r"],
        })
    combined_pf = sorted(combined_pf, key=lambda x: x["entry_time"])

    # Simulate 3 rules: unconstrained, -2R/day, -5R/week
    def sim_pf_rule(rule_name: str, tr_list: list[dict[str, Any]]) -> dict[str, Any]:
        daily_cum: dict[dt.date, float] = {}
        weekly_cum: dict[tuple[int, int], float] = {}
        filtered = []

        for t in tr_list:
            d = t["date"]
            wk = (d.year, d.isocalendar()[1])
            if rule_name == "daily_limit_2r":
                if daily_cum.get(d, 0.0) <= -2.0:
                    continue
            elif rule_name == "weekly_limit_5r":
                if weekly_cum.get(wk, 0.0) <= -5.0:
                    continue
            filtered.append(t)
            daily_cum[d] = daily_cum.get(d, 0.0) + t["net_r"]
            weekly_cum[wk] = weekly_cum.get(wk, 0.0) + t["net_r"]

        n = len(filtered)
        gross_r = float(sum(t["gross_r"] for t in filtered))
        net_r = float(sum(t["net_r"] for t in filtered))

        # Yearly breakdown
        years = sorted(set(t["year"] for t in filtered))
        r_per_year = {}
        for y in years:
            y_trs = [t for t in filtered if t["year"] == y]
            r_per_year[str(y)] = {
                "n": len(y_trs),
                "gross_r": round(float(sum(t["gross_r"] for t in y_trs)), 4),
                "net_r": round(float(sum(t["net_r"] for t in y_trs)), 4),
            }

        # Max drawdown
        eq = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in filtered:
            eq += t["net_r"]
            if eq > peak:
                peak = eq
            dd = peak - eq
            if dd > max_dd:
                max_dd = dd

        return {
            "n": n,
            "gross_r": round(gross_r, 4),
            "net_r": round(net_r, 4),
            "max_dd": round(max_dd, 4),
            "r_per_year": r_per_year,
        }

    rules_results = {
        "unconstrained": sim_pf_rule("unconstrained", combined_pf),
        "daily_limit_2r": sim_pf_rule("daily_limit_2r", combined_pf),
        "weekly_limit_5r": sim_pf_rule("weekly_limit_5r", combined_pf),
    }

    # Correlation and trades per day
    all_dates = sorted(set(t["date"] for t in combined_pf))
    j_daily: dict[dt.date, float] = {}
    for t in judas_portfolio_trades:
        d = dt.datetime.fromisoformat(t["entry_time"]).astimezone(NY_TZ).date()
        j_daily[d] = j_daily.get(d, 0.0) + t["net_r"]
    sb_daily: dict[dt.date, float] = {}
    for t in sb_portfolio_trades:
        sb_daily[t["date"]] = sb_daily.get(t["date"], 0.0) + t["net_r"]

    j_vec = [j_daily.get(d, 0.0) for d in all_dates]
    sb_vec = [sb_daily.get(d, 0.0) for d in all_dates]
    corr = float(np.corrcoef(j_vec, sb_vec)[0, 1]) if len(all_dates) > 1 else 0.0

    trading_days = sum(1 for d in all_dates if d.weekday() < 5)
    trades_per_day = len(combined_pf) / max(trading_days, 1)

    print(f"\nXAUUSD Portfolio Activity:")
    print(f"  Total Trades: {len(combined_pf)} | Trading Days: {trading_days} | Trades/Day: {trades_per_day:.4f}")
    print(f"  Daily Net PnL Correlation: {corr:+.4f}")
    print("\nRisk Rules Comparison (Peak DD Halt Dropped):")
    for r_name, r_stats in rules_results.items():
        print(f"  {r_name:<16}: N={r_stats['n']:<5} | Gross R={r_stats['gross_r']:+8.2f}R | Net R={r_stats['net_r']:+8.2f}R | Max DD={r_stats['max_dd']:6.2f}R")

    # =========================================================================
    # ITEM 5: SILVER BULLET PER INSTRUMENT TABLE
    # =========================================================================
    print("\n" + "=" * 90)
    print("ITEM 5: SILVER BULLET PER INSTRUMENT (LIMIT ENTRY)")
    print("=" * 90)

    with open(out_dir / "step2_multi_instrument_sb.json", "r", encoding="utf-8") as f:
        sb_s2 = json.load(f)

    # Load trades from CSV to extract exact date ranges
    sb_lim_csv = out_dir / "sb_multi_instrument_limit_trades.csv"
    sb_lim_all = []
    with open(sb_lim_csv, "r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            sb_lim_all.append(row)

    sb_per_inst = {}
    print(f"{'Instrument':<10} | {'N':<4} | {'Date Range':<23} | {'Spread Cost (R)':<16} | {'Gross Exp':<10} | {'Net Exp':<10}")
    print("-" * 85)

    for sym in ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "ES"]:
        sub = [t for t in sb_lim_all if t["symbol"] == sym]
        n = len(sub)
        if n > 0:
            dates = [t["date"] for t in sub]
            min_d, max_d = min(dates), max(dates)
            d_range = f"{min_d} to {max_d}"
            gross = float(np.mean([float(t["gross_r"]) for t in sub]))
            net = float(np.mean([float(t["net_r"]) for t in sub]))
            drag = float(np.mean([float(t["cost_drag_r"]) for t in sub]))
        else:
            d_range, gross, net, drag = "N/A", 0.0, 0.0, 0.0

        sb_per_inst[sym] = {
            "n": n,
            "date_range": d_range,
            "cost_drag_r": round(drag, 4),
            "gross_exp": round(gross, 4),
            "net_exp": round(net, 4),
        }
        print(f"{sym:<10} | {n:<4} | {d_range:<23} | {drag:>14.4f}R | {gross:>+9.4f}R | {net:>+9.4f}R")

    # =========================================================================
    # ITEM 6: POOLED V0 OVER 2015-2025 FOR LIVE CORE CLASS
    # =========================================================================
    print("\n" + "=" * 90)
    print("ITEM 6: POOLED V0 OVER 2015-2025 FOR LIVE CORE CLASS")
    print("=" * 90)

    v0_full_raw = run_judas_core_backtest(all_bars_15_25, min_stop_dist=0.0)
    v0_full = process_trade_dicts(v0_full_raw)
    n_full = len(v0_full)
    gross_full = float(np.mean([t["gross_r"] for t in v0_full]))
    net_full = float(np.mean([t["net_r"] for t in v0_full]))
    
    # 10,000 bootstrap CI
    rng = np.random.default_rng(42)
    net_arr = np.array([t["net_r"] for t in v0_full])
    boot_means = np.mean(rng.choice(net_arr, size=(10000, n_full), replace=True), axis=1)
    ci_lower = float(np.percentile(boot_means, 2.5))
    ci_upper = float(np.percentile(boot_means, 97.5))

    print(f"POOLED 2015-2025 (Judas Live Core):")
    print(f"  N              : {n_full}")
    print(f"  Gross Exp      : {gross_full:+.4f}R")
    print(f"  Net Exp        : {net_full:+.4f}R")
    print(f"  95% Bootstrap CI: [{ci_lower:+.4f}, {ci_upper:+.4f}]")

    pooled_v0_result = {
        "n": n_full,
        "gross_exp": round(gross_full, 4),
        "net_exp": round(net_full, 4),
        "bootstrap_ci_95": [round(ci_lower, 4), round(ci_upper, 4)],
    }

    # Save comprehensive report to JSON
    summary_out = {
        "item1_reconciliation": reconcile_item1,
        "item2_weekday_test": weekday_table,
        "item3_outliers_top20": {
            "outliers_gt_10r": outliers,
            "top_20_sum_net_r": round(sum_top_20_r, 4),
            "total_positive_net_r": round(total_pos_r, 4),
            "pct_of_positive_r": round(pct_of_pos, 2),
            "total_net_r": round(total_net_r, 4),
        },
        "item4_portfolio_sim": {
            "activity": {
                "total_trades": len(combined_pf),
                "trading_days": trading_days,
                "trades_per_day": round(trades_per_day, 4),
                "daily_corr": round(corr, 4),
            },
            "rules": rules_results,
        },
        "item5_silver_bullet_instruments": sb_per_inst,
        "item6_pooled_v0_2015_2025": pooled_v0_result,
    }

    out_file = out_dir / "followup_deepdive_summary.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(summary_out, f, indent=2)
    print(f"\nSaved complete deepdive summary to {out_file}")


if __name__ == "__main__":
    main()
