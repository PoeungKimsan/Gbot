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
) -> dict[str, Any]:
    """Execute forward daily paper test up through as_of_date.

    Idempotent:
    - If already run for as_of_date, refreshes state consistently without duplicate entries.
    - Handles flat-start: positions open at flat_start_date are moved to carried_trades.
    - Fills pending signals, tracks stops, and logs exact R drag components.
    """
    init_trend_db(db_path)
    now_utc_str = dt.datetime.now(dt.UTC).isoformat()
    broker_swaps = load_broker_swaps()

    # Pre-load all bars up through as_of_date
    bars_by_sym: dict[str, list[DailyBar]] = {}
    for sym in SYMBOLS:
        bars = load_bars_from_db(sym, as_of_date=as_of_date, db_path=bars_db_path)
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

        # Run T2 and T3 on bars
        # Note: If flat_start_date is given, historical trades exiting before flat_start_date
        # are simulated to establish correct regime, but only trades entered on or after
        # flat_start_date are considered forward trades.
        trades_t2 = run_t2_sma_cross(sym, bars, start_date=None, end_date=as_of_date)
        trades_t3 = run_t3_tsmom(sym, bars, start_date=None, end_date=as_of_date)

        for rule, trade_list in [("T2_SMA_CROSS", trades_t2), ("T3_TSMOM", trades_t3)]:
            for t in trade_list:
                # Flat-start filtering:
                if flat_start_date:
                    if t.entry_date < flat_start_date:
                        # If trade was still open at flat_start_date, record as carried
                        if t.exit_date >= flat_start_date:
                            carried_positions.append({
                                "trade_id": t.trade_id,
                                "rule": rule,
                                "symbol": sym,
                                "direction": t.direction,
                                "entry_date": t.entry_date.isoformat(),
                                "entry_price": t.entry_price,
                                "stop_loss": t.stop_loss,
                                "stop_distance": t.stop_distance,
                            })
                        # Historical trade exited before flat_start_date is ignored
                        continue

                # Forward trade:
                all_closed_trades.append(t)

    # Persist atomically to database
    with get_trend_db(db_path) as conn:
        # Clear existing trades for idempotent rebuild
        conn.execute("DELETE FROM trades;")
        conn.execute("DELETE FROM carried_trades;")

        for ct in carried_positions:
            conn.execute(
                """
                INSERT OR REPLACE INTO carried_trades (
                    trade_id, rule, symbol, direction, entry_date, entry_price, stop_loss, stop_distance, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ct["trade_id"], ct["rule"], ct["symbol"], ct["direction"],
                    ct["entry_date"], ct["entry_price"], ct["stop_loss"], ct["stop_distance"], now_utc_str,
                ),
            )

        for t in all_closed_trades:
            # Check broker swap rate
            sym = t.symbol
            swap_info = broker_swaps.get(sym)
            drag_broker_r = None
            net_r_broker = None
            if swap_info is not None:
                swap_rate_pts = swap_info["swap_long_pts"] if t.direction == "LONG" else swap_info["swap_short_pts"]
                swap_cost_pts = float(t.holding_days) * swap_rate_pts
                drag_broker_r = round(float(swap_cost_pts / t.stop_distance), 6)
                net_r_broker = round(float(t.gross_r - t.drag_spread_r - t.drag_slippage_r - drag_broker_r), 6)

            conn.execute(
                """
                INSERT OR REPLACE INTO trades (
                    trade_id, rule, symbol, direction, entry_date, entry_price,
                    stop_loss, stop_distance, exit_date, exit_price, exit_reason,
                    holding_days, gross_r, drag_spread_r, drag_slippage_r,
                    drag_fin0_r, drag_fin2_r, drag_broker_swap_r,
                    net_r_fin0, net_r_fin2, net_r_broker, is_stop, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    t.trade_id, t.rule, t.symbol, t.direction, t.entry_date.isoformat(), t.entry_price,
                    t.stop_loss, t.stop_distance, t.exit_date.isoformat(), t.exit_price, t.exit_reason,
                    t.holding_days, t.gross_r, t.drag_spread_r, t.drag_slippage_r,
                    0.0, t.drag_financing_r, drag_broker_r,
                    t.net_r_fin0, t.net_r, net_r_broker, 1 if t.is_stop else 0, now_utc_str,
                ),
            )

        # Log run
        conn.execute(
            """
            INSERT INTO runs (as_of_date, executed_at, trades_count, positions_count, warnings_count)
            VALUES (?, ?, ?, ?, ?)
            """,
            (as_of_date.isoformat(), now_utc_str, len(all_closed_trades), len(active_open_positions), 0),
        )

    logger.info(
        "Forward run completed for as-of date %s: %d closed trades, %d carried positions recorded.",
        as_of_date.isoformat(), len(all_closed_trades), len(carried_positions),
    )

    return {
        "as_of_date": as_of_date.isoformat(),
        "closed_trades_count": len(all_closed_trades),
        "carried_count": len(carried_positions),
    }
