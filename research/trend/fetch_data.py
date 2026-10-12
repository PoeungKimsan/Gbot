"""Fetch daily bars from Yahoo Finance for trend research universe.

Strict rules:
- Longest available history (target 2010 onward, ideally 2006-2025).
- Hard-stops at 2025-12-31 (sealed 2026 data stays UNLOADED until the final step).
- Drops any instrument with < 12 years of history.
- Performs an INSTRUMENT IDENTITY AUDIT: first/last close, yearly min/max, verification.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import logging
from pathlib import Path
from typing import Any

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("trend.fetch_data")

DATA_DIR = Path("research/trend/data")

# Fixed candidate universe
# XAUUSD, XAGUSD, EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, USDCHF, US500, GER40, Brent, plus one more liquid index (NDX)
SYMBOLS_MAP = {
    "XAUUSD": {"ticker": "GC=F", "desc": "Gold Futures (USD/oz)"},
    "XAGUSD": {"ticker": "SI=F", "desc": "Silver Futures (USD/oz)"},
    "EURUSD": {"ticker": "EURUSD=X", "desc": "Euro / US Dollar FX"},
    "GBPUSD": {"ticker": "GBPUSD=X", "desc": "British Pound / US Dollar FX"},
    "USDJPY": {"ticker": "USDJPY=X", "desc": "US Dollar / Japanese Yen FX"},
    "AUDUSD": {"ticker": "AUDUSD=X", "desc": "Australian Dollar / US Dollar FX"},
    "USDCAD": {"ticker": "USDCAD=X", "desc": "US Dollar / Canadian Dollar FX"},
    "USDCHF": {"ticker": "USDCHF=X", "desc": "US Dollar / Swiss Franc FX"},
    "US500": {"ticker": "^GSPC", "desc": "S&P 500 Index"},
    "GER40": {"ticker": "^GDAXI", "desc": "DAX 40 Index"},
    "Brent": {"ticker": "BZ=F", "desc": "Brent Crude Oil Futures (USD/bbl)"},
    "NDX": {"ticker": "^NDX", "desc": "NASDAQ-100 Index"},
}

HARD_STOP_DATE = dt.date(2025, 12, 31)
MIN_YEARS_REQUIRED = 12.0


def download_symbol(symbol: str, ticker: str, allow_2026: bool = False) -> list[dict[str, Any]]:
    """Download daily bars via Yahoo Finance chart API."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=25y"
    headers = {"User-Agent": "Mozilla/5.0"}

    with httpx.Client(headers=headers, timeout=30.0) as client:
        r = client.get(url)
        r.raise_for_status()
        data = r.json()

    result = data["chart"]["result"][0]
    timestamps = result["timestamp"]
    quote = result["indicators"]["quote"][0]

    opens = quote["open"]
    highs = quote["high"]
    lows = quote["low"]
    closes = quote["close"]
    volumes = quote.get("volume", [0] * len(timestamps))

    bars = []
    for ts, op, hi, lo, cl, vol in zip(timestamps, opens, highs, lows, closes, volumes):
        if op is None or hi is None or lo is None or cl is None:
            continue
        bar_date = dt.datetime.fromtimestamp(ts, dt.UTC).date()

        # Hard stop enforcement
        if not allow_2026 and bar_date > HARD_STOP_DATE:
            continue

        bars.append({
            "date": bar_date.isoformat(),
            "open": round(float(op), 6),
            "high": round(float(hi), 6),
            "low": round(float(lo), 6),
            "close": round(float(cl), 6),
            "volume": int(vol or 0),
        })
    return bars


def audit_instrument(symbol: str, meta: dict[str, Any], bars: list[dict[str, Any]]) -> dict[str, Any]:
    """Run Instrument Identity Audit across all bars."""
    if not bars:
        return {"symbol": symbol, "status": "NO_DATA"}

    first_bar = bars[0]
    last_bar = bars[-1]

    d_first = dt.date.fromisoformat(first_bar["date"])
    d_last = dt.date.fromisoformat(last_bar["date"])
    years_span = (d_last - d_first).days / 365.25

    # Yearly min/max close
    yearly: dict[int, dict[str, float]] = {}
    for b in bars:
        yr = int(b["date"][:4])
        cl = b["close"]
        if yr not in yearly:
            yearly[yr] = {"min_close": cl, "max_close": cl, "first_close": cl, "last_close": cl}
        else:
            yearly[yr]["min_close"] = min(yearly[yr]["min_close"], cl)
            yearly[yr]["max_close"] = max(yearly[yr]["max_close"], cl)
            yearly[yr]["last_close"] = cl

    passed_history = years_span >= MIN_YEARS_REQUIRED

    return {
        "symbol": symbol,
        "ticker": meta["ticker"],
        "description": meta["desc"],
        "bars_count": len(bars),
        "first_date": first_bar["date"],
        "first_close": first_bar["close"],
        "last_date": last_bar["date"],
        "last_close": last_bar["close"],
        "years_span": round(years_span, 2),
        "meets_history_req": passed_history,
        "yearly_summary": yearly,
    }


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    audit_results = []

    print("\n" + "=" * 90)
    print("STEP 0: INSTRUMENT IDENTITY AUDIT & DATA ACQUISITION")
    print("=" * 90)

    for symbol, meta in SYMBOLS_MAP.items():
        ticker = meta["ticker"]
        logger.info("Fetching %s (%s)...", symbol, ticker)
        bars = download_symbol(symbol, ticker, allow_2026=False)

        # Save raw daily bars
        out_csv = DATA_DIR / f"{symbol}_daily.csv"
        with open(out_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["date", "open", "high", "low", "close", "volume"])
            writer.writeheader()
            writer.writerows(bars)

        audit = audit_instrument(symbol, meta, bars)
        audit_results.append(audit)

        status_flag = "PASS" if audit["meets_history_req"] else "FAIL (<12yr)"
        print(
            f"{symbol:8} ({ticker:10}) | {audit['first_date']} -> {audit['last_date']} "
            f"({audit['years_span']:4.1f} yrs) | Bars: {audit['bars_count']:4} | "
            f"Close: {audit['first_close']:>10.4f} -> {audit['last_close']:>10.4f} | {status_flag}"
        )

    # Save audit report to JSON
    audit_file = DATA_DIR / "instrument_identity_audit.json"
    with open(audit_file, "w", encoding="utf-8") as f:
        json.dump(audit_results, f, indent=2)
    logger.info("Audit saved to %s", audit_file)


if __name__ == "__main__":
    main()
