import csv
import datetime as dt
from decimal import Decimal
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo
import numpy as np

sys.path.insert(0, "engine/src")
sys.path.insert(0, ".")

from engine.market.instrument import InstrumentSpec
from engine.market.models import Bar
from engine.strategy.judas_swing import JudasSwingStrategy
from research.data.loader import load_all_m5_bars
from research.backtest.engine import BacktestRunner, SimulatedTrade
from research.strategies.enhanced_judas import EnhancedJudasStrategy
from research.adapters.silver_bullet_adapter import load_silver_bullet_strategy
from research.metrics.trade_stats import compute_trade_metrics
from research.evaluation.followup_analysis import _trade_to_dict

NY_TZ = ZoneInfo("America/New_York")

print("=" * 60)
print("TASK 1: Core JudasSwingStrategy on 2020-2022 (1x Costs)")
print("=" * 60)
bars_val = load_all_m5_bars(years=["2020", "2021", "2022"])
runner_1x = BacktestRunner(base_spread=Decimal("0.20"), base_slippage=Decimal("0.05"))
core_strat = JudasSwingStrategy()
trades_core_1x, _ = runner_1x.run_judas_backtest(bars_val, judas_strategy=core_strat)
core_dicts_1x = [_trade_to_dict(t) for t in trades_core_1x]
if not core_dicts_1x:
    print("No bars found in cache (local bar cache pruned). Exiting gracefully.")
    sys.exit(0)
m_core_1x = compute_trade_metrics(core_dicts_1x, seed=42)

# Save core trades to CSV for verify.py
out_dir = Path("research/output")
out_dir.mkdir(parents=True, exist_ok=True)
core_csv_path = out_dir / "judas_core_trades_2020_2022.csv"
with open(core_csv_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=list(core_dicts_1x[0].keys()))
    writer.writeheader()
    writer.writerows(core_dicts_1x)

n_core = m_core_1x["n_trades"]
wins_core = sum(1 for t in core_dicts_1x if t["r_multiple"] > 0)
exp_core = m_core_1x["expectancy"]
ci_core = [m_core_1x["ci_95_lower"], m_core_1x["ci_95_upper"]]

print(f"N: {n_core}")
print(f"Wins: {wins_core} (Win Rate: {m_core_1x['win_rate']*100:.2f}%)")
print(f"Expectancy: {exp_core:.4f}R")
print(f"95% Bootstrap CI: [{ci_core[0]:.4f}, {ci_core[1]:.4f}]")
print(f"Profit Factor: {m_core_1x['profit_factor']:.2f}")

# Run cost-aligned nulls for core strategy
from research.evaluation.recompute_nulls_v10 import eval_path, highs, lows, closes, bar_ts_map, window_bar_indices
# Map core trades
core_indices = []
core_stops = []
core_targets = []
for t in core_dicts_1x:
    dt_val = dt.datetime.fromisoformat(t["entry_time"])
    idx_val = bar_ts_map.get(dt_val)
    if idx_val is not None:
        core_indices.append(idx_val)
        entry_f = float(t["entry_price"])
        sl_f = float(t["stop_loss"])
        tp_f = float(t["take_profit"])
        s_dist = abs(entry_f - sl_f)
        t_dist = abs(entry_f - tp_f)
        r_tgt = t_dist / s_dist if s_dist > 0.01 else 2.0
        core_stops.append(max(s_dist, 0.10))
        core_targets.append(r_tgt)

rng = np.random.default_rng(42)
n_iter = 1_000
null_a_core = []
for _ in range(n_iter):
    s_idx = rng.choice(window_bar_indices, size=len(core_indices), replace=True)
    s_stops = rng.choice(core_stops, size=len(core_indices), replace=True)
    s_tgts = rng.choice(core_targets, size=len(core_indices), replace=True)
    s_sides = rng.choice(["BUY", "SELL"], size=len(core_indices))
    iter_r = [eval_path(i + 1, side, sd, rt) for i, side, sd, rt in zip(s_idx, s_sides, s_stops, s_tgts)]
    null_a_core.append(float(np.mean(iter_r)))

null_b_core = []
for _ in range(n_iter):
    s_sides = rng.choice(["BUY", "SELL"], size=len(core_indices))
    iter_r = [eval_path(i, side, sd, rt) for i, side, sd, rt in zip(core_indices, s_sides, core_stops, core_targets)]
    null_b_core.append(float(np.mean(iter_r)))

