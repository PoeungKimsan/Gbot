"""Data ingestion, validation, and bar database management for forward paper-runner."""

from __future__ import annotations

import datetime as dt
import logging
import sqlite3
import zoneinfo
from pathlib import Path
from typing import Any

import httpx
from research.trend.models import DailyBar

from forward.config import (
    BARS_DB_PATH,
    SYMBOLS,
    TICKERS_MAP,
)
from forward.db import get_bars_db, init_bars_db

logger = logging.getLogger("forward.feed")


def fetch_and_store_bars(
    symbols: list[str] | None = None,
    as_of_date: dt.date | None = None,
    db_path: Path = BARS_DB_PATH,
) -> dict[str, int]:
    """Fetch completed daily bars from Yahoo Finance, sanitize, audit roll gaps, and store."""
    init_bars_db(db_path)
    symbols = symbols or SYMBOLS
    counts: dict[str, int] = {}
    now_utc = dt.datetime.now(dt.UTC)
    now_utc_str = now_utc.isoformat()
    today_utc = now_utc.date()

    headers = {"User-Agent": "Mozilla/5.0"}

    with get_bars_db(db_path) as conn:
        for sym in symbols:
            meta = TICKERS_MAP[sym]
            ticker = meta["ticker"]
            tz = zoneinfo.ZoneInfo(meta["exchange_tz"])
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=25y"

            try:
                with httpx.Client(headers=headers, timeout=30.0) as client:
                    r = client.get(url)
                    r.raise_for_status()
                    data = r.json()["chart"]["result"][0]
            except Exception as e:
                logger.error("Failed to fetch bars for %s (%s): %s", sym, ticker, e)
                continue

            timestamps = data.get("timestamp", [])
            indicators = data.get("indicators", {}).get("quote", [{}])[0]
            opens = indicators.get("open", [])
            highs = indicators.get("high", [])
            lows = indicators.get("low", [])
            closes = indicators.get("close", [])
            volumes = indicators.get("volume", [0] * len(timestamps))

            stored_count = 0
            for ts, op, hi, lo, cl, vol in zip(timestamps, opens, highs, lows, closes, volumes, strict=False):
                if op is None or hi is None or lo is None or cl is None:
                    continue

                # Session date conversion per exchange timezone
                bar_dt = dt.datetime.fromtimestamp(ts, tz)
                bar_date = bar_dt.date()

                # Invariant: Never use a bar that is not yet complete
                # An incomplete bar is one timestamped on today's current date before session close
                if bar_date >= today_utc:
                    continue
                if as_of_date and bar_date > as_of_date:
                    continue

                op_f = round(float(op), 6)
                hi_f = round(float(hi), 6)
                lo_f = round(float(lo), 6)
                cl_f = round(float(cl), 6)
                vol_i = int(vol or 0)

                # Sanitization check
                sanitized = 0
                orig_h, orig_l = hi_f, lo_f
                new_h = max(hi_f, op_f, cl_f)
                new_l = min(lo_f, op_f, cl_f)

                if new_h != orig_h or new_l != orig_l:
                    sanitized = 1
                    conn.execute(
                        """
                        INSERT INTO sanitization_logs (symbol, date, orig_high, orig_low, new_high, new_low, logged_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (sym, bar_date.isoformat(), orig_h, orig_l, new_h, new_l, now_utc_str),
                    )

                conn.execute(
                    """
                    INSERT INTO bars (symbol, date, open, high, low, close, volume, fetch_timestamp, sanitized_hl)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol, date) DO UPDATE SET
                        open = excluded.open,
                        high = excluded.high,
                        low = excluded.low,
                        close = excluded.close,
                        volume = excluded.volume,
                        fetch_timestamp = excluded.fetch_timestamp,
                        sanitized_hl = excluded.sanitized_hl
                    """,
                    (sym, bar_date.isoformat(), op_f, new_h, new_l, cl_f, vol_i, now_utc_str, sanitized),
                )
                stored_count += 1

            # Audit roll gaps for futures contracts
            if sym in ("XAUUSD", "XAGUSD", "Brent"):
                audit_roll_gaps(conn, sym, now_utc_str)

            counts[sym] = stored_count
            logger.info("Stored %d bars for %s in %s", stored_count, sym, db_path.name)

    return counts


def audit_roll_gaps(conn: sqlite3.Connection, symbol: str, now_utc_str: str) -> None:
    """Audit for roll gap anomalies: |Close - Close_prev| > 6 * ATR20 (flag only)."""
    rows = conn.execute(
        "SELECT date, close FROM bars WHERE symbol = ? ORDER BY date ASC",
        (symbol,),
    ).fetchall()
    if len(rows) < 25:
        return

    # Check recent bars
    for i in range(21, len(rows)):
        c_curr = rows[i]["close"]
        c_prev = rows[i - 1]["close"]
        diff = abs(c_curr - c_prev)
        # Approximate recent ATR
        # Compute TR over last 20 bars
        prev_slice = rows[i - 20: i]
        diffs = [abs(prev_slice[k]["close"] - prev_slice[k - 1]["close"]) for k in range(1, len(prev_slice))]
        atr_approx = sum(diffs) / len(diffs) if diffs else 1.0

        if diff > 6.0 * atr_approx and atr_approx > 0:
            b_date = rows[i]["date"]
            # Check if already warned
            existing = conn.execute(
                "SELECT id FROM roll_gap_warnings WHERE symbol = ? AND date = ?",
                (symbol, b_date),
            ).fetchone()
            if not existing:
                conn.execute(
                    """
                    INSERT INTO roll_gap_warnings (symbol, date, close_prev, close_curr, diff_pts, atr20, logged_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (symbol, b_date, c_prev, c_curr, diff, atr_approx, now_utc_str),
                )
                conn.execute(
                    "UPDATE bars SET roll_gap_warn = 1 WHERE symbol = ? AND date = ?",
                    (symbol, b_date),
                )
                logger.warning("POSSIBLE ROLL GAP on %s %s: diff=%.4f > 6*ATR (%.4f)", symbol, b_date, diff, atr_approx)


