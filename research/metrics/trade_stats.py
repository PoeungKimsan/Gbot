"""Comprehensive trade performance, bootstrap inference, and cost metrics.

Calculates:
- N trades, win rate, avg R, median R, expectancy
- 95% bootstrap confidence interval (10,000 resamples)
- Student t-statistic and two-tailed p-value
- Profit factor
- Max drawdown (in R and % at 1% risk)
- Longest losing streak
- Daily R distribution & -3R daily stop triggers
- Cost in R (spread, slippage, stop distance)
- Breakdowns by weekday, direction, news filter
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Any
from zoneinfo import ZoneInfo
import numpy as np

NY_TZ = ZoneInfo("America/New_York")


def compute_bootstrap_ci(
    r_multiples: list[float],
    n_resamples: int = 10_000,
    seed: int = 42,
) -> tuple[float | None, float | None]:
    """Calculate 95% bootstrap confidence interval for mean R."""
    if not r_multiples:
        return None, None
    if len(r_multiples) == 1:
        return float(r_multiples[0]), float(r_multiples[0])

    rng = np.random.default_rng(seed)
    arr = np.array(r_multiples, dtype=np.float64)
    n = len(arr)
    # Generate bootstrap sample means
    sample_indices = rng.integers(0, n, size=(n_resamples, n))
    bootstrap_means = np.mean(arr[sample_indices], axis=1)
    ci_lower = float(np.percentile(bootstrap_means, 2.5))
    ci_upper = float(np.percentile(bootstrap_means, 97.5))
    return ci_lower, ci_upper


def compute_t_stat(r_multiples: list[float]) -> tuple[float | None, float | None]:
    """Calculate Student's t-statistic and approximate two-tailed p-value against H0: mu=0."""
    n = len(r_multiples)
    if n < 2:
        return None, None

    arr = np.array(r_multiples, dtype=np.float64)
    mean_val = float(np.mean(arr))
    std_val = float(np.std(arr, ddof=1))
    if std_val == 0.0:
        return None, None

    se = std_val / math.sqrt(n)
    t_stat = mean_val / se

    # Normal approximation / complementary error function for p-value
    # For large n or standard t distribution
    df = n - 1
    # Regularized incomplete beta or standard normal approximation
    z = abs(t_stat)
    p_value = 2.0 * (1.0 - 0.5 * (1.0 + math.erf(z / math.sqrt(2.0))))
    return t_stat, p_value


def compute_drawdowns(r_multiples: list[float], risk_pct: float = 0.01) -> tuple[float, float]:
    """Calculate Max Drawdown in R and in % (compounding at 1% risk per trade)."""
    if not r_multiples:
        return 0.0, 0.0

    # Max Drawdown in R
    cum_r = 0.0
    peak_r = 0.0
    max_dd_r = 0.0
    for r in r_multiples:
        cum_r += r
        if cum_r > peak_r:
            peak_r = cum_r
        dd = peak_r - cum_r
        if dd > max_dd_r:
            max_dd_r = dd

    # Max Drawdown in %
    equity = 1.0
    peak_equity = 1.0
    max_dd_pct = 0.0
    for r in r_multiples:
        equity *= 1.0 + (risk_pct * r)
        if equity > peak_equity:
            peak_equity = equity
        dd_pct = (peak_equity - equity) / peak_equity
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct

    return round(max_dd_r, 4), round(max_dd_pct * 100.0, 4)


def compute_losing_streak(r_multiples: list[float]) -> int:
    """Calculate the longest consecutive losing streak (R <= 0)."""
    longest = 0
    current = 0
    for r in r_multiples:
        if r <= 0.0:
            current += 1
            if current > longest:
                longest = current
        else:
            current = 0
    return longest