pct_a_core = float(np.mean(np.array(null_a_core) <= exp_core) * 100.0)
pval_a_core = float(np.mean(np.array(null_a_core) >= exp_core))
pct_b_core = float(np.mean(np.array(null_b_core) <= exp_core) * 100.0)
pval_b_core = float(np.mean(np.array(null_b_core) >= exp_core))

print(f"Null Model A Mean: {np.mean(null_a_core):.4f}R, Percentile: {pct_a_core:.1f}%, p-val: {pval_a_core:.4f}")
print(f"Null Model B Mean: {np.mean(null_b_core):.4f}R, Percentile: {pct_b_core:.1f}%, p-val: {pval_b_core:.4f}")


print("\n" + "=" * 60)
print("TASK 2: Silver Bullet Limit-Entry R-Multiples Dump & Comparison")
print("=" * 60)
spec = InstrumentSpec("XAU_USD", -2, 2, 2, Decimal("0.01"))
strat_sb_val, _ = load_silver_bullet_strategy(spec)
orig_val = strat_sb_val.on_bar
def p_val(b: Bar):
    res = orig_val(b)
    idx = strat_sb_val._detector.bar_count
    if idx > 400:
        strat_sb_val._detector.drop_levels(idx - 400)
    return res
strat_sb_val.on_bar = p_val
trades_sb_val, _ = runner_1x.run_silver_bullet_backtest(bars_val, strat_sb_val)
sb_dicts_val = [_trade_to_dict(t) for t in trades_sb_val]

# Dump 2020-2022 to CSV
sb_val_csv = out_dir / "silver_bullet_trades_2020_2022.csv"
with open(sb_val_csv, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=list(sb_dicts_val[0].keys()))
    writer.writeheader()
    writer.writerows(sb_dicts_val)

# 2023-2025
bars_disc_raw = load_all_m5_bars(years=["2023", "2024", "2025"])
seen_23_25 = set()
bars_disc = []
for b in sorted(bars_disc_raw, key=lambda x: x.timestamp_utc):
    if b.timestamp_utc not in seen_23_25:
        seen_23_25.add(b.timestamp_utc)
        bars_disc.append(b)

strat_sb_disc, _ = load_silver_bullet_strategy(spec)
orig_disc = strat_sb_disc.on_bar
def p_disc(b: Bar):
    res = orig_disc(b)
    idx = strat_sb_disc._detector.bar_count
    if idx > 400:
        strat_sb_disc._detector.drop_levels(idx - 400)
    return res
strat_sb_disc.on_bar = p_disc
trades_sb_disc, _ = runner_1x.run_silver_bullet_backtest(bars_disc, strat_sb_disc)
sb_dicts_disc = [_trade_to_dict(t) for t in trades_sb_disc]

# Dump 2023-2025 to CSV
sb_disc_csv = out_dir / "silver_bullet_trades_2023_2025.csv"
with open(sb_disc_csv, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=list(sb_dicts_disc[0].keys()))
    writer.writeheader()
    writer.writerows(sb_dicts_disc)

m_sb_val = compute_trade_metrics(sb_dicts_val, seed=42)
m_sb_disc = compute_trade_metrics(sb_dicts_disc, seed=42)

print(f"2020-2022 SB: Exp={m_sb_val['expectancy']:.4f}R, CI=[{m_sb_val['ci_95_lower']:.4f}, {m_sb_val['ci_95_upper']:.4f}], PF={m_sb_val['profit_factor']:.2f}")
print(f"2023-2025 SB: Exp={m_sb_disc['expectancy']:.4f}R, CI=[{m_sb_disc['ci_95_lower']:.4f}, {m_sb_disc['ci_95_upper']:.4f}], PF={m_sb_disc['profit_factor']:.2f}")

