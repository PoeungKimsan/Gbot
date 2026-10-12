"""Step 2: Silver Bullet across instruments evaluation.

Runs the UNCHANGED Silver Bullet detector (both limit and market-entry) on:
- XAUUSD
- EURUSD
- GBPUSD
- USDJPY
- ES (US Index futures)

Applies realistic per-instrument costs:
- XAUUSD: spread 0.20, stop slippage 0.05
- EURUSD: spread 0.00010, stop slippage 0.00002
- GBPUSD: spread 0.00012, stop slippage 0.00003
- USDJPY: spread 0.012, stop slippage 0.003
- ES: spread 0.50, stop slippage 0.25

Reports:
- Per-instrument N, win rate, gross and net expectancy, cost drag
- Pooled N, pooled gross and net expectancy
- Date-clustered bootstrap 95% CI (resampling calendar dates)
"""

from __future__ import annotations

import csv
import datetime as dt
from decimal import Decimal
import json
from pathlib import Path
import pickle
import sys
from typing import Any
from zoneinfo import ZoneInfo
import numpy as np
import pyarrow.parquet as pq
import pyarrow.compute as pc

# Force line buffering
sys.stdout.reconfigure(line_buffering=True)

sys.path.insert(0, "engine/src")
sys.path.insert(0, ".")

from engine.market.instrument import InstrumentSpec
from engine.market.models import Bar, OHLC, Timeframe
from research.adapters.silver_bullet_adapter import load_silver_bullet_strategy

NY_TZ = ZoneInfo("America/New_York")
UTC_TZ = dt.UTC

INSTRUMENT_SPECS = {
    "XAUUSD": InstrumentSpec("XAU_USD", -2, 2, 2, Decimal("0.01")),
    "EURUSD": InstrumentSpec("EUR_USD", -4, 5, 0, Decimal("1")),
    "GBPUSD": InstrumentSpec("GBP_USD", -4, 5, 0, Decimal("1")),
    "USDJPY": InstrumentSpec("USD_JPY", -2, 3, 0, Decimal("1")),
    "ES": InstrumentSpec("ES", 0, 2, 0, Decimal("1")),
}

INSTRUMENT_COSTS = {
    "XAUUSD": {"spread": 0.20, "slippage": 0.05},
    "EURUSD": {"spread": 0.00010, "slippage": 0.00002},
    "GBPUSD": {"spread": 0.00012, "slippage": 0.00003},
    "USDJPY": {"spread": 0.012, "slippage": 0.003},
    "ES": {"spread": 0.50, "slippage": 0.25},
}


def load_parquet_bars(file_path: str, symbol: str) -> list[Bar]:
    table = pq.read_table(file_path)
    mask = pc.equal(table["symbol"], symbol)
    sub = table.filter(mask).sort_by([("date", "ascending")]).to_pydict()
    
    bars: list[Bar] = []
    n = len(sub["date"])
    for i in range(n):
        dt_ny = sub["date"][i].replace(tzinfo=NY_TZ)
        dt_utc = dt_ny.astimezone(UTC_TZ)
        ohlc = OHLC(
            open=Decimal(str(sub["open"][i])),
            high=Decimal(str(sub["high"][i])),
            low=Decimal(str(sub["low"][i])),
            close=Decimal(str(sub["close"][i])),
        )
        b = Bar(
            timeframe=Timeframe.M5,
            timestamp_utc=dt_utc,
            complete=True,
            volume=int(sub["volume"][i]),
            bid_ohlc=ohlc,
            ask_ohlc=ohlc,
        )
        bars.append(b)
    return bars


def load_xauusd_bars() -> list[Bar]:
    # Load cached M5 bars for 2021-2024
    with open("research/data/cache/m5_bars_2020_2021_2022.pkl", "rb") as f:
        bars_1 = pickle.load(f)
    with open("research/data/cache/m5_bars_2023_2024_2025.pkl", "rb") as f:
        bars_2 = pickle.load(f)
    
    seen = set()
    combined = []
    min_date = dt.datetime(2021, 8, 1, 0, 0, tzinfo=UTC_TZ)
    max_date = dt.datetime(2024, 10, 2, 0, 0, tzinfo=UTC_TZ)
    for b in sorted(bars_1 + bars_2, key=lambda x: x.timestamp_utc):
        if min_date <= b.timestamp_utc <= max_date and b.timestamp_utc not in seen:
            seen.add(b.timestamp_utc)
            combined.append(b)
    return combined


