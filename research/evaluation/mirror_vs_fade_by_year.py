import sys
sys.path.insert(0, "engine/src")
sys.path.insert(0, ".")

import bisect
import datetime as dt
from decimal import Decimal
import json
import numpy as np
from zoneinfo import ZoneInfo

from engine.market.models import Bar
from research.data.loader import load_all_m5_bars
from research.backtest.engine import BacktestRunner, SimulatedTrade
from research.strategies.enhanced_judas import EnhancedJudasStrategy
from research.metrics.trade_stats import compute_trade_metrics
from research.evaluation.followup_analysis import _trade_to_dict

NY_TZ = ZoneInfo("America/New_York")
runner = BacktestRunner(base_spread=Decimal("0.20"), base_slippage=Decimal("0.05"))

bars_val = load_all_m5_bars(years=["2020", "2021", "2022"])
bars_disc_raw = load_all_m5_bars(years=["2023", "2024", "2025"])
seen_23_25 = set()
bars_disc = []
for b in sorted(bars_disc_raw, key=lambda x: x.timestamp_utc):
    if b.timestamp_utc not in seen_23_25:
        seen_23_25.add(b.timestamp_utc)
        bars_disc.append(b)

all_bars = bars_val + bars_disc

yearly_results = []
years = [2020, 2021, 2022, 2023, 2024, 2025]

for yr in years:
    yr_bars = [b for b in all_bars if b.timestamp_utc.astimezone(NY_TZ).year == yr]
    yr_bar_ts = [b.timestamp_utc for b in yr_bars]

    # 1. Fade (Standard Judas)
    strat_fade = EnhancedJudasStrategy()
    trades_fade, _ = runner.run_judas_backtest(yr_bars, judas_strategy=strat_fade)
    fade_dicts = [_trade_to_dict(t) for t in trades_fade]
    m_fade = compute_trade_metrics(fade_dicts, seed=42)

    # 2. Mirror (Flipped Direction)
    mirror_simulated_trades = []
    trade_id = 0
    for t in trades_fade:
        trade_id += 1
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

        start_idx = bisect.bisect_left(yr_bar_ts, t.entry_time)
        trade_obj = SimulatedTrade(
            trade_id=f"mirror-{yr}-{trade_id:04d}",
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

        for b_idx in range(start_idx, min(len(yr_bars), start_idx + 288)):
            b = yr_bars[b_idx]
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
            last_b = yr_bars[min(len(yr_bars) - 1, start_idx + 287)]
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

    yearly_results.append({
        "year": str(yr),
        "n_trades": m_fade["n_trades"],
        "fade_wr": round(m_fade["win_rate"] * 100, 2),
        "fade_exp_r": round(m_fade["expectancy"], 4),
        "fade_pf": round(m_fade["profit_factor"], 2),
        "mirror_wr": round(m_mirror["win_rate"] * 100, 2),
        "mirror_exp_r": round(m_mirror["expectancy"], 4),
        "mirror_pf": round(m_mirror["profit_factor"], 2),
        "diff_r": round(m_mirror["expectancy"] - m_fade["expectancy"], 4),
    })

print(json.dumps(yearly_results, indent=2))
