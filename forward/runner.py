"""Core daily execution runner and trade reconciliation engine for forward paper-trading."""

from __future__ import annotations

import csv
import datetime as dt
import logging
from pathlib import Path
from typing import Any

from research.trend.models import DailyBar, TrendTrade
from research.trend.strategies import run_t2_sma_cross, run_t3_tsmom

from forward.config import (
    BARS_DB_PATH,
    CARRIED_PENDING_CUTOFF_DATE,
    START_BOUNDARY_SIGNAL_DATE,
    SWAPS_CSV_PATH,
    SYMBOLS,
    TREND_DB_PATH,
)
from forward.db import get_trend_db, init_trend_db
from forward.feed import load_bars_from_db

logger = logging.getLogger("forward.runner")


def load_broker_swaps() -> dict[str, dict[str, float]]:
    """Load broker swaps from forward/swaps.csv if present. Never invent rates."""
    if not SWAPS_CSV_PATH.is_file():
        return {}
    swaps: dict[str, dict[str, float]] = {}
    with open(SWAPS_CSV_PATH, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            sym = r.get("symbol") or r.get("market")
            if not sym:
                continue
            sl_raw = r.get("swap_long_pts") or r.get("swap_long")
            ss_raw = r.get("swap_short_pts") or r.get("swap_short")
            if sl_raw is None or ss_raw is None:
                continue
            try:
                sl = float(sl_raw)
                ss = float(ss_raw)
                import math
                if not (math.isnan(sl) or math.isnan(ss)):
                    swaps[sym] = {
                        "swap_long_pts": sl,
                        "swap_short_pts": ss,
                    }
            except (ValueError, TypeError):
                continue
    return swaps


def run_forward_day(
    as_of_date: dt.date | None = None,
    flat_start_date: dt.date | None = None,
    db_path: Path = TREND_DB_PATH,
    bars_db_path: Path = BARS_DB_PATH,
    current_time: dt.datetime | None = None,
) -> dict[str, Any]:
    """Execute forward daily paper test up through as_of_date.

    Start boundary rule (Prereg v1.1):
    - Signals computed on bars <= 2026-10-09 (including entries pending for 2026-10-12 open)
      are 'carried-pending' and excluded from fresh forward trades.
    - Fresh trades require signals computed on bars >= 2026-10-12 (entering >= 2026-10-13).
    - A reversal entry after a carried exit is fresh if its signal was computed on a bar >= 2026-10-12.
    - Logs stop % of price per trade and open positions / summed open risk daily.
    """
    init_trend_db(db_path)
    now_utc_str = dt.datetime.now(dt.UTC).isoformat()
    broker_swaps = load_broker_swaps()

    cutoff_dt = dt.date.fromisoformat(CARRIED_PENDING_CUTOFF_DATE)
    start_boundary_dt = flat_start_date or dt.date.fromisoformat(START_BOUNDARY_SIGNAL_DATE)

    # Pre-load all bars up through as_of_date
    bars_by_sym: dict[str, list[DailyBar]] = {}
    for sym in SYMBOLS:
        bars = load_bars_from_db(sym, as_of_date=as_of_date, db_path=bars_db_path, current_time=current_time)
        bars_by_sym[sym] = bars

    # If as_of_date is not specified, derive from the latest available bar date across markets
    if as_of_date is None:
        latest_dates = [b[-1].date for b in bars_by_sym.values() if b]
        as_of_date = max(latest_dates) if latest_dates else dt.date.today()

    all_closed_trades: list[TrendTrade] = []
    carried_positions: list[dict[str, Any]] = []
    active_open_positions: list[dict[str, Any]] = []

    for sym in SYMBOLS:
        bars = bars_by_sym[sym]
        if len(bars) < 252:
            continue

        date_to_idx = {b.date: i for i, b in enumerate(bars)}

        # Run T2 and T3 on bars
        trades_t2 = run_t2_sma_cross(sym, bars, start_date=None, end_date=as_of_date)
        trades_t3 = run_t3_tsmom(sym, bars, start_date=None, end_date=as_of_date)

        for rule, trade_list in [("T2_SMA_CROSS", trades_t2), ("T3_TSMOM", trades_t3)]:
            for t in trade_list:
                # Identify signal date
                entry_idx = date_to_idx.get(t.entry_date)
                signal_date = bars[entry_idx - 1].date if (entry_idx and entry_idx > 0) else t.entry_date

                # Stop % of price
                stop_pct = round((t.stop_distance / t.entry_price) * 100.0, 4) if t.entry_price > 0 else 0.0

                # Check if position is currently open
                if t.exit_reason == "PERIOD_END" and t.exit_date >= as_of_date:
                    active_open_positions.append({
                        "position_id": f"POS_{t.trade_id}",
                        "rule": rule,
                        "symbol": sym,
                        "direction": t.direction,
                        "entry_date": t.entry_date.isoformat(),
                        "entry_price": t.entry_price,
                        "stop_loss": t.stop_loss,
                        "stop_distance": t.stop_distance,
                        "stop_pct_price": stop_pct,
                        "open_risk_r": 1.0,
                    })

                # Boundary partitioning:
                # 1. Carried / Carried-Pending: signal <= 2026-10-09
                if signal_date <= cutoff_dt:
                    # Either entered before 10-12 or pending for 10-12
                    status = "CARRIED_PENDING" if t.entry_date >= start_boundary_dt else "CARRIED"
                    if t.exit_date >= start_boundary_dt:
                        carried_positions.append({
                            "trade_id": t.trade_id,
                            "rule": rule,
                            "symbol": sym,
                            "direction": t.direction,
                            "entry_date": t.entry_date.isoformat(),
                            "entry_price": t.entry_price,
                            "stop_loss": t.stop_loss,
                            "stop_distance": t.stop_distance,
                            "stop_pct_price": stop_pct,
                            "status": status,
                        })
                    continue

                # 2. Fresh forward trade: signal >= 2026-10-12
                if signal_date >= start_boundary_dt:
                    all_closed_trades.append(t)
                else:
                    # Signal was between 10-10 and 10-11 (weekend, no bars)
                    if t.entry_date >= start_boundary_dt:
                        all_closed_trades.append(t)

    # Persist atomically to database
    with get_trend_db(db_path) as conn:
        conn.execute("DELETE FROM trades;")
        conn.execute("DELETE FROM carried_trades;")
        conn.execute("DELETE FROM open_positions;")

        for ct in carried_positions:
            conn.execute(
                """
                INSERT OR REPLACE INTO carried_trades (
                    trade_id, rule, symbol, direction, entry_date, entry_price,
                    stop_loss, stop_distance, stop_pct_price, status, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ct["trade_id"], ct["rule"], ct["symbol"], ct["direction"],
                    ct["entry_date"], ct["entry_price"], ct["stop_loss"], ct["stop_distance"],
                    ct["stop_pct_price"], ct["status"], now_utc_str,
                ),
            )

        for pos in active_open_positions:
            conn.execute(
                """
                INSERT OR REPLACE INTO open_positions (
                    position_id, rule, symbol, direction, entry_date, entry_price,
                    stop_loss, stop_distance, stop_pct_price, open_risk_r, last_updated
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pos["position_id"], pos["rule"], pos["symbol"], pos["direction"],
                    pos["entry_date"], pos["entry_price"], pos["stop_loss"], pos["stop_distance"],
                    pos["stop_pct_price"], pos["open_risk_r"], now_utc_str,
                ),
            )

        for t in all_closed_trades:
            sym = t.symbol
            swap_info = broker_swaps.get(sym)
            drag_broker_r = None
            net_r_broker = None
            if swap_info is not None:
                swap_rate_pts = swap_info["swap_long_pts"] if t.direction == "LONG" else swap_info["swap_short_pts"]
                swap_cost_pts = float(t.holding_days) * swap_rate_pts
                drag_broker_r = round(float(swap_cost_pts / t.stop_distance), 6)
                net_r_broker = round(float(t.gross_r - t.drag_spread_r - t.drag_slippage_r - drag_broker_r), 6)

            stop_pct = round((t.stop_distance / t.entry_price) * 100.0, 4) if t.entry_price > 0 else 0.0

            conn.execute(
                """
                INSERT OR REPLACE INTO trades (
                    trade_id, rule, symbol, direction, entry_date, entry_price,
                    stop_loss, stop_distance, stop_pct_price, exit_date, exit_price, exit_reason,
                    holding_days, gross_r, drag_spread_r, drag_slippage_r,
                    drag_fin0_r, drag_fin2_r, drag_broker_swap_r,
                    net_r_fin0, net_r_fin2, net_r_broker, is_stop, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    t.trade_id, t.rule, t.symbol, t.direction, t.entry_date.isoformat(), t.entry_price,
                    t.stop_loss, t.stop_distance, stop_pct, t.exit_date.isoformat(), t.exit_price, t.exit_reason,
                    t.holding_days, t.gross_r, t.drag_spread_r, t.drag_slippage_r,
                    0.0, t.drag_financing_r, drag_broker_r,
                    t.net_r_fin0, t.net_r, net_r_broker, 1 if t.is_stop else 0, now_utc_str,
                ),
            )
            logger.info(
                "Trade logged: %s %s %s entry=%.4f stop=%.4f (stop_pct=%.2f%%)",
                t.rule, sym, t.direction, t.entry_price, t.stop_loss, stop_pct,
            )

        # Daily summary logging
        summed_open_risk_r = round(float(len(active_open_positions) * 1.0), 4)
        conn.execute(
            """
            INSERT INTO runs (as_of_date, executed_at, trades_count, positions_count, summed_open_risk_r, warnings_count)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (as_of_date.isoformat(), now_utc_str, len(all_closed_trades), len(active_open_positions), summed_open_risk_r, 0),
        )

    logger.info(
        "Daily run %s: %d closed trades, %d open positions (summed risk=%.2f R), %d carried recorded.",
        as_of_date.isoformat(), len(all_closed_trades), len(active_open_positions), summed_open_risk_r, len(carried_positions),
    )

    return {
        "as_of_date": as_of_date.isoformat(),
        "closed_trades_count": len(all_closed_trades),
        "open_positions_count": len(active_open_positions),
        "summed_open_risk_r": summed_open_risk_r,
        "carried_count": len(carried_positions),
    }
