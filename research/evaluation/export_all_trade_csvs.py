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
from research.evaluation.followup_analysis import _trade_to_dict
from research.evaluation.analyze_sb_unfilled_split import run_market_entry_silver_bullet
import bisect

NY_TZ = ZoneInfo("America/New_York")
out_dir = Path("research/output")
out_dir.mkdir(parents=True, exist_ok=True)
spec = InstrumentSpec("XAU_USD", -2, 2, 2, Decimal("0.01"))

runner_1x = BacktestRunner(base_spread=Decimal("0.20"), base_slippage=Decimal("0.05"))
runner_0x = BacktestRunner(base_spread=Decimal("0.0"), base_slippage=Decimal("0.0"), cost_multiplier=Decimal("0.0"))

bars_20_22 = load_all_m5_bars(years=["2020", "2021", "2022"])
val_bar_ts = [b.timestamp_utc for b in bars_20_22]

bars_23_25_raw = load_all_m5_bars(years=["2023", "2024", "2025"])
seen_23_25 = set()
bars_23_25 = []
for b in sorted(bars_23_25_raw, key=lambda x: x.timestamp_utc):
    if b.timestamp_utc not in seen_23_25:
        seen_23_25.add(b.timestamp_utc)
        bars_23_25.append(b)

all_bars = bars_20_22 + bars_23_25

