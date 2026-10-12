"""Step 1 Pre-Registered Judas Variants Evaluation Harness.

Pre-registered variants (max 5, fixed definitions, no sweeps):
C1: Skip setups where round-trip cost > 10% of risk (stop distance < $2.00).
H1: Take only sweeps in the direction of daily bias (prior close vs 20-day EMA).
H2: Take only sweeps where the Asian extreme is within 0.5 ATR(14) of previous-day high/low.
H3: Skip the London window on days with high-impact EU/UK/US events (expanded calendar).
C1+H1: Composite filter (both C1 and H1 active).

Evaluates on:
- 2020-2022 (Validation / Development)
- 2023-2025 (Discovery / Confirmation)
- 2015-2019 (Extra Validation)
Under ONE unified cost model (spread 0.20 round-trip + 0.05 stop slippage).
"""

from __future__ import annotations

import csv
import datetime as dt
from decimal import Decimal
import json
from pathlib import Path
import sys
from typing import Any
from zoneinfo import ZoneInfo
import numpy as np

sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, "engine/src")
sys.path.insert(0, ".")

from engine.market.models import Bar
from research.data.loader import load_all_m5_bars
from research.backtest.engine import BacktestRunner, SimulatedTrade
from research.strategies.enhanced_judas import EnhancedJudasStrategy

NY_TZ = ZoneInfo("America/New_York")
out_dir = Path("research/output")
out_dir.mkdir(parents=True, exist_ok=True)


def compute_trade_r(
    raw_pnl: float,
    stop_dist: float,
    is_stop: bool,
    spread: float = 0.20,
    slippage: float = 0.05,
) -> tuple[float, float, float]:
    """Single unified cost function: returns (gross_r, net_r, cost_drag_r)."""
    s_dist = max(float(stop_dist), 0.10)
    gross_r = raw_pnl / s_dist
    cost_drag = (spread + (slippage if is_stop else 0.0)) / s_dist
    net_r = gross_r - cost_drag
    return gross_r, net_r, cost_drag


def compute_metrics(trades: list[dict[str, Any]], seed: int = 42) -> dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {
            "n": 0, "gross_exp": 0.0, "gross_wr": 0.0, "gross_pf": 0.0,
            "net_exp": 0.0, "net_wr": 0.0, "net_pf": 0.0,
            "ci_lower": 0.0, "ci_upper": 0.0, "cost_drag": 0.0,
        }

    gross_r = np.array([float(t["gross_r"]) for t in trades], dtype=np.float64)
    net_r = np.array([float(t["net_r"]) for t in trades], dtype=np.float64)
    drag_r = gross_r - net_r

    gross_wins = np.sum(gross_r > 0.0)
    gross_wr = float(gross_wins / n)
    gross_exp = float(np.mean(gross_r))

    net_wins = np.sum(net_r > 0.0)
    net_wr = float(net_wins / n)
    net_exp = float(np.mean(net_r))
    cost_drag = float(np.mean(drag_r))

    rng = np.random.default_rng(seed)
    indices = rng.integers(0, n, size=(10_000, n))
    means = np.mean(net_r[indices], axis=1)
    ci_lower = float(np.percentile(means, 2.5))
    ci_upper = float(np.percentile(means, 97.5))

    gp_g = float(np.sum(gross_r[gross_r > 0.0]))
    gl_g = float(abs(np.sum(gross_r[gross_r < 0.0])))
    pf_gross = (gp_g / gl_g) if gl_g > 0 else 999.0

    gp_n = float(np.sum(net_r[net_r > 0.0]))
    gl_n = float(abs(np.sum(net_r[net_r < 0.0])))
    pf_net = (gp_n / gl_n) if gl_n > 0 else 999.0

    return {
        "n": n,
        "gross_exp": gross_exp,
        "gross_wr": gross_wr,
        "gross_pf": pf_gross,
        "net_exp": net_exp,
        "net_wr": net_wr,
        "net_pf": pf_net,
        "ci_lower": ci_lower,
        "ci_upper": ci_upper,
        "cost_drag": cost_drag,
    }


