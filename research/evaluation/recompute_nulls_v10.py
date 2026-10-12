import sys
sys.path.insert(0, "engine/src")
sys.path.insert(0, ".")

import bisect
import datetime as dt
from decimal import Decimal
import numpy as np
from zoneinfo import ZoneInfo

from research.data.loader import load_all_m5_bars
from research.backtest.engine import BacktestRunner, SimulatedTrade
from research.strategies.enhanced_judas import EnhancedJudasStrategy
from research.metrics.trade_stats import compute_trade_metrics
from research.evaluation.followup_analysis import _trade_to_dict

NY_TZ = ZoneInfo("America/New_York")

bars_val = load_all_m5_bars(years=["2020", "2021", "2022"])
val_bar_ts = [b.timestamp_utc for b in bars_val]

runner = BacktestRunner(base_spread=Decimal("0.20"), base_slippage=Decimal("0.05"))
strat_v0 = EnhancedJudasStrategy()
trades_v0, _ = runner.run_judas_backtest(bars_val, judas_strategy=strat_v0)

# Build Mirror trades V10
mirror_simulated_trades: list[SimulatedTrade] = []
trade_id_counter = 0

for t in trades_v0:
    trade_id_counter += 1
    mirror_side = "BUY" if t.side == "SELL" else "SELL"
    entry_p = t.entry_price
    stop_dist = abs(t.entry_price - t.stop_loss)
    target_dist = abs(t.entry_price - t.take_profit)
    r_target_mult = target_dist / stop_dist if stop_dist > 0 else Decimal("2.0")

    if mirror_side == "BUY":
        mirror_sl = entry_p - stop_dist
        mirror_tp = entry_p + (r_target_mult * stop_dist)
    else:
        mirror_sl = entry_p + stop_dist
        mirror_tp = entry_p - (r_target_mult * stop_dist)

    start_idx = bisect.bisect_left(val_bar_ts, t.entry_time)
    trade_obj = SimulatedTrade(
        trade_id=f"mirror-{trade_id_counter:04d}",
        strategy="Mirror Judas Breakout",
        side=mirror_side,
        entry_time=t.entry_time,
        entry_price=entry_p,
        stop_loss=mirror_sl,
        take_profit=mirror_tp,
        risk_dollars=stop_dist,
        reward_dollars=target_dist,
    )

    exit_bar = None
    exit_p = None
    exit_reason = "TIMEOUT"
    is_stop = False

    for b_idx in range(start_idx, min(len(bars_val), start_idx + 288)):
        b = bars_val[b_idx]
        mid = b.mid_ohlc
        hit_sl = False
        hit_tp = False
        if mirror_side == "BUY":
            if mid.low <= mirror_sl:
                hit_sl = True
            if mid.high >= mirror_tp:
                hit_tp = True
        else:
            if mid.high >= mirror_sl:
                hit_sl = True
            if mid.low <= mirror_tp:
                hit_tp = True

        if hit_sl and hit_tp:
            exit_bar = b
            exit_p = mirror_sl
            exit_reason = "STOP_LOSS (conflict)"
            is_stop = True
            break
        elif hit_sl:
            exit_bar = b
            exit_p = mirror_sl
            exit_reason = "STOP_LOSS"
            is_stop = True
            break
        elif hit_tp:
            exit_bar = b
            exit_p = mirror_tp
            exit_reason = "TAKE_PROFIT"
            is_stop = False
            break

    if exit_bar is None:
        last_b = bars_val[min(len(bars_val) - 1, start_idx + 287)]
        exit_bar = last_b
        exit_p = last_b.mid_ohlc.close
        exit_reason = "TIMEOUT"
        is_stop = False

    runner._close_trade(
        trade_obj,
        exit_price=exit_p,
        exit_time=exit_bar.timestamp_utc,
        reason=exit_reason,
        is_stop=is_stop,
    )
    mirror_simulated_trades.append(trade_obj)

mirror_dicts = [_trade_to_dict(t) for t in mirror_simulated_trades]
m_mirror = compute_trade_metrics(mirror_dicts, seed=42)
real_mirror_exp = m_mirror["expectancy"]
print(f"V10 Mirror: N={m_mirror['n_trades']}, WR={m_mirror['win_rate']:.4f}, Exp={real_mirror_exp:.4f}")

# Now build cost-aligned Null Model A and Null Model B for V10 Mirror
# Fast vectorized arrays
highs = np.array([float(b.mid_ohlc.high) for b in bars_val], dtype=np.float64)
lows = np.array([float(b.mid_ohlc.low) for b in bars_val], dtype=np.float64)
closes = np.array([float(b.mid_ohlc.close) for b in bars_val], dtype=np.float64)
bar_ts_map = {b.timestamp_utc: idx for idx, b in enumerate(bars_val)}