# 1. Judas Live Core Class 2020-2022
strat_core = JudasSwingStrategy()
t_core, _ = runner_1x.run_judas_backtest(bars_20_22, judas_strategy=strat_core)
core_dicts = [_trade_to_dict(t) for t in t_core]
with open(out_dir / "judas_core_trades_2020_2022.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(core_dicts[0].keys()))
    w.writeheader(); w.writerows(core_dicts)

# 2. Judas V0 Baseline 2020-2022
strat_v0 = EnhancedJudasStrategy()
t_v0, _ = runner_1x.run_judas_backtest(bars_20_22, judas_strategy=strat_v0)
v0_dicts = [_trade_to_dict(t) for t in t_v0]
with open(out_dir / "judas_fade_trades_2020_2022.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(v0_dicts[0].keys()))
    w.writeheader(); w.writerows(v0_dicts)

# 3. Judas V10 Mirror 2020-2022 (exact headline mirror evaluated in followup_analysis.py)
from research.evaluation.recompute_nulls_v10 import mirror_dicts as v10_dicts
with open(out_dir / "judas_mirror_trades_2020_2022.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(v10_dicts[0].keys()))
    w.writeheader(); w.writerows(v10_dicts)

# 4. Silver Bullet Limit 2020-2022 & 2023-2025
strat_sb1, _ = load_silver_bullet_strategy(spec)
o_sb1 = strat_sb1.on_bar
def p_sb1(b):
    res = o_sb1(b); idx = strat_sb1._detector.bar_count
    if idx > 400: strat_sb1._detector.drop_levels(idx - 400)
    return res
strat_sb1.on_bar = p_sb1
t_sb_val, _ = runner_1x.run_silver_bullet_backtest(bars_20_22, strat_sb1)
sb_val_dicts = [_trade_to_dict(t) for t in t_sb_val]
with open(out_dir / "silver_bullet_trades_2020_2022.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(sb_val_dicts[0].keys()))
    w.writeheader(); w.writerows(sb_val_dicts)

strat_sb2, _ = load_silver_bullet_strategy(spec)
o_sb2 = strat_sb2.on_bar
def p_sb2(b):
    res = o_sb2(b); idx = strat_sb2._detector.bar_count
    if idx > 400: strat_sb2._detector.drop_levels(idx - 400)
    return res
strat_sb2.on_bar = p_sb2
t_sb_disc, _ = runner_1x.run_silver_bullet_backtest(bars_23_25, strat_sb2)
sb_disc_dicts = [_trade_to_dict(t) for t in t_sb_disc]
with open(out_dir / "silver_bullet_trades_2023_2025.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(sb_disc_dicts[0].keys()))
    w.writeheader(); w.writerows(sb_disc_dicts)

# 5. Silver Bullet Market-Entry Variant
def get_sb_mkt_dicts(bars):
    strat, _ = load_silver_bullet_strategy(spec)
    orig_f = strat.on_bar
    def p_f(b: Bar):
        res = orig_f(b); idx = strat._detector.bar_count
        if idx > 400: strat._detector.drop_levels(idx - 400)
        return res
    strat.on_bar = p_f
    active_t: SimulatedTrade | None = None
    trades: list[SimulatedTrade] = []
    trade_id = 0
    pending_mkt = None
    for idx, bar in enumerate(bars):
        bar_time = bar.timestamp_utc
        ny_time = bar_time.astimezone(NY_TZ)
        mid = bar.mid_ohlc
        if active_t is not None:
            stop_hit = (mid.low <= active_t.stop_loss) if active_t.side == "BUY" else (mid.high >= active_t.stop_loss)
            tp_hit = (mid.high >= active_t.take_profit) if active_t.side == "BUY" else (mid.low <= active_t.take_profit)
            dynamic_close = ny_time.time() >= dt.time(16, 45)
            if stop_hit and tp_hit:
                runner_1x._close_trade(active_t, exit_price=active_t.stop_loss, exit_time=bar_time, reason="STOP_LOSS (conflict)", is_stop=True)
                trades.append(active_t); active_t = None
            elif stop_hit:
                runner_1x._close_trade(active_t, exit_price=active_t.stop_loss, exit_time=bar_time, reason="STOP_LOSS", is_stop=True)
                trades.append(active_t); active_t = None
            elif tp_hit:
                runner_1x._close_trade(active_t, exit_price=active_t.take_profit, exit_time=bar_time, reason="TAKE_PROFIT", is_stop=False)
                trades.append(active_t); active_t = None
            elif dynamic_close:
                runner_1x._close_trade(active_t, exit_price=mid.close, exit_time=bar_time, reason="DYNAMIC_CLOSE_1645", is_stop=False)
                trades.append(active_t); active_t = None

        if pending_mkt is not None:
            p_side, p_sl, p_tp = pending_mkt
            pending_mkt = None
            if active_t is None and ny_time.time() < dt.time(16, 45):
                trade_id += 1
                entry_price = mid.open
                active_t = SimulatedTrade(
                    trade_id=f"sb-mkt-{trade_id:04d}", strategy="Silver Bullet (Market Entry)", side=p_side,
                    entry_time=bar_time, entry_price=entry_price, stop_loss=p_sl, take_profit=p_tp,
                    risk_dollars=abs(entry_price - p_sl), reward_dollars=abs(p_tp - entry_price),
                    spread_paid=runner_1x.effective_spread, in_sample=True,
                )
        intents = strat.on_bar(bar)
        for intent in intents:
            if type(intent).__name__ == "PlaceLimit":
                side_s = "BUY" if intent.side.value == "BUY" else "SELL"
                pending_mkt = (side_s, intent.stop_price, intent.target_price)
    return [_trade_to_dict(t) for t in trades]

sb_mkt_val_dicts = get_sb_mkt_dicts(bars_20_22)
with open(out_dir / "silver_bullet_market_trades_2020_2022.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(sb_mkt_val_dicts[0].keys()))
    w.writeheader(); w.writerows(sb_mkt_val_dicts)

sb_mkt_disc_dicts = get_sb_mkt_dicts(bars_23_25)
with open(out_dir / "silver_bullet_market_trades_2023_2025.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(sb_mkt_disc_dicts[0].keys()))
    w.writeheader(); w.writerows(sb_mkt_disc_dicts)

# 6. Yearly Fade Trades (2020-2025)
yearly_fade_all = []
yearly_mirror_all = []
for yr in [2020, 2021, 2022, 2023, 2024, 2025]:
    yr_bars = [b for b in all_bars if b.timestamp_utc.astimezone(NY_TZ).year == yr]
    yr_bar_ts = [b.timestamp_utc for b in yr_bars]
    t_f, _ = runner_1x.run_judas_backtest(yr_bars, judas_strategy=EnhancedJudasStrategy())
    for t in t_f:
        d = _trade_to_dict(t)
        d["year"] = yr
        yearly_fade_all.append(d)

    # Mirror for this year
    for t in t_f:
        m_side = "BUY" if t.side == "SELL" else "SELL"
        s_dist = abs(t.entry_price - t.stop_loss)
        t_dist = abs(t.entry_price - t.take_profit)
        r_mult = t_dist / s_dist if s_dist > 0 else Decimal("2.0")
        if m_side == "BUY":
            m_sl = t.entry_price - s_dist
            m_tp = t.entry_price + (r_mult * s_dist)
        else:
            m_sl = t.entry_price + s_dist
            m_tp = t.entry_price - (r_mult * s_dist)
        s_idx = bisect.bisect_left(yr_bar_ts, t.entry_time)
        exit_b = None; exit_p = None; exit_r = "TIMEOUT"; is_s = False
        for bi in range(s_idx, min(len(yr_bars), s_idx + 288)):
            b = yr_bars[bi]; mid = b.mid_ohlc
            hit_sl = (mid.low <= m_sl) if m_side == "BUY" else (mid.high >= m_sl)
            hit_tp = (mid.high >= m_tp) if m_side == "BUY" else (mid.low <= m_tp)
            if hit_sl and hit_tp: exit_b = b; exit_p = m_sl; exit_r = "STOP_LOSS (conflict)"; is_s = True; break
            elif hit_sl: exit_b = b; exit_p = m_sl; exit_r = "STOP_LOSS"; is_s = True; break
            elif hit_tp: exit_b = b; exit_p = m_tp; exit_r = "TAKE_PROFIT"; is_s = False; break
        if exit_b is None:
            lb = yr_bars[min(len(yr_bars)-1, s_idx+287)]
            exit_b = lb; exit_p = lb.mid_ohlc.close; exit_r = "TIMEOUT"; is_s = False
        tr = SimulatedTrade(
            trade_id=t.trade_id, strategy="Mirror Judas Breakout", side=m_side, entry_time=t.entry_time,
            entry_price=t.entry_price, stop_loss=m_sl, take_profit=m_tp, risk_dollars=s_dist, reward_dollars=t_dist,
        )
        runner_1x._close_trade(tr, exit_price=exit_p, exit_time=exit_b.timestamp_utc, reason=exit_r, is_stop=is_s)
        d = _trade_to_dict(tr)
        d["year"] = yr
        yearly_mirror_all.append(d)

with open(out_dir / "yearly_fade_trades_2020_2025.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(yearly_fade_all[0].keys()))
    w.writeheader(); w.writerows(yearly_fade_all)

with open(out_dir / "yearly_mirror_trades_2020_2025.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(yearly_mirror_all[0].keys()))
    w.writeheader(); w.writerows(yearly_mirror_all)

print("ALL trade CSVs successfully written to research/output/.")
