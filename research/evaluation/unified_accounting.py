"""Unified accounting evaluation and export script.

Enforces ONE cost function across all strategies, baselines, and null models:
- Half-spread on entry ($0.10) AND exit ($0.10) = $0.20 per round-trip.
- Slippage on stops ($0.05) if stop-loss is triggered, $0.00 otherwise.
- Cost drag in R = ($0.20 + ($0.05 if is_stop else $0.00)) / Stop Distance.
- Gross R = raw_pnl / Stop Distance.
- Net R = Gross R - Cost Drag (R).
"""

from __future__ import annotations

import bisect
import csv
import datetime as dt
from decimal import Decimal
import json
from pathlib import Path
import sys
from typing import Any
from zoneinfo import ZoneInfo
import numpy as np

# Ensure immediate unbuffered output
sys.stdout.reconfigure(line_buffering=True)

sys.path.insert(0, "engine/src")
sys.path.insert(0, ".")

from engine.market.instrument import InstrumentSpec
from engine.market.models import Bar
from engine.strategy.judas_swing import JudasSwingStrategy
from research.data.loader import load_all_m5_bars
from research.backtest.engine import BacktestRunner, SimulatedTrade
from research.strategies.enhanced_judas import EnhancedJudasStrategy
from research.adapters.silver_bullet_adapter import load_silver_bullet_strategy

NY_TZ = ZoneInfo("America/New_York")
out_dir = Path("research/output")
out_dir.mkdir(parents=True, exist_ok=True)
spec = InstrumentSpec("XAU_USD", -2, 2, 2, Decimal("0.01"))


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


def trade_to_dict(t: SimulatedTrade) -> dict[str, Any]:
    """Serializes a SimulatedTrade to dict."""
    return {
        "trade_id": t.trade_id,
        "strategy": t.strategy,
        "side": t.side,
        "entry_time": t.entry_time.isoformat(),
        "entry_price": str(t.entry_price),
        "stop_loss": str(t.stop_loss),
        "take_profit": str(t.take_profit),
        "exit_time": t.exit_time.isoformat() if t.exit_time else "",
        "exit_price": str(t.exit_price) if t.exit_price else "",
        "pnl_dollars": str(t.pnl_dollars),
        "r_multiple": float(t.r_multiple),
        "status": t.status,
        "exit_reason": t.exit_reason,
        "spread": float(t.spread_paid),
        "slippage": float(t.slippage_paid),
        "news_filtered": t.news_filtered,
        "in_sample": t.in_sample,
    }