def simulate_silver_bullet(
    symbol: str,
    bars: list[Bar],
    mode: str, # "limit" or "market"
) -> list[dict[str, Any]]:
    spec = INSTRUMENT_SPECS[symbol]
    costs = INSTRUMENT_COSTS[symbol]
    spread = costs["spread"]
    slippage = costs["slippage"]

    strat, _ = load_silver_bullet_strategy(spec)
    # Prune levels to maintain fast evaluation
    orig_sb = strat.on_bar
    def p_sb(b: Bar):
        res = orig_sb(b)
        idx = strat._detector.bar_count
        if idx > 400:
            strat._detector.drop_levels(idx - 400)
        return res
    strat.on_bar = p_sb

    trades: list[dict[str, Any]] = []
    
    # State tracking
    # Active trade: {side, entry_price, sl, tp, risk, entry_time}
    active_trade: dict[str, Any] | None = None
    resting_orders: list[dict[str, Any]] = [] # For limit mode
    pending_market_entry: dict[str, Any] | None = None # For market mode

    trade_id = 0

    for idx, bar in enumerate(bars):
        bar_time = bar.timestamp_utc
        ny_time = bar_time.astimezone(NY_TZ)
        mid = bar.mid_ohlc

        # 1. Manage active trade
        if active_trade is not None:
            stop_hit = (mid.low <= active_trade["stop_loss"]) if active_trade["side"] == "BUY" else (mid.high >= active_trade["stop_loss"])
            tp_hit = (mid.high >= active_trade["take_profit"]) if active_trade["side"] == "BUY" else (mid.low <= active_trade["take_profit"])
            dynamic_close = ny_time.time() >= dt.time(16, 45)

            if stop_hit or tp_hit or dynamic_close:
                # Trade exits on this bar
                if stop_hit and tp_hit:
                    # Conservative conflict resolution: stop hit
                    exit_price = active_trade["stop_loss"]
                    is_stop = True
                    reason = "STOP_LOSS (conflict)"
                elif stop_hit:
                    exit_price = active_trade["stop_loss"]
                    is_stop = True
                    reason = "STOP_LOSS"
                elif tp_hit:
                    exit_price = active_trade["take_profit"]
                    is_stop = False
                    reason = "TAKE_PROFIT"
                else:
                    exit_price = mid.close
                    is_stop = False
                    reason = "DYNAMIC_CLOSE_1645"

                # Calculate PnL and R
                entry_p = float(active_trade["entry_price"])
                exit_p = float(exit_price)
                risk_d = float(active_trade["risk"])
                s_dist = max(risk_d, 1e-6)

                raw_pnl = (exit_p - entry_p) if active_trade["side"] == "BUY" else (entry_p - exit_p)
                gross_r = raw_pnl / s_dist
                cost_drag = (spread + (slippage if is_stop else 0.0)) / s_dist
                net_r = gross_r - cost_drag

                trades.append({
                    "trade_id": f"{symbol}-{mode}-{trade_id}",
                    "symbol": symbol,
                    "mode": mode,
                    "side": active_trade["side"],
                    "entry_time": active_trade["entry_time"].isoformat(),
                    "date": active_trade["entry_time"].date().isoformat(),
                    "entry_price": entry_p,
                    "stop_loss": float(active_trade["stop_loss"]),
                    "take_profit": float(active_trade["take_profit"]),
                    "exit_time": bar_time.isoformat(),
                    "exit_price": exit_p,
                    "exit_reason": reason,
                    "raw_pnl": raw_pnl,
                    "risk_distance": s_dist,
                    "gross_r": gross_r,
                    "cost_drag_r": cost_drag,
                    "net_r": net_r,
                    "is_win": gross_r > 0,
                    "is_stop": is_stop,
                })
                active_trade = None

        if mode == "limit":
            # 2. Check resting limit orders
            for order in list(resting_orders):
                if active_trade is not None:
                    break

                # Expiry check
                if idx > order["expires_bar_idx"] or ny_time.time() >= dt.time(16, 45):
                    resting_orders.remove(order)
                    continue

                # Through fill check
                filled = (mid.low < order["limit_price"]) if order["side"] == "BUY" else (mid.high > order["limit_price"])
                if filled:
                    resting_orders.remove(order)
                    trade_id += 1
                    risk_d = abs(order["limit_price"] - order["stop_loss"])
                    active_trade = {
                        "side": order["side"],
                        "entry_price": order["limit_price"],
                        "stop_loss": order["stop_loss"],
                        "take_profit": order["take_profit"],
                        "risk": risk_d,
                        "entry_time": bar_time,
                    }
                    break

        elif mode == "market":
            # Execute pending market entry at open of this bar
            if pending_market_entry is not None:
                p_side = pending_market_entry["side"]
                p_sl = pending_market_entry["stop_loss"]
                p_tp = pending_market_entry["take_profit"]
                pending_market_entry = None

                if active_trade is None and ny_time.time() < dt.time(16, 45):
                    trade_id += 1
                    entry_p = mid.open
                    risk_d = abs(entry_p - p_sl)
                    active_trade = {
                        "side": p_side,
                        "entry_price": entry_p,
                        "stop_loss": p_sl,
                        "take_profit": p_tp,
                        "risk": risk_d,
                        "entry_time": bar_time,
                    }

        # 3. Strategy evaluation
        intents = strat.on_bar(bar)
        for intent in intents:
            if type(intent).__name__ == "PlaceLimit":
                side_str = "BUY" if intent.side.value == "BUY" else "SELL"
                if mode == "limit":
                    if active_trade is None:
                        resting_orders.append({
                            "side": side_str,
                            "limit_price": intent.limit_price,
                            "stop_loss": intent.stop_price,
                            "take_profit": intent.target_price,
                            "placed_bar_idx": idx,
                            "expires_bar_idx": idx + intent.expires_after_bars,
                        })
                elif mode == "market":
                    if active_trade is None:
                        pending_market_entry = {
                            "side": side_str,
                            "stop_loss": intent.stop_price,
                            "take_profit": intent.target_price,
                        }

    return trades