def load_bars_from_db(
    symbol: str,
    as_of_date: dt.date | None = None,
    db_path: Path = BARS_DB_PATH,
) -> list[DailyBar]:
    """Load bars from SQLite, sanitize, and compute Wilder ATR20, SMA50, SMA200, Donchian channels."""
    init_bars_db(db_path)
    with get_bars_db(db_path) as conn:
        query = "SELECT date, open, high, low, close, volume FROM bars WHERE symbol = ? "
        params: list[Any] = [symbol]
        if as_of_date:
            query += "AND date <= ? "
            params.append(as_of_date.isoformat())
        query += "ORDER BY date ASC"

        rows = conn.execute(query, params).fetchall()

    if not rows:
        return []

    n = len(rows)
    dates = [dt.date.fromisoformat(r["date"]) for r in rows]
    opens = [float(r["open"]) for r in rows]
    highs = [float(r["high"]) for r in rows]
    lows = [float(r["low"]) for r in rows]
    closes = [float(r["close"]) for r in rows]
    volumes = [int(r["volume"]) for r in rows]

    # Geometric bar sanitization invariant
    for i in range(n):
        highs[i] = max(highs[i], opens[i], closes[i])
        lows[i] = min(lows[i], opens[i], closes[i])

    # 1. True Range & Wilder ATR(20)
    tr = [0.0] * n
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )

    atr20 = [0.0] * n
    if n >= 20:
        atr20[19] = sum(tr[:20]) / 20.0
        for i in range(20, n):
            atr20[i] = (atr20[i - 1] * 19.0 + tr[i]) / 20.0
        for i in range(19):
            atr20[i] = atr20[19]

    # 2. SMA(50) and SMA(200)
    sma50 = [0.0] * n
    if n >= 50:
        c_sum50 = sum(closes[:50])
        sma50[49] = c_sum50 / 50.0
        for i in range(50, n):
            c_sum50 += closes[i] - closes[i - 50]
            sma50[i] = c_sum50 / 50.0

    sma200 = [0.0] * n
    if n >= 200:
        c_sum200 = sum(closes[:200])
        sma200[199] = c_sum200 / 200.0
        for i in range(200, n):
            c_sum200 += closes[i] - closes[i - 200]
            sma200[i] = c_sum200 / 200.0

    # 3. Donchian channels
    donchian_high55 = [0.0] * n
    donchian_low55 = [0.0] * n
    for i in range(55, n):
        donchian_high55[i] = max(highs[i - 55: i])
        donchian_low55[i] = min(lows[i - 55: i])

    donchian_high20 = [0.0] * n
    donchian_low20 = [0.0] * n
    for i in range(20, n):
        donchian_high20[i] = max(highs[i - 20: i])
        donchian_low20[i] = min(lows[i - 20: i])

    # 4. 252-day Return
    return_252 = [0.0] * n
    for i in range(252, n):
        if closes[i - 252] > 0:
            return_252[i] = (closes[i] - closes[i - 252]) / closes[i - 252]

    # 5. Month-end identification
    is_month_end = [False] * n
    for i in range(n):
        if i == n - 1 or dates[i].month != dates[i + 1].month:
            is_month_end[i] = True

    bars: list[DailyBar] = []
    for i in range(n):
        bars.append(
            DailyBar(
                date=dates[i],
                open=opens[i],
                high=highs[i],
                low=lows[i],
                close=closes[i],
                volume=volumes[i],
                atr20=atr20[i],
                sma50=sma50[i],
                sma200=sma200[i],
                donchian_high55=donchian_high55[i],
                donchian_low55=donchian_low55[i],
                donchian_high20=donchian_high20[i],
                donchian_low20=donchian_low20[i],
                return_252=return_252[i],
                is_month_end=is_month_end[i],
            )
        )

    return bars
