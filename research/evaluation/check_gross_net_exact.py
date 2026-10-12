import sys
sys.path.insert(0, "engine/src")
sys.path.insert(0, ".")

from decimal import Decimal
import numpy as np
from research.data.loader import load_all_m5_bars
from research.backtest.engine import BacktestRunner, SimulatedTrade
from research.strategies.enhanced_judas import EnhancedJudasStrategy
from research.metrics.trade_stats import compute_trade_metrics
from research.evaluation.followup_analysis import _trade_to_dict
import bisect

bars_val = load_all_m5_bars(years=["2020", "2021", "2022"])
val_bar_ts = [b.timestamp_utc for b in bars_val]

runner_0x = BacktestRunner(base_spread=Decimal("0.0"), base_slippage=Decimal("0.0"), cost_multiplier=Decimal("0.0"))
runner_1x = BacktestRunner(base_spread=Decimal("0.20"), base_slippage=Decimal("0.05"), cost_multiplier=Decimal("1.0"))

trades_fade_0x, _ = runner_0x.run_judas_backtest(bars_val, judas_strategy=EnhancedJudasStrategy())
fade_0x_dicts = [_trade_to_dict(t) for t in trades_fade_0x]
m_fade_0x = compute_trade_metrics(fade_0x_dicts, seed=42)

trades_fade_1x, _ = runner_1x.run_judas_backtest(bars_val, judas_strategy=EnhancedJudasStrategy())
fade_1x_dicts = [_trade_to_dict(t) for t in trades_fade_1x]
m_fade_1x = compute_trade_metrics(fade_1x_dicts, seed=42)

# Now build Mirror trades:
# 1. Mirror 0x:
# 2. Mirror 1x with EXACT same cost model as runner_1x:
# Runner 1x shifts entry price by +0.10 for BUY, -0.10 for SELL, and subtracts 0.05 slippage on stop-out.
mirror_0x_trades = []
mirror_1x_trades = []

spread_cost = Decimal("0.10") # half spread
slippage_cost = Decimal("0.05")

for t in trades_fade_0x:
    mirror_side = "BUY" if t.side == "SELL" else "SELL"
    # Setup raw entry price (the bar close)
    # Note: t was run at 0x so t.entry_price is the exact unshifted bar close
    raw_close = t.entry_price
    stop_dist = abs(t.entry_price - t.stop_loss)
    target_dist = abs(t.entry_price - t.take_profit)
    r_mult = target_dist / stop_dist if stop_dist > 0 else Decimal("2.0")

    # Mirror stops and targets around raw_close
    if mirror_side == "BUY":
        mirror_sl = raw_close - stop_dist
        mirror_tp = raw_close + (r_mult * stop_dist)
        entry_1x = raw_close + spread_cost # adverse entry shift
    else:
        mirror_sl = raw_close + stop_dist
        mirror_tp = raw_close - (r_mult * stop_dist)
        entry_1x = raw_close - spread_cost # adverse entry shift

    start_idx = bisect.bisect_left(val_bar_ts, t.entry_time)

    # Simulate path
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

    # Trade 0x
    t_0x = SimulatedTrade(
        trade_id=t.trade_id, strategy="Mirror 0x", side=mirror_side, entry_time=t.entry_time,
        entry_price=raw_close, stop_loss=mirror_sl, take_profit=mirror_tp,
        risk_dollars=stop_dist, reward_dollars=target_dist,
    )
    runner_0x._close_trade(t_0x, exit_price=exit_p, exit_time=exit_bar.timestamp_utc, reason=exit_reason, is_stop=is_stop)
    mirror_0x_trades.append(t_0x)

    # Trade 1x (with exact adverse entry and stop slippage)
    t_1x = SimulatedTrade(
        trade_id=t.trade_id, strategy="Mirror 1x", side=mirror_side, entry_time=t.entry_time,
        entry_price=entry_1x, stop_loss=mirror_sl, take_profit=mirror_tp,
        risk_dollars=stop_dist, reward_dollars=target_dist, spread_paid=Decimal("0.20")
    )
    runner_1x._close_trade(t_1x, exit_price=exit_p, exit_time=exit_bar.timestamp_utc, reason=exit_reason, is_stop=is_stop)
    mirror_1x_trades.append(t_1x)

m_m_0x = compute_trade_metrics([_trade_to_dict(t) for t in mirror_0x_trades], seed=42)
m_m_1x = compute_trade_metrics([_trade_to_dict(t) for t in mirror_1x_trades], seed=42)

print(f"Fade (V0):     Gross (0x) = {m_fade_0x['expectancy']:+.4f}R | Net (1x) = {m_fade_1x['expectancy']:+.4f}R | Drag = {m_fade_0x['expectancy'] - m_fade_1x['expectancy']:.4f}R")
print(f"Mirror (V10):  Gross (0x) = {m_m_0x['expectancy']:+.4f}R | Net (1x) = {m_m_1x['expectancy']:+.4f}R | Drag = {m_m_0x['expectancy'] - m_m_1x['expectancy']:.4f}R")
avg_0x = (m_fade_0x['expectancy'] + m_m_0x['expectancy']) / 2.0
avg_1x = (m_fade_1x['expectancy'] + m_m_1x['expectancy']) / 2.0
print(f"Avg(Fade,Mirror): Gross = {avg_0x:+.4f}R | Net = {avg_1x:+.4f}R")
