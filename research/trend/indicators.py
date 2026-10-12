"""Technical indicator computations on daily bars."""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

from research.trend.models import DailyBar


def load_daily_bars(csv_path: str | Path, hard_stop_date: dt.date | None = None) -> list[DailyBar]:
    """Load daily bars from CSV and compute all indicators."""
    raw_rows: list[dict[str, str]] = []
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            b_date = dt.date.fromisoformat(row["date"])
            if hard_stop_date and b_date > hard_stop_date:
                continue
            raw_rows.append(row)

    if not raw_rows:
        return []

    # Sort chronologically
    raw_rows.sort(key=lambda r: r["date"])

    n = len(raw_rows)
    dates = [dt.date.fromisoformat(r["date"]) for r in raw_rows]
    opens = [float(r["open"]) for r in raw_rows]
    highs = [float(r["high"]) for r in raw_rows]
    lows = [float(r["low"]) for r in raw_rows]
    closes = [float(r["close"]) for r in raw_rows]
    volumes = [int(r.get("volume", 0) or 0) for r in raw_rows]

    # Geometric bar sanitization invariant: High >= max(Open, Close), Low <= min(Open, Close)
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
        # Backfill first 19
        for i in range(19):
            atr20[i] = atr20[19]

    # 2. SMA 50 and SMA 200
    sma50 = [0.0] * n
    cum_50 = 0.0
    for i in range(n):
        cum_50 += closes[i]
        if i >= 50:
            cum_50 -= closes[i - 50]
            sma50[i] = cum_50 / 50.0
        elif i == 49:
            sma50[i] = cum_50 / 50.0

    sma200 = [0.0] * n
    cum_200 = 0.0
    for i in range(n):
        cum_200 += closes[i]
        if i >= 200:
            cum_200 -= closes[i - 200]
            sma200[i] = cum_200 / 200.0
        elif i == 199:
            sma200[i] = cum_200 / 200.0

    # 3. Donchian channels (prior 55 and prior 20 days: strictly prior to bar i, i.e. i-N to i-1)
    donchian_high55 = [0.0] * n
    donchian_low55 = [0.0] * n
    donchian_high20 = [0.0] * n
    donchian_low20 = [0.0] * n

    for i in range(n):
        if i >= 55:
            donchian_high55[i] = max(highs[i - 55 : i])
            donchian_low55[i] = min(lows[i - 55 : i])
        if i >= 20:
            donchian_high20[i] = max(highs[i - 20 : i])
            donchian_low20[i] = min(lows[i - 20 : i])

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

    # Assemble DailyBar objects
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
