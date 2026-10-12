"""Fast vectorized Null / Random-Entry Baseline models for statistical edge validation.

Evaluates:
(a) Random entry time & direction in strategy time windows, with stop distances
    sampled from the empirical distribution and same R target.
(b) Fixed real signal timestamps with randomized direction (50/50 Long/Short).
Reports:
- Null distribution of average R (mean, median, std, 5th, 95th percentiles).
- Empirical percentile and p-value of the real strategy vs null distributions.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
import logging
from typing import Any
from zoneinfo import ZoneInfo
import numpy as np

from engine.market.models import Bar

logger = logging.getLogger(__name__)

NY_TZ = ZoneInfo("America/New_York")


def run_null_models(
    bars: list[Bar],
    real_trades: list[dict[str, Any]],
    n_iterations: int = 1_000,
    seed: int = 42,
    window_hours: tuple[int, int] = (2, 5),  # Default 02:00-05:00 NY for Judas
) -> dict[str, Any]:
    """Run Null Baseline Model (a) and Model (b) using fast vectorized array lookups."""
    rng = np.random.default_rng(seed)

    if not real_trades:
        return {
            "model_a": {"p_value": 1.0, "null_mean": 0.0, "null_std": 0.0, "percentile": 50.0},
            "model_b": {"p_value": 1.0, "null_mean": 0.0, "null_std": 0.0, "percentile": 50.0},
        }

    real_r_multiples = [float(t["r_multiple"]) for t in real_trades]
    real_avg_r = float(np.mean(real_r_multiples))
    n_real = len(real_trades)

    # Pre-extract arrays for ultra-fast vector evaluation
    n_bars = len(bars)
    highs = np.array([float(b.mid_ohlc.high) for b in bars], dtype=np.float64)
    lows = np.array([float(b.mid_ohlc.low) for b in bars], dtype=np.float64)
    closes = np.array([float(b.mid_ohlc.close) for b in bars], dtype=np.float64)

    # Extract empirical stop distances (filter out 0.0 from trades trailed to breakeven)
    stop_distances = [
        float(abs(Decimal(str(t["entry_price"])) - Decimal(str(t["stop_loss"]))))
        for t in real_trades
        if t.get("entry_price") and t.get("stop_loss") and abs(Decimal(str(t["entry_price"])) - Decimal(str(t["stop_loss"]))) > Decimal("0.10")
    ]
    if not stop_distances:
        stop_distances = [3.0]  # Fallback $3.00

    w_start, w_end = window_hours
    window_bar_indices = [
        idx for idx, b in enumerate(bars)
        if w_start <= b.timestamp_utc.astimezone(NY_TZ).hour < w_end
        and idx < n_bars - 288
    ]

    # Map real trade timestamps to bar indices for Model (b)
    bar_time_to_idx = {b.timestamp_utc: idx for idx, b in enumerate(bars)}
    real_trade_bar_indices: list[int] = []
    real_trade_stop_dists: list[float] = []

    for t in real_trades:
        ts_str = t.get("occurred_at") or t.get("time") or t.get("entry_time")
        raw_dist = float(abs(Decimal(str(t["entry_price"])) - Decimal(str(t["stop_loss"]))))
        dist = raw_dist if raw_dist > 0.10 else 3.0
        if ts_str:
            try:
                dt_obj = dt.datetime.fromisoformat(str(ts_str))
                if dt_obj.tzinfo is None:
                    dt_obj = dt_obj.replace(tzinfo=dt.UTC)
                idx = bar_time_to_idx.get(dt_obj)
                if idx is None:
                    idx = min(
                        range(len(bars)),
                        key=lambda i: abs((bars[i].timestamp_utc - dt_obj).total_seconds()),
                    )
                real_trade_bar_indices.append(idx)
                real_trade_stop_dists.append(dist)
            except Exception:
                continue

    def _eval_single_path_fast(start_idx: int, side: str, stop_dist: float) -> float:
        stop_dist = max(float(stop_dist), 0.10)
        end_idx = min(n_bars, start_idx + 288)
        if end_idx <= start_idx:
            return 0.0
        entry = closes[start_idx - 1]
        sub_highs = highs[start_idx:end_idx]
        sub_lows = lows[start_idx:end_idx]

        if side == "BUY":
            sl = entry - stop_dist
            tp = entry + (2.0 * stop_dist)
            sl_hits = np.where(sub_lows <= sl)[0]
            tp_hits = np.where(sub_highs >= tp)[0]
        else:
            sl = entry + stop_dist
            tp = entry - (2.0 * stop_dist)
            sl_hits = np.where(sub_highs >= sl)[0]
            tp_hits = np.where(sub_lows <= tp)[0]

        first_sl = int(sl_hits[0]) if len(sl_hits) > 0 else 999999
        first_tp = int(tp_hits[0]) if len(tp_hits) > 0 else 999999

        if first_sl <= first_tp and first_sl != 999999:
            # Conservative: STOP hit first or conflict
            return -(stop_dist + 0.05) / stop_dist
        elif first_tp < first_sl and first_tp != 999999:
            return 2.0
        else:
            # Time close
            last_c = closes[end_idx - 1]
            pnl = (last_c - entry) if side == "BUY" else (entry - last_c)
            return pnl / stop_dist

    # -------------------------------------------------------------
    # (a) Random entry time and direction inside window
    # -------------------------------------------------------------
    null_a_means: list[float] = []
    if window_bar_indices:
        for _ in range(n_iterations):
            sampled_indices = rng.choice(window_bar_indices, size=n_real, replace=True)
            sampled_stops = rng.choice(stop_distances, size=n_real, replace=True)
            sampled_sides = rng.choice(["BUY", "SELL"], size=n_real)

            iter_r = [
                _eval_single_path_fast(int(s_idx) + 1, str(side), float(stop_d))
                for s_idx, stop_d, side in zip(sampled_indices, sampled_stops, sampled_sides, strict=False)
            ]
            null_a_means.append(float(np.mean(iter_r)))

    # -------------------------------------------------------------
    # (b) Real signal timestamps, randomized direction
    # -------------------------------------------------------------
    null_b_means: list[float] = []
    if real_trade_bar_indices:
        for _ in range(n_iterations):
            sampled_sides = rng.choice(["BUY", "SELL"], size=len(real_trade_bar_indices))
            iter_r = [
                _eval_single_path_fast(int(s_idx) + 1, str(side), float(stop_d))
                for s_idx, stop_d, side in zip(
                    real_trade_bar_indices, real_trade_stop_dists, sampled_sides, strict=False
                )
            ]
            null_b_means.append(float(np.mean(iter_r)))

    def _eval_null(null_means: list[float]) -> dict[str, Any]:
        if not null_means:
            return {"p_value": 1.0, "null_mean": 0.0, "null_std": 0.0, "percentile": 50.0}
        arr = np.array(null_means)
        p_val = float(np.mean(arr >= real_avg_r))
        percentile = float(np.mean(arr < real_avg_r) * 100.0)
        return {
            "real_avg_r": round(real_avg_r, 4),
            "null_mean": round(float(np.mean(arr)), 4),
            "null_std": round(float(np.std(arr)), 4),
            "null_median": round(float(np.median(arr)), 4),
            "null_ci_95": [
                round(float(np.percentile(arr, 2.5)), 4),
                round(float(np.percentile(arr, 97.5)), 4),
            ],
            "percentile": round(percentile, 2),
            "p_value": round(p_val, 4),
            "iterations": len(null_means),
        }

    return {
        "model_a": _eval_null(null_a_means),
        "model_b": _eval_null(null_b_means),
        "null_a_distribution": null_a_means,
        "null_b_distribution": null_b_means,
    }