def build_daily_features(bars: list[Bar]) -> tuple[dict[dt.date, float], dict[dt.date, float], dict[dt.date, tuple[float, float]]]:
    """Computes:
    1. 20-day EMA of daily closes (for H1 bias)
    2. 14-day ATR of daily bars (for H2 distance)
    3. Previous day High & Low (for H2 distance)
    """
    daily_highs: dict[dt.date, float] = {}
    daily_lows: dict[dt.date, float] = {}
    daily_closes: dict[dt.date, float] = {}

    for b in bars:
        d = b.timestamp_utc.astimezone(NY_TZ).date()
        hi = float(b.mid_ohlc.high)
        lo = float(b.mid_ohlc.low)
        cl = float(b.mid_ohlc.close)
        if d not in daily_highs:
            daily_highs[d] = hi
            daily_lows[d] = lo
            daily_closes[d] = cl
        else:
            daily_highs[d] = max(daily_highs[d], hi)
            daily_lows[d] = min(daily_lows[d], lo)
            daily_closes[d] = cl

    sorted_dates = sorted(daily_highs.keys())
    
    # TR and ATR14
    tr_series = {}
    for i, d in enumerate(sorted_dates):
        hi = daily_highs[d]
        lo = daily_lows[d]
        if i == 0:
            tr_series[d] = hi - lo
        else:
            prev_c = daily_closes[sorted_dates[i - 1]]
            tr_series[d] = max(hi - lo, abs(hi - prev_c), abs(lo - prev_c))

    atr14: dict[dt.date, float] = {}
    for i in range(len(sorted_dates)):
        d = sorted_dates[i]
        start_idx = max(0, i - 13)
        vals = [tr_series[sorted_dates[j]] for j in range(start_idx, i + 1)]
        atr14[d] = float(np.mean(vals))

    # 20-day EMA of daily closes
    ema20: dict[dt.date, float] = {}
    alpha = 2.0 / (20.0 + 1.0)
    current_ema = daily_closes[sorted_dates[0]]
    for d in sorted_dates:
        c = daily_closes[d]
        current_ema = alpha * c + (1.0 - alpha) * current_ema
        ema20[d] = current_ema

    # Previous day high/low
    prev_extremes: dict[dt.date, tuple[float, float]] = {}
    for i in range(1, len(sorted_dates)):
        curr_d = sorted_dates[i]
        prev_d = sorted_dates[i - 1]
        prev_extremes[curr_d] = (daily_highs[prev_d], daily_lows[prev_d])

    return ema20, atr14, prev_extremes


def is_high_impact_news_day(d: dt.date) -> bool:
    """Expanded calendar high-impact day check for H3:
    - First Friday of the month (NFP)
    - Second Tuesday/Wednesday of the month (US CPI)
    - FOMC rate decision Wednesdays (approx 8 times / year: late Jan, mid March, early May, mid June, late July, mid Sept, early Nov, mid Dec)
    - ECB / BOE rate decision Thursdays (first or second Thursday of month)
    """
    wd = d.weekday()
    day = d.day

    # NFP: First Friday of month (Friday with day <= 7)
    if wd == 4 and day <= 7:
        return True

    # CPI: Typically between day 10 and 15
    if wd in (1, 2) and 10 <= day <= 15:
        return True

    # FOMC Wednesdays: approx mid or late month Wednesdays
    if wd == 2 and (14 <= day <= 22 or day >= 26):
        return True

    # ECB/BOE Thursdays: typically between day 8 and 18
    if wd == 3 and 7 <= day <= 21:
        return True

    return False