def main() -> None:
    print("Loading bar datasets...")
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

    runner_0x = BacktestRunner(base_spread=Decimal("0.0"), base_slippage=Decimal("0.0"), cost_multiplier=Decimal("0.0"))

    # -------------------------------------------------------------------------
    # 1. Judas Fade (V0 Baseline 2020-2022)
    # -------------------------------------------------------------------------
    print("Evaluating Judas Fade (V0)...")
    trades_fade_raw, _ = runner_0x.run_judas_backtest(bars_20_22, judas_strategy=EnhancedJudasStrategy())
    fade_rows = []
    for t in trades_fade_raw:
        d = trade_to_dict(t)
        pnl = float(t.pnl_dollars)
        s_dist = float(t.risk_dollars)
        is_stop = "STOP" in t.exit_reason
        g_r, n_r, drag = compute_trade_r(pnl, s_dist, is_stop)
        d["gross_r"] = round(g_r, 4)
        d["net_r"] = round(n_r, 4)
        d["r_multiple"] = round(n_r, 4)
        d["cost_drag_r"] = round(drag, 4)
        fade_rows.append(d)

    with open(out_dir / "judas_fade_trades_2020_2022.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(fade_rows[0].keys()))
        w.writeheader(); w.writerows(fade_rows)

    # -------------------------------------------------------------------------
    # 2. Judas Mirror (V10 2020-2022)
    # -------------------------------------------------------------------------
    print("Evaluating Judas Mirror (V10)...")
    mirror_rows = []
    for t in trades_fade_raw:
        m_side = "BUY" if t.side == "SELL" else "SELL"
        entry_p = t.entry_price
        s_dist = abs(t.entry_price - t.stop_loss)
        t_dist = abs(t.entry_price - t.take_profit)
        r_mult = t_dist / s_dist if s_dist > 0 else Decimal("2.0")

        if m_side == "BUY":
            m_sl = entry_p - s_dist
            m_tp = entry_p + (r_mult * s_dist)
        else:
            m_sl = entry_p + s_dist
            m_tp = entry_p - (r_mult * s_dist)

        start_idx = bisect.bisect_left(val_bar_ts, t.entry_time)
        exit_bar = None; exit_p = None; exit_reason = "TIMEOUT"; is_stop = False
        for b_idx in range(start_idx, min(len(bars_20_22), start_idx + 288)):
            b = bars_20_22[b_idx]; mid = b.mid_ohlc
            hit_sl = (mid.low <= m_sl) if m_side == "BUY" else (mid.high >= m_sl)
            hit_tp = (mid.high >= m_tp) if m_side == "BUY" else (mid.low <= m_tp)
            if hit_sl and hit_tp:
                exit_bar = b; exit_p = m_sl; exit_reason = "STOP_LOSS (conflict)"; is_stop = True; break
            elif hit_sl:
                exit_bar = b; exit_p = m_sl; exit_reason = "STOP_LOSS"; is_stop = True; break
            elif hit_tp:
                exit_bar = b; exit_p = m_tp; exit_reason = "TAKE_PROFIT"; is_stop = False; break
        if exit_bar is None:
            last_b = bars_20_22[min(len(bars_20_22) - 1, start_idx + 287)]
            exit_bar = last_b; exit_p = last_b.mid_ohlc.close; exit_reason = "TIMEOUT"; is_stop = False

        raw_diff = float((exit_p - entry_p) if m_side == "BUY" else (entry_p - exit_p))
        g_r, n_r, drag = compute_trade_r(raw_diff, float(s_dist), is_stop)

        d = {
            "trade_id": t.trade_id, "strategy": "Mirror Judas Breakout", "side": m_side,
            "entry_time": t.entry_time.isoformat(), "entry_price": str(entry_p),
            "stop_loss": str(m_sl), "take_profit": str(m_tp),
            "exit_time": exit_bar.timestamp_utc.isoformat(), "exit_price": str(exit_p),
            "pnl_dollars": str(round(raw_diff, 4)), "r_multiple": round(n_r, 4),
            "gross_r": round(g_r, 4), "net_r": round(n_r, 4), "cost_drag_r": round(drag, 4),
            "status": "CLOSED", "exit_reason": exit_reason, "spread": 0.20,
            "slippage": 0.05 if is_stop else 0.0, "news_filtered": False, "in_sample": True,
        }
        mirror_rows.append(d)

    with open(out_dir / "judas_mirror_trades_2020_2022.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(mirror_rows[0].keys()))
        w.writeheader(); w.writerows(mirror_rows)

    # -------------------------------------------------------------------------
    # 3. Judas Live Core Class 2020-2022
    # -------------------------------------------------------------------------
    print("Evaluating Judas Live Core...")
    trades_core_raw, _ = runner_0x.run_judas_backtest(bars_20_22, judas_strategy=JudasSwingStrategy())
    core_rows = []
    for t in trades_core_raw:
        d = trade_to_dict(t)
        pnl = float(t.pnl_dollars)
        s_dist = float(t.risk_dollars)
        is_stop = "STOP" in t.exit_reason
        g_r, n_r, drag = compute_trade_r(pnl, s_dist, is_stop)
        d["gross_r"] = round(g_r, 4)
        d["net_r"] = round(n_r, 4)
        d["r_multiple"] = round(n_r, 4)
        d["cost_drag_r"] = round(drag, 4)
        core_rows.append(d)

    with open(out_dir / "judas_core_trades_2020_2022.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(core_rows[0].keys()))
        w.writeheader(); w.writerows(core_rows)

    # -------------------------------------------------------------------------
    # 4. Null Model A & Null Model B with Unified Cost Function
    # -------------------------------------------------------------------------
    print("Evaluating Null Models A & B with unified cost function...")
    highs = np.array([float(b.mid_ohlc.high) for b in bars_20_22], dtype=np.float64)
    lows = np.array([float(b.mid_ohlc.low) for b in bars_20_22], dtype=np.float64)
    closes = np.array([float(b.mid_ohlc.close) for b in bars_20_22], dtype=np.float64)
    bar_ts_map = {b.timestamp_utc: idx for idx, b in enumerate(bars_20_22)}

    v0_indices = [bar_ts_map[dt.datetime.fromisoformat(t["entry_time"])] for t in fade_rows if dt.datetime.fromisoformat(t["entry_time"]) in bar_ts_map]
    v0_stops = [float(abs(Decimal(t["entry_price"]) - Decimal(t["stop_loss"]))) for t in fade_rows]
    v0_targets = [float(abs(Decimal(t["entry_price"]) - Decimal(t["take_profit"])) / abs(Decimal(t["entry_price"]) - Decimal(t["stop_loss"]))) for t in fade_rows]

    window_bar_indices = [
        idx for idx, b in enumerate(bars_20_22)
        if 2 <= b.timestamp_utc.astimezone(NY_TZ).hour < 5 and idx < len(bars_20_22) - 288
    ]

    def eval_null_path_both(start_idx: int, side: str, s_dist: float, r_tgt: float) -> tuple[float, float]:
        end_idx = min(len(bars_20_22), start_idx + 288)
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
            raw_pnl = -s_dist
            is_stop = True
        elif first_tp < first_sl and first_tp != 999999:
            raw_pnl = r_tgt * s_dist
            is_stop = False
        else:
            last_c = closes[end_idx - 1]
            raw_pnl = (last_c - entry) if side == "BUY" else (entry - last_c)
            is_stop = False

        g_r, n_r, _ = compute_trade_r(raw_pnl, s_dist, is_stop)
        return g_r, n_r

    rng = np.random.default_rng(42)
    n_iter = 1_000
    null_a_gross_means, null_a_net_means = [], []
    null_b_gross_means, null_b_net_means = [], []

    n_eval = len(v0_indices)
    for _ in range(n_iter):
        s_idx = rng.choice(window_bar_indices, size=n_eval, replace=True)
        s_stops = rng.choice(v0_stops, size=n_eval, replace=True)
        s_tgts = rng.choice(v0_targets, size=n_eval, replace=True)
        s_sides = rng.choice(["BUY", "SELL"], size=n_eval)
        res_a = [eval_null_path_both(i + 1, side, sd, rt) for i, side, sd, rt in zip(s_idx, s_sides, s_stops, s_tgts)]
        null_a_gross_means.append(float(np.mean([x[0] for x in res_a])))
        null_a_net_means.append(float(np.mean([x[1] for x in res_a])))

        res_b = [eval_null_path_both(i, side, sd, rt) for i, side, sd, rt in zip(v0_indices, s_sides, v0_stops, v0_targets)]
        null_b_gross_means.append(float(np.mean([x[0] for x in res_b])))
        null_b_net_means.append(float(np.mean([x[1] for x in res_b])))

    # -------------------------------------------------------------------------
    # 5. Silver Bullet Limit (2020-2022 & 2023-2025)
    # -------------------------------------------------------------------------
    print("Evaluating Silver Bullet Limit...")
    def get_sb_limit_rows(bars: list[Bar]) -> list[dict[str, Any]]:
        strat, _ = load_silver_bullet_strategy(spec)
        orig_f = strat.on_bar
        def p_f(b: Bar):
            res = orig_f(b); idx = strat._detector.bar_count
            if idx > 400: strat._detector.drop_levels(idx - 400)
            return res
        strat.on_bar = p_f
        trades, _ = runner_0x.run_silver_bullet_backtest(bars, strat)
        rows = []
        for t in trades:
            d = trade_to_dict(t)
            pnl = float(t.pnl_dollars)
            s_dist = float(t.risk_dollars)
            is_stop = "STOP" in t.exit_reason
            g_r, n_r, drag = compute_trade_r(pnl, s_dist, is_stop)
            d["gross_r"] = round(g_r, 4)
            d["net_r"] = round(n_r, 4)
            d["r_multiple"] = round(n_r, 4)
            d["cost_drag_r"] = round(drag, 4)
            rows.append(d)
        return rows

    sb_lim_val_rows = get_sb_limit_rows(bars_20_22)
    with open(out_dir / "silver_bullet_trades_2020_2022.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(sb_lim_val_rows[0].keys()))
        w.writeheader(); w.writerows(sb_lim_val_rows)

    sb_lim_disc_rows = get_sb_limit_rows(bars_23_25)
    with open(out_dir / "silver_bullet_trades_2023_2025.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(sb_lim_disc_rows[0].keys()))
        w.writeheader(); w.writerows(sb_lim_disc_rows)

    # -------------------------------------------------------------------------
    # 6. Silver Bullet Market (2020-2022 & 2023-2025)
    # -------------------------------------------------------------------------
    print("Evaluating Silver Bullet Market...")
    def run_sb_market_simulation(bars: list[Bar]) -> list[dict[str, Any]]:
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
                    runner_0x._close_trade(active_t, exit_price=active_t.stop_loss, exit_time=bar_time, reason="STOP_LOSS (conflict)", is_stop=True)
                    trades.append(active_t); active_t = None
                elif stop_hit:
                    runner_0x._close_trade(active_t, exit_price=active_t.stop_loss, exit_time=bar_time, reason="STOP_LOSS", is_stop=True)
                    trades.append(active_t); active_t = None
                elif tp_hit:
                    runner_0x._close_trade(active_t, exit_price=active_t.take_profit, exit_time=bar_time, reason="TAKE_PROFIT", is_stop=False)
                    trades.append(active_t); active_t = None
                elif dynamic_close:
                    runner_0x._close_trade(active_t, exit_price=mid.close, exit_time=bar_time, reason="DYNAMIC_CLOSE_1645", is_stop=False)
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
                        spread_paid=Decimal("0.0"), in_sample=True,
                    )
            intents = strat.on_bar(bar)
            for intent in intents:
                if type(intent).__name__ == "PlaceLimit":
                    side_s = "BUY" if intent.side.value == "BUY" else "SELL"
                    pending_mkt = (side_s, intent.stop_price, intent.target_price)

        rows = []
        for t in trades:
            d = trade_to_dict(t)
            raw_pnl = float(t.pnl_dollars)
            s_dist = float(abs(Decimal(d["entry_price"]) - Decimal(d["stop_loss"])))
            is_stop = "STOP" in d["exit_reason"]
            g_r, n_r, drag = compute_trade_r(raw_pnl, s_dist, is_stop)
            d["gross_r"] = round(g_r, 4)
            d["net_r"] = round(n_r, 4)
            d["r_multiple"] = round(n_r, 4)
            d["cost_drag_r"] = round(drag, 4)
            rows.append(d)
        return rows

    sb_mkt_val_rows = run_sb_market_simulation(bars_20_22)
    with open(out_dir / "silver_bullet_market_trades_2020_2022.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(sb_mkt_val_rows[0].keys()))
        w.writeheader(); w.writerows(sb_mkt_val_rows)

    sb_mkt_disc_rows = run_sb_market_simulation(bars_23_25)
    with open(out_dir / "silver_bullet_market_trades_2023_2025.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(sb_mkt_disc_rows[0].keys()))
        w.writeheader(); w.writerows(sb_mkt_disc_rows)

    # -------------------------------------------------------------------------
    # 7. Yearly Fade & Mirror (2020-2025)
    # -------------------------------------------------------------------------
    print("Evaluating Yearly Fade & Mirror (2020-2025)...")
    yearly_fade_rows, yearly_mirror_rows = [], []
    for yr in [2020, 2021, 2022, 2023, 2024, 2025]:
        yr_bars = [b for b in all_bars if b.timestamp_utc.astimezone(NY_TZ).year == yr]
        yr_bar_ts = [b.timestamp_utc for b in yr_bars]
        t_f_raw, _ = runner_0x.run_judas_backtest(yr_bars, judas_strategy=EnhancedJudasStrategy())
        for t in t_f_raw:
            d = trade_to_dict(t)
            pnl = float(t.pnl_dollars)
            s_dist = float(t.risk_dollars)
            is_stop = "STOP" in t.exit_reason
            g_r, n_r, drag = compute_trade_r(pnl, s_dist, is_stop)
            d["year"] = yr
            d["gross_r"] = round(g_r, 4); d["net_r"] = round(n_r, 4); d["r_multiple"] = round(n_r, 4); d["cost_drag_r"] = round(drag, 4)
            yearly_fade_rows.append(d)

        # Mirror
        for t in t_f_raw:
            m_side = "BUY" if t.side == "SELL" else "SELL"
            entry_p = t.entry_price
            s_dist = abs(t.entry_price - t.stop_loss)
            t_dist = abs(t.entry_price - t.take_profit)
            r_mult = t_dist / s_dist if s_dist > 0 else Decimal("2.0")
            if m_side == "BUY": m_sl = entry_p - s_dist; m_tp = entry_p + (r_mult * s_dist)
            else: m_sl = entry_p + s_dist; m_tp = entry_p - (r_mult * s_dist)
            s_idx = bisect.bisect_left(yr_bar_ts, t.entry_time)
            exit_bar = None; exit_p = None; exit_reason = "TIMEOUT"; is_stop = False
            for b_idx in range(s_idx, min(len(yr_bars), s_idx + 288)):
                b = yr_bars[b_idx]; mid = b.mid_ohlc
                hit_sl = (mid.low <= m_sl) if m_side == "BUY" else (mid.high >= m_sl)
                hit_tp = (mid.high >= m_tp) if m_side == "BUY" else (mid.low <= m_tp)
                if hit_sl and hit_tp: exit_bar = b; exit_p = m_sl; exit_reason = "STOP_LOSS (conflict)"; is_stop = True; break
                elif hit_sl: exit_bar = b; exit_p = m_sl; exit_reason = "STOP_LOSS"; is_stop = True; break
                elif hit_tp: exit_bar = b; exit_p = m_tp; exit_reason = "TAKE_PROFIT"; is_stop = False; break
            if exit_bar is None:
                last_b = yr_bars[min(len(yr_bars) - 1, s_idx + 287)]
                exit_bar = last_b; exit_p = last_b.mid_ohlc.close; exit_reason = "TIMEOUT"; is_stop = False

            raw_diff = float((exit_p - entry_p) if m_side == "BUY" else (entry_p - exit_p))
            g_r, n_r, drag = compute_trade_r(raw_diff, float(s_dist), is_stop)
            d_m = {
                "trade_id": t.trade_id, "strategy": "Mirror Judas Breakout", "side": m_side, "year": yr,
                "entry_time": t.entry_time.isoformat(), "entry_price": str(entry_p), "stop_loss": str(m_sl), "take_profit": str(m_tp),
                "exit_time": exit_bar.timestamp_utc.isoformat(), "exit_price": str(exit_p), "pnl_dollars": str(round(raw_diff, 4)),
                "r_multiple": round(n_r, 4), "gross_r": round(g_r, 4), "net_r": round(n_r, 4), "cost_drag_r": round(drag, 4),
                "status": "CLOSED", "exit_reason": exit_reason, "spread": 0.20, "slippage": 0.05 if is_stop else 0.0,
                "news_filtered": False, "in_sample": True,
            }
            yearly_mirror_rows.append(d_m)

    with open(out_dir / "yearly_fade_trades_2020_2025.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(yearly_fade_rows[0].keys()))
        w.writeheader(); w.writerows(yearly_fade_rows)

    with open(out_dir / "yearly_mirror_trades_2020_2025.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(yearly_mirror_rows[0].keys()))
        w.writeheader(); w.writerows(yearly_mirror_rows)

    # -------------------------------------------------------------------------
    # 8. Compute Statistics & Percentiles
    # -------------------------------------------------------------------------
    fade_gross_exp = float(np.mean([float(r["gross_r"]) for r in fade_rows]))
    fade_net_exp = float(np.mean([float(r["net_r"]) for r in fade_rows]))
    mirror_gross_exp = float(np.mean([float(r["gross_r"]) for r in mirror_rows]))
    mirror_net_exp = float(np.mean([float(r["net_r"]) for r in mirror_rows]))

    avg_fm_gross = (fade_gross_exp + mirror_gross_exp) / 2.0
    avg_fm_net = (fade_net_exp + mirror_net_exp) / 2.0

    null_b_g_mean = float(np.mean(null_b_gross_means))
    null_b_n_mean = float(np.mean(null_b_net_means))
    null_a_g_mean = float(np.mean(null_a_gross_means))
    null_a_n_mean = float(np.mean(null_a_net_means))

    pct_fade_null_b = float(np.mean(np.array(null_b_net_means) <= fade_net_exp) * 100.0)
    pct_mirror_null_b = float(np.mean(np.array(null_b_net_means) <= mirror_net_exp) * 100.0)

    # Save null summary
    null_summary = {
        "null_a_gross_mean": round(null_a_g_mean, 4),
        "null_a_net_mean": round(null_a_n_mean, 4),
        "null_b_gross_mean": round(null_b_g_mean, 4),
        "null_b_net_mean": round(null_b_n_mean, 4),
        "avg_fade_mirror_gross": round(avg_fm_gross, 4),
        "avg_fade_mirror_net": round(avg_fm_net, 4),
        "pct_fade_null_b": round(pct_fade_null_b, 2),
        "pct_mirror_null_b": round(pct_mirror_null_b, 2),
    }
    with open(out_dir / "null_simulations_summary.json", "w", encoding="utf-8") as f:
        json.dump(null_summary, f, indent=2)

    # Print Summary
    print("\n" + "=" * 95)
    print("UNIFIED ACCOUNTING SUMMARY (GROSS VS NET)")
    print("=" * 95)
    def p_stat(label: str, rows: list[dict[str, Any]]) -> None:
        g = [float(r["gross_r"]) for r in rows]
        n = [float(r["net_r"]) for r in rows]
        drag = [float(r["cost_drag_r"]) for r in rows]
        wr_g = np.mean(np.array(g) > 0) * 100
        wr_n = np.mean(np.array(n) > 0) * 100
        print(f"{label:<25} | N={len(rows):<4} | Gross Exp={np.mean(g):>+7.4f}R (WR {wr_g:>5.1f}%) | Net Exp={np.mean(n):>+7.4f}R (WR {wr_n:>5.1f}%) | Drag={np.mean(drag):.4f}R")

    p_stat("Judas Fade (V0)", fade_rows)
    p_stat("Judas Mirror (V10)", mirror_rows)
    p_stat("Judas Live Core", core_rows)
    p_stat("SB Limit 2020-2022", sb_lim_val_rows)
    p_stat("SB Limit 2023-2025", sb_lim_disc_rows)
    p_stat("SB Market 2020-2022", sb_mkt_val_rows)
    p_stat("SB Market 2023-2025", sb_mkt_disc_rows)

    print("-" * 95)
    print(f"Fade & Mirror Midpoint:  Gross = {avg_fm_gross:+.4f}R | Net = {avg_fm_net:+.4f}R")
    print(f"Null Model B (1,000 iter): Gross = {null_b_g_mean:+.4f}R | Net = {null_b_n_mean:+.4f}R")
    print(f"Null Model A (1,000 iter): Gross = {null_a_g_mean:+.4f}R | Net = {null_a_n_mean:+.4f}R")
    print(f"Percentile of Net Fade ({fade_net_exp:+.4f}R) within Net Null B:   {pct_fade_null_b:.1f}% (p = {1.0 - pct_fade_null_b/100:.4f})")
    print(f"Percentile of Net Mirror ({mirror_net_exp:+.4f}R) within Net Null B: {pct_mirror_null_b:.1f}% (p = {1.0 - pct_mirror_null_b/100:.4f})")
    print("=" * 95)


if __name__ == "__main__":
    main()