def compute_trade_metrics(
    trades: list[dict[str, Any]],
    bootstrap_resamples: int = 10_000,
    seed: int = 42,
) -> dict[str, Any]:
    """Compute comprehensive statistical and risk metrics from a list of trades."""
    if not trades:
        return {
            "n_trades": 0,
            "win_rate": 0.0,
            "avg_r": 0.0,
            "median_r": 0.0,
            "expectancy": 0.0,
            "ci_95_lower": None,
            "ci_95_upper": None,
            "t_stat": None,
            "p_value": None,
            "profit_factor": 0.0,
            "max_drawdown_r": 0.0,
            "max_drawdown_pct": 0.0,
            "longest_losing_streak": 0,
            "daily_stats": {
                "days_count": 0,
                "mean_daily_r": 0.0,
                "std_daily_r": 0.0,
                "days_tripped_3r": 0,
                "max_trades_per_day": 0,
            },
            "costs": {
                "avg_spread_entry": 0.0,
                "max_spread_entry": 0.0,
                "avg_slippage": 0.0,
                "avg_stop_distance_dollars": 0.0,
                "total_cost_r": 0.0,
            },
            "unfilled_rate": 0.0,
            "breakdown_weekday": {},
            "breakdown_direction": {},
            "breakdown_news_filter": {},
        }

    r_vals = [float(t["r_multiple"]) for t in trades if t.get("r_multiple") is not None]
    n = len(r_vals)
    if n == 0:
        return compute_trade_metrics([])

    wins = [r for r in r_vals if r > 0.0]
    losses = [r for r in r_vals if r < 0.0]

    win_rate = len(wins) / n
    avg_r = float(np.mean(r_vals))
    median_r = float(np.median(r_vals))

    ci_lower, ci_upper = compute_bootstrap_ci(r_vals, n_resamples=bootstrap_resamples, seed=seed)
    t_stat, p_val = compute_t_stat(r_vals)

    gross_profit = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    if gross_loss > 0.0:
        profit_factor = round(gross_profit / gross_loss, 4)
    else:
        profit_factor = 999.0 if gross_profit > 0 else 0.0

    max_dd_r, max_dd_pct = compute_drawdowns(r_vals)
    longest_losing_streak = compute_losing_streak(r_vals)

    # Group trades by trading day (NY date)
    daily_groups: dict[dt.date, list[float]] = {}
    for t in trades:
        ts_str = t.get("occurred_at") or t.get("time") or t.get("exit_time")
        if ts_str:
            try:
                dt_obj = dt.datetime.fromisoformat(str(ts_str))
                if dt_obj.tzinfo is None:
                    dt_obj = dt_obj.replace(tzinfo=dt.UTC)
                ny_date = dt_obj.astimezone(NY_TZ).date()
            except Exception:
                ny_date = dt.date(2024, 1, 1)
        else:
            ny_date = dt.date(2024, 1, 1)

        daily_groups.setdefault(ny_date, []).append(float(t.get("r_multiple", 0.0)))

    daily_totals = [sum(rs) for rs in daily_groups.values()]
    daily_counts = [len(rs) for rs in daily_groups.values()]

    days_tripped_3r = sum(1 for total in daily_totals if total <= -3.0)
    max_trades_per_day = max(daily_counts) if daily_counts else 0

    # Costs & Execution statistics
    spreads = [float(t["spread"]) for t in trades if t.get("spread") is not None]
    slippages = [float(t["slippage"]) for t in trades if t.get("slippage") is not None]
    stop_distances = [
        abs(float(t["entry_price"]) - float(t["stop_loss"]))
        for t in trades
        if t.get("entry_price") is not None and t.get("stop_loss") is not None
    ]

    avg_spread = float(np.mean(spreads)) if spreads else 0.0
    max_spread = float(np.max(spreads)) if spreads else 0.0
    avg_slippage = float(np.mean(slippages)) if slippages else 0.0
    avg_stop_dist = float(np.mean(stop_distances)) if stop_distances else 0.0

    # Total cost expressed in R
    cost_in_r = 0.0
    if stop_distances and avg_stop_dist > 0.0:
        # Each trade's spread + slippage divided by its stop distance
        trade_costs_r = []
        for t in trades:
            s = float(t.get("spread", 0.0))
            slip = float(t.get("slippage", 0.0))
            entry = float(t.get("entry_price", 0.0))
            stop = float(t.get("stop_loss", 0.0))
            dist = abs(entry - stop)
            if dist > 0.0:
                trade_costs_r.append((s + slip) / dist)
        cost_in_r = float(sum(trade_costs_r)) if trade_costs_r else 0.0

    # Breakdown by Weekday
    breakdown_weekday: dict[str, dict[str, Any]] = {}
    weekday_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    for day_name in weekday_names:
        day_trades = [
            t for t in trades
            if _get_weekday_name(t.get("occurred_at") or t.get("time")) == day_name
        ]
        if day_trades:
            sub_r = [float(t["r_multiple"]) for t in day_trades]
            breakdown_weekday[day_name] = {
                "n": len(sub_r),
                "win_rate": round(sum(1 for r in sub_r if r > 0) / len(sub_r), 4),
                "avg_r": round(float(np.mean(sub_r)), 4),
            }

    # Breakdown by Direction
    breakdown_direction: dict[str, dict[str, Any]] = {}
    for d in ["BUY", "SELL", "LONG", "SHORT"]:
        dir_trades = [t for t in trades if t.get("side", "").upper() == d]
        if dir_trades:
            sub_r = [float(t["r_multiple"]) for t in dir_trades]
            canonical_dir = "LONG" if d in ("BUY", "LONG") else "SHORT"
            breakdown_direction[canonical_dir] = {
                "n": len(sub_r),
                "win_rate": round(sum(1 for r in sub_r if r > 0) / len(sub_r), 4),
                "avg_r": round(float(np.mean(sub_r)), 4),
            }

    # Breakdown by News Filter
    breakdown_news: dict[str, dict[str, Any]] = {}
    for nf in [True, False]:
        nf_trades = [t for t in trades if bool(t.get("news_filtered", False)) == nf]
        lbl = "news_filtered" if nf else "unfiltered"
        if nf_trades:
            sub_r = [float(t["r_multiple"]) for t in nf_trades]
            breakdown_news[lbl] = {
                "n": len(sub_r),
                "win_rate": round(sum(1 for r in sub_r if r > 0) / len(sub_r), 4),
                "avg_r": round(float(np.mean(sub_r)), 4),
            }

    return {
        "n_trades": n,
        "win_rate": round(win_rate, 4),
        "avg_r": round(avg_r, 4),
        "median_r": round(median_r, 4),
        "expectancy": round(avg_r, 4),
        "ci_95_lower": round(ci_lower, 4) if ci_lower is not None else None,
        "ci_95_upper": round(ci_upper, 4) if ci_upper is not None else None,
        "t_stat": round(t_stat, 4) if t_stat is not None else None,
        "p_value": round(p_val, 6) if p_val is not None else None,
        "profit_factor": profit_factor,
        "max_drawdown_r": max_dd_r,
        "max_drawdown_pct": max_dd_pct,
        "longest_losing_streak": longest_losing_streak,
        "daily_stats": {
            "days_count": len(daily_totals),
            "mean_daily_r": round(float(np.mean(daily_totals)), 4) if daily_totals else 0.0,
            "std_daily_r": round(float(np.std(daily_totals)), 4) if daily_totals else 0.0,
            "days_tripped_3r": days_tripped_3r,
            "max_trades_per_day": max_trades_per_day,
        },
        "costs": {
            "avg_spread_entry": round(avg_spread, 4),
            "max_spread_entry": round(max_spread, 4),
            "avg_slippage": round(avg_slippage, 4),
            "avg_stop_distance_dollars": round(avg_stop_dist, 4),
            "total_cost_r": round(cost_in_r, 4),
        },
        "unfilled_rate": round(
            sum(1 for t in trades if t.get("status") == "CANCELLED") / max(1, len(trades)),
            4,
        ),
        "breakdown_weekday": breakdown_weekday,
        "breakdown_direction": breakdown_direction,
        "breakdown_news_filter": breakdown_news,
    }


def _get_weekday_name(ts: Any) -> str:
    if not ts:
        return "Unknown"
    try:
        dt_obj = dt.datetime.fromisoformat(str(ts))
        return dt_obj.strftime("%A")
    except Exception:
        return "Unknown"