def run_variant_eval(period_name: str, bars: list[Bar]) -> dict[str, Any]:
    print(f"\n==========================================================================")
    print(f"EVALUATING PRE-REGISTERED VARIANTS ON {period_name} ({len(bars)} BARS)")
    print(f"==========================================================================")

    ema20, atr14, prev_extremes = build_daily_features(bars)

    runner_0x = BacktestRunner(base_spread=Decimal("0.0"), base_slippage=Decimal("0.0"), cost_multiplier=Decimal("0.0"))
    strat = EnhancedJudasStrategy()
    raw_trades, _ = runner_0x.run_judas_backtest(bars, judas_strategy=strat)

    # Process all trades into unified accounting
    processed_all = []
    for t in raw_trades:
        raw_pnl = float(t.pnl_dollars)
        s_dist = float(t.risk_dollars)
        is_stop = "STOP" in t.exit_reason
        g_r, n_r, drag = compute_trade_r(raw_pnl, s_dist, is_stop)
        
        e_ny = t.entry_time.astimezone(NY_TZ)
        d = e_ny.date()
        
        # Features for filters
        # Prior close vs EMA20
        # If trade date d has prior date d_prev:
        prior_close = None
        ema_val = ema20.get(d)
        prev_h, prev_l = prev_extremes.get(d, (float(t.entry_price) + 10.0, float(t.entry_price) - 10.0))
        atr_val = atr14.get(d, 20.0)

        # C1 filter: round-trip cost > 10% of risk <=> stop_distance < 2.00
        pass_c1 = (s_dist >= 2.00)

        # H1 filter: Daily bias via 20-day EMA
        # Bullish (price > EMA) -> only take sweeps of Asian Low (BUY setups)
        # Bearish (price < EMA) -> only take sweeps of Asian High (SELL setups)
        bias_bullish = float(t.entry_price) >= ema_val if ema_val else True
        if bias_bullish:
            pass_h1 = (t.side == "BUY")
        else:
            pass_h1 = (t.side == "SELL")

        # H2 filter: Asian extreme within 0.5 ATR of prev day extreme
        # For SELL (swept Asian High): abs(sweep_extreme - prev_high) <= 0.5 * ATR
        # For BUY (swept Asian Low): abs(sweep_extreme - prev_low) <= 0.5 * ATR
        sweep_ext = float(t.stop_loss) # stop rests near sweep extreme
        if t.side == "SELL":
            dist_to_prev = abs(sweep_ext - prev_h)
        else:
            dist_to_prev = abs(sweep_ext - prev_l)
        pass_h2 = (dist_to_prev <= 0.5 * atr_val)

        # H3 filter: Skip high-impact event days
        pass_h3 = not is_high_impact_news_day(d)

        # Composite C1 + H1
        pass_c1_h1 = pass_c1 and pass_h1

        processed_all.append({
            "trade": t,
            "date": d,
            "side": t.side,
            "entry_time": t.entry_time.isoformat(),
            "gross_r": round(g_r, 4),
            "net_r": round(n_r, 4),
            "cost_drag_r": round(drag, 4),
            "pass_v0": True,
            "pass_c1": pass_c1,
            "pass_h1": pass_h1,
            "pass_h2": pass_h2,
            "pass_h3": pass_h3,
            "pass_c1_h1": pass_c1_h1,
        })

    variant_keys = [
        ("V0 Baseline", "pass_v0"),
        ("C1 (Cost <= 10% Risk)", "pass_c1"),
        ("H1 (Daily EMA Bias)", "pass_h1"),
        ("H2 (Near Prev Extreme)", "pass_h2"),
        ("H3 (Event Calendar Filter)", "pass_h3"),
        ("C1+H1 Composite", "pass_c1_h1"),
    ]

    results = {}
    print(f"{'Variant':<25} | {'N':<5} | {'Gross Exp':<10} | {'Gross WR':<9} | {'Net Exp':<10} | {'Net WR':<8} | {'Net 95% CI':<16} | {'Drag':<7} | {'Net PF':<6}")
    print("-" * 110)

    for v_name, flag in variant_keys:
        subset = [t for t in processed_all if t[flag]]
        m = compute_metrics(subset)
        results[v_name] = m
        ci_str = f"[{m['ci_lower']:+.2f}, {m['ci_upper']:+.2f}]"
        print(f"{v_name:<25} | {m['n']:<5} | {m['gross_exp']:>+9.4f}R | {m['gross_wr']*100:>7.2f}% | {m['net_exp']:>+9.4f}R | {m['net_wr']*100:>6.2f}% | {ci_str:<16} | {m['cost_drag']:>6.4f}R | {m['net_pf']:>6.2f}")

    return results


def main() -> None:
    print("Loading bar datasets for Judas variant testing...")
    bars_val = load_all_m5_bars(years=["2020", "2021", "2022"])
    
    # 2023-2025 confirmation dataset
    bars_disc_raw = load_all_m5_bars(years=["2023", "2024", "2025"])
    seen_23_25 = set()
    bars_disc = []
    for b in sorted(bars_disc_raw, key=lambda x: x.timestamp_utc):
        if b.timestamp_utc not in seen_23_25:
            seen_23_25.add(b.timestamp_utc)
            bars_disc.append(b)

    # 2015-2019 extra validation dataset
    bars_15_19 = load_all_m5_bars(years=["2015", "2016", "2017", "2018", "2019"])

    val_res = run_variant_eval("2020-2022 (Validation / Dev)", bars_val)
    disc_res = run_variant_eval("2023-2025 (Discovery / Confirmation)", bars_disc)
    extra_res = run_variant_eval("2015-2019 (Extra Validation)", bars_15_19)

    summary = {
        "validation_2020_2022": val_res,
        "confirmation_2023_2025": disc_res,
        "extra_validation_2015_2019": extra_res,
    }
    with open(out_dir / "step1_judas_variants_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print("\n" + "=" * 100)
    print("STEP 1 PRE-REGISTERED JUDAS VARIANTS COMPLETE")
    print("=" * 100)


if __name__ == "__main__":
    main()
