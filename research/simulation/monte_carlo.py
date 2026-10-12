"""Monte Carlo risk and sequence permutation simulation.

Simulates 10,000 shuffles of trade sequence at 1% risk:
- 95th-percentile maximum drawdown (in R and %)
- Probability of experiencing a 10-loss streak
- Frequency of triggering the -3R daily loss kill-switch
- Number of trades needed to reach 2 standard errors of observed expectancy
"""

from __future__ import annotations

import math
from typing import Any
import numpy as np


def run_monte_carlo_risk(
    r_multiples: list[float],
    trades_per_day: list[int] | None = None,
    n_simulations: int = 10_000,
    risk_pct: float = 0.01,
    seed: int = 42,
) -> dict[str, Any]:
    """Execute 10,000 Monte Carlo trade sequence permutations."""
    if not r_multiples:
        return {
            "n_simulations": 0,
            "mdd_pct_95th": 0.0,
            "mdd_r_95th": 0.0,
            "prob_10_loss_streak": 0.0,
            "prob_daily_stop_triggered": 0.0,
            "avg_daily_stops_per_run": 0.0,
            "trades_to_2_se": None,
        }

    rng = np.random.default_rng(seed)
    r_arr = np.array(r_multiples, dtype=np.float64)
    n_trades = len(r_arr)

    mean_r = float(np.mean(r_arr))
    std_r = float(np.std(r_arr, ddof=1)) if n_trades > 1 else 0.0

    # Trades needed for 2 SE (t-stat >= 2.0)
    # SE = std / sqrt(N). We want mean_r >= 2 * SE => N >= (2 * std / mean_r)^2
    trades_to_2_se: int | None = None
    if mean_r > 0.0 and std_r > 0.0:
        trades_to_2_se = int(math.ceil((2.0 * std_r / mean_r) ** 2))

    # Prepare daily slot partition if trades_per_day provided
    day_lengths = trades_per_day if trades_per_day and sum(trades_per_day) == n_trades else [1] * n_trades

    sim_mdd_pct: list[float] = []
    sim_mdd_r: list[float] = []
    has_10_loss_count = 0
    runs_with_daily_stop = 0
    total_daily_stops = 0

    for _ in range(n_simulations):
        # Permute trade outcomes
        shuffled = rng.permutation(r_arr)

        # 1. Drawdown in %
        equity_curve = np.cumprod(1.0 + (risk_pct * shuffled))
        running_max_pct = np.maximum.accumulate(equity_curve)
        # Avoid division by zero
        drawdowns_pct = (running_max_pct - equity_curve) / np.maximum(running_max_pct, 1e-9)
        mdd_pct = float(np.max(drawdowns_pct))
        sim_mdd_pct.append(mdd_pct)

        # 2. Drawdown in R
        cum_r = np.cumsum(shuffled)
        running_max_r = np.maximum.accumulate(cum_r)
        drawdowns_r = running_max_r - cum_r
        mdd_r = float(np.max(drawdowns_r))
        sim_mdd_r.append(mdd_r)

        # 3. 10-loss streak check
        longest_streak = 0
        curr_streak = 0
        for val in shuffled:
            if val <= 0.0:
                curr_streak += 1
                if curr_streak > longest_streak:
                    longest_streak = curr_streak
            else:
                curr_streak = 0
        if longest_streak >= 10:
            has_10_loss_count += 1

        # 4. Daily -3R killswitch trigger check
        curr_idx = 0
        day_stops_in_run = 0
        for d_len in day_lengths:
            day_slice = shuffled[curr_idx : curr_idx + d_len]
            curr_idx += d_len
            # Check cumulative sum within day
            day_cum = 0.0
            for r_val in day_slice:
                day_cum += r_val
                if day_cum <= -3.0:
                    day_stops_in_run += 1
                    break
        if day_stops_in_run > 0:
            runs_with_daily_stop += 1
        total_daily_stops += day_stops_in_run

    mdd_pct_95th = float(np.percentile(sim_mdd_pct, 95)) * 100.0
    mdd_r_95th = float(np.percentile(sim_mdd_r, 95))
    prob_10_loss = has_10_loss_count / n_simulations
    prob_daily_stop = runs_with_daily_stop / n_simulations
    avg_daily_stops = total_daily_stops / n_simulations

    return {
        "n_simulations": n_simulations,
        "observed_mean_r": round(mean_r, 4),
        "observed_std_r": round(std_r, 4),
        "mdd_pct_95th": round(mdd_pct_95th, 2),
        "mdd_pct_median": round(float(np.median(sim_mdd_pct)) * 100.0, 2),
        "mdd_r_95th": round(mdd_r_95th, 2),
        "mdd_r_median": round(float(np.median(sim_mdd_r)), 2),
        "prob_10_loss_streak": round(prob_10_loss, 4),
        "prob_daily_stop_triggered": round(prob_daily_stop, 4),
        "avg_daily_stops_per_run": round(avg_daily_stops, 2),
        "trades_to_2_se": trades_to_2_se,
    }