def run_date_clustered_bootstrap(
    trades: list[dict[str, Any]],
    n_iter: int = 10000,
    seed: int = 42,
) -> tuple[float, float]:
    """Runs a date-clustered bootstrap over trades."""
    if not trades:
        return 0.0, 0.0

    rng = np.random.default_rng(seed)
    
    # Group trades by date
    date_to_trades: dict[str, list[float]] = {}
    for t in trades:
        d = t["date"]
        date_to_trades.setdefault(d, []).append(t["net_r"])
    
    dates = list(date_to_trades.keys())
    m = len(dates)
    if m == 0:
        return 0.0, 0.0

    means = []
    for _ in range(n_iter):
        sampled_dates = rng.choice(dates, size=m, replace=True)
        sampled_net_rs = []
        for d in sampled_dates:
            sampled_net_rs.extend(date_to_trades[d])
        means.append(np.mean(sampled_net_rs))

    ci_lower = float(np.percentile(means, 2.5))
    ci_upper = float(np.percentile(means, 97.5))
    return ci_lower, ci_upper


def main() -> None:
    print("=== STEP 2: SILVER BULLET ACROSS INSTRUMENTS ===")
    
    # 1. Load bars for all instruments
    print("Loading data for all 5 instruments...")
    data_bars: dict[str, list[Bar]] = {}
    
    print("Loading XAUUSD...")
    data_bars["XAUUSD"] = load_xauusd_bars()
    print(f"  XAUUSD: {len(data_bars['XAUUSD'])} bars ({data_bars['XAUUSD'][0].timestamp_utc.date()} to {data_bars['XAUUSD'][-1].timestamp_utc.date()})")

    for fx_sym in ["EURUSD", "GBPUSD", "USDJPY"]:
        print(f"Loading {fx_sym}...")
        data_bars[fx_sym] = load_parquet_bars("research/data/cache/fx_5min.parquet", fx_sym)
        print(f"  {fx_sym}: {len(data_bars[fx_sym])} bars ({data_bars[fx_sym][0].timestamp_utc.date()} to {data_bars[fx_sym][-1].timestamp_utc.date()})")

    print("Loading ES...")
    data_bars["ES"] = load_parquet_bars("research/data/cache/stk_5min.parquet", "ES")
    print(f"  ES: {len(data_bars['ES'])} bars ({data_bars['ES'][0].timestamp_utc.date()} to {data_bars['ES'][-1].timestamp_utc.date()})")

    # 2. Simulate for both modes
    results = {}
    out_dir = Path("research/output")
    out_dir.mkdir(parents=True, exist_ok=True)

    for mode in ["limit", "market"]:
        print(f"\n--- Simulating {mode.upper()} mode across all instruments ---")
        mode_trades: list[dict[str, Any]] = []
        inst_summary = {}

        for sym in ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "ES"]:
            bars = data_bars[sym]
            tr = simulate_silver_bullet(sym, bars, mode=mode)
            mode_trades.extend(tr)
            
            n = len(tr)
            if n > 0:
                gross_r_vals = [t["gross_r"] for t in tr]
                net_r_vals = [t["net_r"] for t in tr]
                gross_wins = sum(1 for t in tr if t["gross_r"] > 0)
                net_wins = sum(1 for t in tr if t["net_r"] > 0)
                cost_drags = [t["cost_drag_r"] for t in tr]

                inst_summary[sym] = {
                    "n": n,
                    "gross_exp": float(np.mean(gross_r_vals)),
                    "net_exp": float(np.mean(net_r_vals)),
                    "gross_wr": float(gross_wins / n),
                    "net_wr": float(net_wins / n),
                    "cost_drag": float(np.mean(cost_drags)),
                }
            else:
                inst_summary[sym] = {
                    "n": 0,
                    "gross_exp": 0.0,
                    "net_exp": 0.0,
                    "gross_wr": 0.0,
                    "net_wr": 0.0,
                    "cost_drag": 0.0,
                }
            print(f"  {sym:<8}: N={inst_summary[sym]['n']:<4} | Gross Exp={inst_summary[sym]['gross_exp']:+.4f}R | Net Exp={inst_summary[sym]['net_exp']:+.4f}R | Cost Drag={inst_summary[sym]['cost_drag']:.4f}R")

        # Save trade CSV
        csv_path = out_dir / f"sb_multi_instrument_{mode}_trades.csv"
        if mode_trades:
            fieldnames = list(mode_trades[0].keys())
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(mode_trades)
        print(f"Saved {len(mode_trades)} trades to {csv_path}")

        # Pooled metrics
        pooled_n = len(mode_trades)
        if pooled_n > 0:
            pooled_gross_exp = float(np.mean([t["gross_r"] for t in mode_trades]))
            pooled_net_exp = float(np.mean([t["net_r"] for t in mode_trades]))
            ci_lower, ci_upper = run_date_clustered_bootstrap(mode_trades, n_iter=10000, seed=42)
        else:
            pooled_gross_exp = 0.0
            pooled_net_exp = 0.0
            ci_lower, ci_upper = 0.0, 0.0

        print(f"\n  POOLED {mode.upper()}: N={pooled_n} | Gross Exp={pooled_gross_exp:+.4f}R | Net Exp={pooled_net_exp:+.4f}R | Date-Clustered 95% CI=[{ci_lower:+.4f}, {ci_upper:+.4f}]")

        results[mode] = {
            "by_instrument": inst_summary,
            "pooled": {
                "n": pooled_n,
                "gross_exp": pooled_gross_exp,
                "net_exp": pooled_net_exp,
                "date_clustered_ci_95": [ci_lower, ci_upper],
            }
        }

    # Save summary json
    summary_path = out_dir / "step2_multi_instrument_sb.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved Step 2 summary to {summary_path}")


if __name__ == "__main__":
    main()