v10_bar_indices = []
v10_stop_dists = []
v10_r_targets = []
for t in mirror_dicts:
    dt_val = dt.datetime.fromisoformat(t["entry_time"])
    idx_val = bar_ts_map.get(dt_val)
    if idx_val is not None:
        v10_bar_indices.append(idx_val)
        entry_f = float(t["entry_price"])
        sl_f = float(t["stop_loss"])
        tp_f = float(t["take_profit"])
        s_dist = abs(entry_f - sl_f)
        t_dist = abs(entry_f - tp_f)
        r_tgt = t_dist / s_dist if s_dist > 0.01 else 2.0
        v10_stop_dists.append(max(s_dist, 0.10))
        v10_r_targets.append(r_tgt)

# Window bar indices for Null A (02:00 to 05:00 NY)
window_bar_indices = [
    idx for idx, b in enumerate(bars_val)
    if 2 <= b.timestamp_utc.astimezone(NY_TZ).hour < 5 and idx < len(bars_val) - 288
]

rng = np.random.default_rng(42)
n_iterations = 1_000

# Cost model: spread 0.20, slippage 0.05 on stop
def eval_path(start_idx, side, s_dist, r_tgt):
    end_idx = min(len(bars_val), start_idx + 288)
    entry = closes[start_idx - 1]
    sub_h = highs[start_idx:end_idx]
    sub_l = lows[start_idx:end_idx]

    spread_r = 0.20 / s_dist
    slippage_r = 0.05 / s_dist

    if side == "BUY":
        sl = entry - s_dist
        tp = entry + (r_tgt * s_dist)
        sl_hits = np.where(sub_l <= sl)[0]
        tp_hits = np.where(sub_h >= tp)[0]
    else:
        sl = entry + s_dist
        tp = entry - (r_tgt * s_dist)
        sl_hits = np.where(sub_h >= sl)[0]
        tp_hits = np.where(sub_l <= tp)[0]

    first_sl = int(sl_hits[0]) if len(sl_hits) > 0 else 999999
    first_tp = int(tp_hits[0]) if len(tp_hits) > 0 else 999999

    if first_sl <= first_tp and first_sl != 999999:
        return -1.0 - spread_r - slippage_r
    elif first_tp < first_sl and first_tp != 999999:
        return r_tgt - spread_r
    else:
        last_c = closes[end_idx - 1]
        raw_diff = (last_c - entry) if side == "BUY" else (entry - last_c)
        return (raw_diff / s_dist) - spread_r

# Re-run Null Model A (random window bars, random stop dist, random side)
null_a_means = []
n_trades_eval = len(v10_bar_indices)
for _ in range(n_iterations):
    sampled_indices = rng.choice(window_bar_indices, size=n_trades_eval, replace=True)
    sampled_stops = rng.choice(v10_stop_dists, size=n_trades_eval, replace=True)
    sampled_targets = rng.choice(v10_r_targets, size=n_trades_eval, replace=True)
    sampled_sides = rng.choice(["BUY", "SELL"], size=n_trades_eval)
    iter_r = [eval_path(idx + 1, side, s_d, r_t) for idx, side, s_d, r_t in zip(sampled_indices, sampled_sides, sampled_stops, sampled_targets)]
    null_a_means.append(float(np.mean(iter_r)))

# Re-run Null Model B (fixed signal bars, fixed stop dist, fixed target, random side)
null_b_means = []
for _ in range(n_iterations):
    sampled_sides = rng.choice(["BUY", "SELL"], size=n_trades_eval)
    iter_r = [eval_path(idx, side, s_d, r_t) for idx, side, s_d, r_t in zip(v10_bar_indices, sampled_sides, v10_stop_dists, v10_r_targets)]
    null_b_means.append(float(np.mean(iter_r)))

# Evaluation using single convention: Percentile of real within null
# Percentile = % of null distribution values < real_exp
# p-value = 1.0 - (percentile / 100) = % of null values >= real_exp
pct_a = float(np.mean(np.array(null_a_means) <= real_mirror_exp) * 100.0)
pval_a = float(np.mean(np.array(null_a_means) >= real_mirror_exp))

pct_b = float(np.mean(np.array(null_b_means) <= real_mirror_exp) * 100.0)
pval_b = float(np.mean(np.array(null_b_means) >= real_mirror_exp))

print(f"\n--- COST-ALIGNED NULL RESULTS FOR V10 MIRROR ---")
print(f"Real V10 Expectancy: {real_mirror_exp:.4f}R")
print(f"Null Model A Mean: {np.mean(null_a_means):.4f}R, CI=[{np.percentile(null_a_means, 2.5):.4f}, {np.percentile(null_a_means, 97.5):.4f}]")
print(f"  Percentile of Real within Null A: {pct_a:.1f}% (p-value: {pval_a:.4f})")
print(f"Null Model B Mean: {np.mean(null_b_means):.4f}R, CI=[{np.percentile(null_b_means, 2.5):.4f}, {np.percentile(null_b_means, 97.5):.4f}]")
print(f"  Percentile of Real within Null B: {pct_b:.1f}% (p-value: {pval_b:.4f})")