print("\nSide-by-Side Trade R Comparison (27 trades each):")
print(f"{'#':<3} | {'2020-2022 Entry':<20} {'R':<8} | {'2023-2025 Entry':<20} {'R':<8} | {'Match?'}")
print("-" * 75)
identical_rows = 0
for i in range(27):
    t_v = sb_dicts_val[i]
    t_d = sb_dicts_disc[i]
    rv = float(t_v["r_multiple"])
    rd = float(t_d["r_multiple"])
    match = (abs(rv - rd) < 0.001)
    if match: identical_rows += 1
    print(f"{i+1:2d}  | {t_v['entry_time'][:16]:<20} {rv:>7.4f} | {t_d['entry_time'][:16]:<20} {rd:>7.4f} | {str(match)}")

print(f"\nIdentical rows count: {identical_rows}/27")


print("\n" + "=" * 60)
print("TASK 3: Gross (0x) vs Net (1x) on the 525 Signal Bars")
print("=" * 60)
# Fade (V0)
runner_0x = BacktestRunner(base_spread=Decimal("0.0"), base_slippage=Decimal("0.0"), cost_multiplier=Decimal("0.0"))
trades_fade_0x, _ = runner_0x.run_judas_backtest(bars_val, judas_strategy=EnhancedJudasStrategy())
fade_0x_dicts = [_trade_to_dict(t) for t in trades_fade_0x]
m_fade_0x = compute_trade_metrics(fade_0x_dicts, seed=42)

trades_fade_1x, _ = runner_1x.run_judas_backtest(bars_val, judas_strategy=EnhancedJudasStrategy())
fade_1x_dicts = [_trade_to_dict(t) for t in trades_fade_1x]
m_fade_1x = compute_trade_metrics(fade_1x_dicts, seed=42)

# Dump fade trades to CSV
with open(out_dir / "judas_fade_trades_2020_2022.csv", "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=list(fade_1x_dicts[0].keys()))
    writer.writeheader()
    writer.writerows(fade_1x_dicts)

# Mirror (V10)
from research.evaluation.recompute_nulls_v10 import mirror_simulated_trades, mirror_dicts, m_mirror
# Re-run mirror at 0x
mirror_0x_trades = []
import bisect
val_bar_ts = [b.timestamp_utc for b in bars_val]
for t in trades_fade_0x:
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
        trade_id=t.trade_id,
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
        hit_sl = (mid.low <= mirror_sl) if mirror_side == "BUY" else (mid.high >= mirror_sl)
        hit_tp = (mid.high >= mirror_tp) if mirror_side == "BUY" else (mid.low <= mirror_tp)
        if hit_sl and hit_tp:
            exit_bar = b; exit_p = mirror_sl; exit_reason = "STOP_LOSS (conflict)"; is_stop = True; break
        elif hit_sl:
            exit_bar = b; exit_p = mirror_sl; exit_reason = "STOP_LOSS"; is_stop = True; break
        elif hit_tp:
            exit_bar = b; exit_p = mirror_tp; exit_reason = "TAKE_PROFIT"; is_stop = False; break
    if exit_bar is None:
        last_b = bars_val[min(len(bars_val) - 1, start_idx + 287)]
        exit_bar = last_b; exit_p = last_b.mid_ohlc.close; exit_reason = "TIMEOUT"; is_stop = False

    runner_0x._close_trade(trade_obj, exit_price=exit_p, exit_time=exit_bar.timestamp_utc, reason=exit_reason, is_stop=is_stop)
    mirror_0x_trades.append(trade_obj)

mirror_0x_dicts = [_trade_to_dict(t) for t in mirror_0x_trades]
m_mirror_0x = compute_trade_metrics(mirror_0x_dicts, seed=42)

# Dump mirror trades 1x to CSV
with open(out_dir / "judas_mirror_trades_2020_2022.csv", "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=list(mirror_dicts[0].keys()))
    writer.writeheader()
    writer.writerows(mirror_dicts)

# Null Model B: 0x and 1x
# For 0x Null B:
def eval_path_0x(start_idx, side, s_dist, r_tgt):
    end_idx = min(len(bars_val), start_idx + 288)
    entry = closes[start_idx - 1]
    sub_h = highs[start_idx:end_idx]
    sub_l = lows[start_idx:end_idx]
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
        return -1.0
    elif first_tp < first_sl and first_tp != 999999:
        return r_tgt
    else:
        last_c = closes[end_idx - 1]
        raw_diff = (last_c - entry) if side == "BUY" else (entry - last_c)
        return raw_diff / s_dist

null_b_0x_means = []
null_b_1x_means = []
from research.evaluation.recompute_nulls_v10 import v10_bar_indices, v10_stop_dists, v10_r_targets
for _ in range(n_iter):
    s_sides = rng.choice(["BUY", "SELL"], size=len(v10_bar_indices))
    iter_0x = [eval_path_0x(i, side, sd, rt) for i, side, sd, rt in zip(v10_bar_indices, s_sides, v10_stop_dists, v10_r_targets)]
    iter_1x = [eval_path(i, side, sd, rt) for i, side, sd, rt in zip(v10_bar_indices, s_sides, v10_stop_dists, v10_r_targets)]
    null_b_0x_means.append(float(np.mean(iter_0x)))
    null_b_1x_means.append(float(np.mean(iter_1x)))

exp_fade_0x = m_fade_0x["expectancy"]
exp_fade_1x = m_fade_1x["expectancy"]
exp_mirror_0x = m_mirror_0x["expectancy"]
exp_mirror_1x = m_mirror["expectancy"]
exp_null_b_0x = float(np.mean(null_b_0x_means))
exp_null_b_1x = float(np.mean(null_b_1x_means))
avg_fm_0x = (exp_fade_0x + exp_mirror_0x) / 2.0
avg_fm_1x = (exp_fade_1x + exp_mirror_1x) / 2.0

print(f"Fade (V0):   Gross (0x) = {exp_fade_0x:+.4f}R | Net (1x) = {exp_fade_1x:+.4f}R | Cost Drag = {exp_fade_0x - exp_fade_1x:.4f}R")
print(f"Mirror (V10): Gross (0x) = {exp_mirror_0x:+.4f}R | Net (1x) = {exp_mirror_1x:+.4f}R | Cost Drag = {exp_mirror_0x - exp_mirror_1x:.4f}R")
print(f"Null B Mean:  Gross (0x) = {exp_null_b_0x:+.4f}R | Net (1x) = {exp_null_b_1x:+.4f}R | Cost Drag = {exp_null_b_0x - exp_null_b_1x:.4f}R")
print(f"Average of Fade & Mirror: Gross = {avg_fm_0x:+.4f}R | Net = {avg_fm_1x:+.4f}R")
print(f"Gap (Null B - Avg(Fade,Mirror)): Gross = {exp_null_b_0x - avg_fm_0x:+.4f}R | Net = {exp_null_b_1x - avg_fm_1x:+.4f}R")


print("\n" + "=" * 60)
print("TASK 4: Silver Bullet Time Windows (NY Time) Breakdown")
print("=" * 60)
# Check hours of trades in 2020-2022 and 2023-2025
def breakdown_sb_windows(trades_dicts):
    london_count = 0
    ny_am_count = 0
    other_count = 0
    for t in trades_dicts:
        dt_obj = dt.datetime.fromisoformat(t["entry_time"]).astimezone(NY_TZ)
        h = dt_obj.hour
        if h == 3:
            london_count += 1
        elif h == 10:
            ny_am_count += 1
        else:
            other_count += 1
    return london_count, ny_am_count, other_count

l_val, ny_val, o_val = breakdown_sb_windows(sb_dicts_val)
l_disc, ny_disc, o_disc = breakdown_sb_windows(sb_dicts_disc)

print(f"2020-2022 SB (N={len(sb_dicts_val)}): London (03:00-04:00 NY) = {l_val}, NY Morning (10:00-11:00 NY) = {ny_val}, Other = {o_val}")
print(f"2023-2025 SB (N={len(sb_dicts_disc)}): London (03:00-04:00 NY) = {l_disc}, NY Morning (10:00-11:00 NY) = {ny_disc}, Other = {o_disc}")

# Summary of windows
print("\nSilver Bullet Detector Configuration:")
print("  - LONDON_WINDOW: 03:00 to 04:00 NY (dt.time(3, 0) to dt.time(4, 0))")
print("  - NY_AM_WINDOW:  10:00 to 11:00 NY (dt.time(10, 0) to dt.time(11, 0))")
print("Live Engine Configuration:")
print("  - Strategy: JudasSwingStrategy (engine.strategy.judas_swing)")
print("  - Execution Window: 02:00 to 05:00 NY (London Open fakeout session)")
print("  - Asian Session:   18:00 to 00:00 NY (locked prior to 02:00 NY open)")
