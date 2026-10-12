"""Daily forward logging runner for trend_v1.2 T2 and T3 strategies.

Pre-registration: research/prereg/trend_v1.2.md (commit f1affec).
Applies T2 (SMA 50/200 cross, 3xATR(20) stop) and T3 (12-month TSMOM sign flip, 3xATR(20) stop).
Zero live engine imports. Zero MT5 order execution.
Reads completed MT5 D1 bars, warm-ups on bars < 2026-10-12, and logs trades on bars >= 2026-10-12.
"""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5  # noqa: N813
import yaml
from research.trend.costs import compute_costs

# Required universe and config
CONFIG_PATH = Path("config/trend_symbols.yaml")
LOG_CSV_PATH = Path("forward_log.csv")
RUN_LOG_PATH = Path("forward_run.log")
START_FORWARD_DATE = dt.date(2026, 10, 12)
REQUIRED_WARMUP_BARS = 252


def load_symbols() -> dict[str, dict[str, Any]]:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg.get("symbols", {})


def fetch_and_clean_d1_bars(
    broker_symbol: str,
    max_bars: int = 1500,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Fetch MT5 D1 bars, filter flat/duplicate bars, enforce geometric sanity."""
    flags = {"duplicate_bars": 0, "flat_bars": 0, "total_raw": 0, "cleaned_bars": 0}

    mt5.symbol_select(broker_symbol, True)
    # Start at pos 1 to strictly fetch completed/closed bars (index 0 is still-forming)
    rates = mt5.copy_rates_from_pos(broker_symbol, mt5.TIMEFRAME_D1, 1, max_bars)
    if rates is None or len(rates) == 0:
        return [], flags

    flags["total_raw"] = len(rates)

    cleaned: list[dict[str, Any]] = []
    seen_dates: set[dt.date] = set()

    now_utc = dt.datetime.now(dt.UTC)
    today_utc = now_utc.date()

    for r in rates:
        bar_dt = dt.datetime.fromtimestamp(r["time"], tz=dt.UTC)
        bar_date = bar_dt.date()

        # Strict invariant: only closed bars prior to today's forming bar
        if bar_date >= today_utc:
            continue

        # If duplicate date, skip and flag
        if bar_date in seen_dates:
            flags["duplicate_bars"] += 1
            continue
        seen_dates.add(bar_date)

        op = float(r["open"])
        hi = float(r["high"])
        lo = float(r["low"])
        cl = float(r["close"])
        vol = int(r["tick_volume"])

        # Check for flat bar: high <= low or zero tick volume or completely flat
        if hi <= lo or vol == 0 or (op == hi == lo == cl):
            flags["flat_bars"] += 1
            continue

        # Geometric sanitization invariant
        hi = max(hi, op, cl)
        lo = min(lo, op, cl)

        cleaned.append({
            "date": bar_date,
            "open": op,
            "high": hi,
            "low": lo,
            "close": cl,
            "volume": vol,
            "is_forming": False,
        })

    flags["cleaned_bars"] = len(cleaned)
    return cleaned, flags


def compute_indicators(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compute Wilder ATR(20), SMA 50, SMA 200, and 252-day Return per prereg f1affec."""
    n = len(bars)
    if n == 0:
        return []

    # True Range & Wilder ATR(20)
    tr = [0.0] * n
    tr[0] = bars[0]["high"] - bars[0]["low"]
    for i in range(1, n):
        tr[i] = max(
            bars[i]["high"] - bars[i]["low"],
            abs(bars[i]["high"] - bars[i - 1]["close"]),
            abs(bars[i]["low"] - bars[i - 1]["close"]),
        )

    atr20 = [0.0] * n
    if n >= 20:
        atr20[19] = sum(tr[:20]) / 20.0
        for i in range(20, n):
            atr20[i] = (atr20[i - 1] * 19.0 + tr[i]) / 20.0
        for i in range(19):
            atr20[i] = atr20[19]

    # SMA 50 and SMA 200
    sma50 = [0.0] * n
    cum50 = 0.0
    for i in range(n):
        cum50 += bars[i]["close"]
        if i >= 50:
            cum50 -= bars[i - 50]["close"]
            sma50[i] = cum50 / 50.0
        elif i == 49:
            sma50[i] = cum50 / 50.0

    sma200 = [0.0] * n
    cum200 = 0.0
    for i in range(n):
        cum200 += bars[i]["close"]
        if i >= 200:
            cum200 -= bars[i - 200]["close"]
            sma200[i] = cum200 / 200.0
        elif i == 199:
            sma200[i] = cum200 / 200.0

    # 252-day return and Month-End detection
    ret252 = [0.0] * n
    for i in range(252, n):
        c_prev = bars[i - 252]["close"]
        if c_prev > 0:
            ret252[i] = (bars[i]["close"] - c_prev) / c_prev

    is_month_end = [False] * n
    for i in range(n - 1):
        d1 = bars[i]["date"]
        d2 = bars[i + 1]["date"]
        if d1.month != d2.month or d1.year != d2.year:
            is_month_end[i] = True

    enriched = []
    for i in range(n):
        b = dict(bars[i])
        b["atr20"] = atr20[i]
        b["sma50"] = sma50[i]
        b["sma200"] = sma200[i]
        b["ret252"] = ret252[i]
        b["is_month_end"] = is_month_end[i]
        enriched.append(b)

    return enriched


def evaluate_t2_sma_cross(
    symbol: str,
    bars: list[dict[str, Any]],
    start_forward_date: dt.date = START_FORWARD_DATE,
) -> list[dict[str, Any]]:
    """T2 SMA Cross: 50/200 cross, 3x ATR initial stop, wait for fresh cross after stop.

    Returns trades that enter on bars dated >= start_forward_date.
    """
    n = len(bars)
    in_pos = False
    direction = ""
    entry_date: dt.date = dt.date.min
    entry_price = 0.0
    stop_loss = 0.0
    stop_distance = 0.0
    stopped_out_regime: str | None = None

    forward_trades: list[dict[str, Any]] = []

    for i in range(1, n):
        bar = bars[i]
        prev_bar = bars[i - 1]

        # Evaluate cross that occurred at bar i-1 (using bars i-2 and i-1)
        if i >= 201:
            prev2 = bars[i - 2]
            bullish_cross = (prev2["sma50"] <= prev2["sma200"] and prev_bar["sma50"] > prev_bar["sma200"])
            bearish_cross = (prev2["sma50"] >= prev2["sma200"] and prev_bar["sma50"] < prev_bar["sma200"])
        else:
            bullish_cross = False
            bearish_cross = False

        # Reset stopped_out_regime upon fresh cross
        if bullish_cross and stopped_out_regime == "BULL":
            stopped_out_regime = None
        if bearish_cross and stopped_out_regime == "BEAR":
            stopped_out_regime = None

        # 1. If in position, check stop first on current bar
        if in_pos:
            is_stopped = False
            exit_price = 0.0

            if direction == "LONG":
                if bar["low"] <= stop_loss:
                    is_stopped = True
                    exit_price = bar["open"] if bar["open"] <= stop_loss else stop_loss
                    stopped_out_regime = "BULL"
            elif direction == "SHORT":
                if bar["high"] >= stop_loss:
                    is_stopped = True
                    exit_price = bar["open"] if bar["open"] >= stop_loss else stop_loss
                    stopped_out_regime = "BEAR"

            if is_stopped:
                in_pos = False
                if entry_date >= start_forward_date:
                    costs = compute_costs(
                        symbol=symbol,
                        direction=direction,
                        entry_price=entry_price,
                        exit_price=exit_price,
                        stop_distance=stop_distance,
                        is_stop=True,
                        entry_date=entry_date,
                        exit_date=bar["date"],
                    )
                    forward_trades.append({
                        "date": bar["date"].isoformat(),
                        "market": symbol,
                        "rule": "T2_SMA_CROSS",
                        "signal": direction,
                        "entry": f"{entry_price:.5f}",
                        "stop": f"{stop_loss:.5f}",
                        "exit": f"{exit_price:.5f}",
                        "R": f"{costs['net_r']:+.4f}",
                        "R_gross": f"{costs['gross_r']:+.4f}",
                        "R_net": f"{costs['net_r']:+.4f}",
                        "status": "STOP_CLOSED",
                    })
                continue

            # Check opposite cross exit on bar.open
            opposite_cross = (direction == "LONG" and bearish_cross) or (direction == "SHORT" and bullish_cross)
            if opposite_cross:
                in_pos = False
                exit_price = bar["open"]
                if entry_date >= start_forward_date:
                    costs = compute_costs(
                        symbol=symbol,
                        direction=direction,
                        entry_price=entry_price,
                        exit_price=exit_price,
                        stop_distance=stop_distance,
                        is_stop=False,
                        entry_date=entry_date,
                        exit_date=bar["date"],
                    )
                    forward_trades.append({
                        "date": bar["date"].isoformat(),
                        "market": symbol,
                        "rule": "T2_SMA_CROSS",
                        "signal": direction,
                        "entry": f"{entry_price:.5f}",
                        "stop": f"{stop_loss:.5f}",
                        "exit": f"{exit_price:.5f}",
                        "R": f"{costs['net_r']:+.4f}",
                        "R_gross": f"{costs['gross_r']:+.4f}",
                        "R_net": f"{costs['net_r']:+.4f}",
                        "status": "OPPOSITE_CROSS_CLOSED",
                    })

        # 2. If flat, check entry on fresh cross
        if not in_pos and i >= 201:
            atr = prev_bar["atr20"]
            stop_dist = 3.0 * atr

            if bullish_cross and stopped_out_regime != "BULL" and stop_dist > 0:
                direction = "LONG"
                entry_price = bar["open"]
                stop_distance = stop_dist
                stop_loss = entry_price - stop_distance
                entry_date = bar["date"]
                in_pos = True
                stopped_out_regime = None
                if entry_date >= start_forward_date:
                    forward_trades.append({
                        "date": bar["date"].isoformat(),
                        "market": symbol,
                        "rule": "T2_SMA_CROSS",
                        "signal": direction,
                        "entry": f"{entry_price:.5f}",
                        "stop": f"{stop_loss:.5f}",
                        "exit": "OPEN",
                        "R": "N/A",
                        "R_gross": "N/A",
                        "R_net": "N/A",
                        "status": "POSITION_OPENED",
                    })
            elif bearish_cross and stopped_out_regime != "BEAR" and stop_dist > 0:
                direction = "SHORT"
                entry_price = bar["open"]
                stop_distance = stop_dist
                stop_loss = entry_price + stop_distance
                entry_date = bar["date"]
                in_pos = True
                stopped_out_regime = None
                if entry_date >= start_forward_date:
                    forward_trades.append({
                        "date": bar["date"].isoformat(),
                        "market": symbol,
                        "rule": "T2_SMA_CROSS",
                        "signal": direction,
                        "entry": f"{entry_price:.5f}",
                        "stop": f"{stop_loss:.5f}",
                        "exit": "OPEN",
                        "R": "N/A",
                        "R_gross": "N/A",
                        "R_net": "N/A",
                        "status": "POSITION_OPENED",
                    })

    return forward_trades


def evaluate_t3_tsmom(
    symbol: str,
    bars: list[dict[str, Any]],
    start_forward_date: dt.date = START_FORWARD_DATE,
) -> list[dict[str, Any]]:
    """T3 TSMOM: Month-end 252-day return sign, hold while sign persists, 3x ATR stop.

    Returns trades that enter on bars dated >= start_forward_date.
    """
    n = len(bars)
    in_pos = False
    direction = ""
    entry_date: dt.date = dt.date.min
    entry_price = 0.0
    stop_loss = 0.0
    stop_distance = 0.0
    current_episode_sign: int = 0

    forward_trades: list[dict[str, Any]] = []

    for i in range(1, n):
        bar = bars[i]
        prev_bar = bars[i - 1]

        # 1. If in position, check stop first on current bar
        if in_pos:
            is_stopped = False
            exit_price = 0.0

            if direction == "LONG":
                if bar["low"] <= stop_loss:
                    is_stopped = True
                    exit_price = bar["open"] if bar["open"] <= stop_loss else stop_loss
            elif direction == "SHORT":
                if bar["high"] >= stop_loss:
                    is_stopped = True
                    exit_price = bar["open"] if bar["open"] >= stop_loss else stop_loss

            if is_stopped:
                in_pos = False
                if entry_date >= start_forward_date:
                    costs = compute_costs(
                        symbol=symbol,
                        direction=direction,
                        entry_price=entry_price,
                        exit_price=exit_price,
                        stop_distance=stop_distance,
                        is_stop=True,
                        entry_date=entry_date,
                        exit_date=bar["date"],
                    )
                    forward_trades.append({
                        "date": bar["date"].isoformat(),
                        "market": symbol,
                        "rule": "T3_TSMOM",
                        "signal": direction,
                        "entry": f"{entry_price:.5f}",
                        "stop": f"{stop_loss:.5f}",
                        "exit": f"{exit_price:.5f}",
                        "R": f"{costs['net_r']:+.4f}",
                        "R_gross": f"{costs['gross_r']:+.4f}",
                        "R_net": f"{costs['net_r']:+.4f}",
                        "status": "STOP_CLOSED",
                    })
                continue

        # 2. Check if prev_bar was month-end (evaluating return sign)
        if prev_bar["is_month_end"] and i >= 253:
            ret = prev_bar["ret252"]
            new_sign = 1 if ret > 0 else (-1 if ret < 0 else 0)

            if new_sign != 0:
                if new_sign != current_episode_sign:
                    # If in position, exit old trade at bar.open
                    if in_pos:
                        in_pos = False
                        exit_price = bar["open"]
                        if entry_date >= start_forward_date:
                            costs = compute_costs(
                                symbol=symbol,
                                direction=direction,
                                entry_price=entry_price,
                                exit_price=exit_price,
                                stop_distance=stop_distance,
                                is_stop=False,
                                entry_date=entry_date,
                                exit_date=bar["date"],
                            )
                            forward_trades.append({
                                "date": bar["date"].isoformat(),
                                "market": symbol,
                                "rule": "T3_TSMOM",
                                "signal": direction,
                                "entry": f"{entry_price:.5f}",
                                "stop": f"{stop_loss:.5f}",
                                "exit": f"{exit_price:.5f}",
                                "R": f"{costs['net_r']:+.4f}",
                                "R_gross": f"{costs['gross_r']:+.4f}",
                                "R_net": f"{costs['net_r']:+.4f}",
                                "status": "SIGN_FLIP_CLOSED",
                            })

                    # Start new episode
                    current_episode_sign = new_sign

                    # Enter new trade
                    direction = "LONG" if new_sign == 1 else "SHORT"
                    entry_price = bar["open"]
                    stop_dist = 3.0 * prev_bar["atr20"]
                    if stop_dist > 0:
                        stop_distance = stop_dist
                        stop_loss = (entry_price - stop_distance) if direction == "LONG" else (entry_price + stop_distance)
                        entry_date = bar["date"]
                        in_pos = True
                        if entry_date >= start_forward_date:
                            forward_trades.append({
                                "date": bar["date"].isoformat(),
                                "market": symbol,
                                "rule": "T3_TSMOM",
                                "signal": direction,
                                "entry": f"{entry_price:.5f}",
                                "stop": f"{stop_loss:.5f}",
                                "exit": "OPEN",
                                "R": "N/A",
                                "R_gross": "N/A",
                                "R_net": "N/A",
                                "status": "POSITION_OPENED",
                            })

    return forward_trades


def run_forward_log() -> None:
    print("=" * 100)
    print("TASK C: Forward Daily Trade Log Runner (trend_v1.2 T2 & T3 per commit f1affec)")
    print(f"Timestamp UTC: {dt.datetime.now(dt.UTC).isoformat()}")
    print("=" * 100)

    if not mt5.initialize():
        print(f"FATAL: MT5 failed to initialize: {mt5.last_error()}")
        return

    symbols_map = load_symbols()
    print(f"Config loaded: {len(symbols_map)} markets from {CONFIG_PATH}\n")

    all_forward_records: list[dict[str, Any]] = []
    market_audit: list[dict[str, Any]] = []

    for research_sym, meta in symbols_map.items():
        broker_sym = meta["broker_symbol"]
        cleaned_bars, flags = fetch_and_clean_d1_bars(broker_sym, max_bars=1200)

        insufficient_flag = len(cleaned_bars) < REQUIRED_WARMUP_BARS
        insufficient_str = f"[FLAG: BARS < {REQUIRED_WARMUP_BARS}]" if insufficient_flag else "OK"

        market_audit.append({
            "market": research_sym,
            "broker_symbol": broker_sym,
            "raw_bars": flags["total_raw"],
            "cleaned_bars": flags["cleaned_bars"],
            "duplicates_skipped": flags["duplicate_bars"],
            "flat_skipped": flags["flat_bars"],
            "warmup_status": insufficient_str,
            "last_bar_date": cleaned_bars[-1]["date"] if cleaned_bars else None,
        })

        if insufficient_flag:
            print(f"WARNING: {research_sym} has only {len(cleaned_bars)} bars; needs {REQUIRED_WARMUP_BARS} for warm-up!")
            continue

        enriched = compute_indicators(cleaned_bars)
        t2_records = evaluate_t2_sma_cross(research_sym, enriched, start_forward_date=START_FORWARD_DATE)
        t3_records = evaluate_t3_tsmom(research_sym, enriched, start_forward_date=START_FORWARD_DATE)

        all_forward_records.extend(t2_records)
        all_forward_records.extend(t3_records)

    mt5.shutdown()

    # Print Data Quality & Invariant Table
    print(f"{'Market':<8} | {'Exness Sym':<9} | {'Raw':<5} | {'Clean':<5} | {'Dup':<4} | {'Flat':<5} | {'Warmup':<15} | {'Last Bar Date':<13}")
    print("-" * 80)
    for a in market_audit:
        print(
            f"{a['market']:<8} | {a['broker_symbol']:<9} | {a['raw_bars']:<5} | {a['cleaned_bars']:<5} | "
            f"{a['duplicates_skipped']:<4} | {a['flat_skipped']:<5} | {a['warmup_status']:<15} | {a['last_bar_date']!s:<13}"
        )

    print("\n" + "=" * 60)
    print(f"FORWARD LOG RECORDS (dated >= {START_FORWARD_DATE}):")
    print(f"Total trades/signals evaluated on or after {START_FORWARD_DATE}: {len(all_forward_records)}")
    print("=" * 60)

    # Append records to forward_log.csv idempotently
    new_appended = append_forward_log_records(all_forward_records, csv_path=LOG_CSV_PATH)
    print(f"forward_log.csv status: updated -> {LOG_CSV_PATH.resolve()}")
    print(f"New rows written: {new_appended}")

    # Append every run record to forward_run.log
    total_bars_used = sum(a["cleaned_bars"] for a in market_audit)
    now_iso = dt.datetime.now(dt.UTC).isoformat()
    today_str = dt.date.today().isoformat()
    run_log_line = f"timestamp={now_iso} | date={today_str} | bars_used={total_bars_used} | rows_added={new_appended}\n"
    with open(RUN_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(run_log_line)
    print(f"forward_run.log entry: {run_log_line.strip()}")

    if LOG_CSV_PATH.is_file():
        print("\nCurrent forward_log.csv content:")
        with open(LOG_CSV_PATH, encoding="utf-8") as f:
            print(f.read())


def append_forward_log_records(
    records: list[dict[str, Any]],
    csv_path: Path = LOG_CSV_PATH,
) -> int:
    """Append forward records idempotently. Returns count of new rows added."""
    fieldnames = ["date", "market", "rule", "signal", "entry", "stop", "exit", "R", "R_gross", "R_net"]
    existing_keys = set()
    file_existed = csv_path.is_file()
    if file_existed:
        with open(csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                existing_keys.add((
                    str(row.get("date")),
                    str(row.get("market")),
                    str(row.get("rule")),
                    str(row.get("signal")),
                    str(row.get("entry")),
                ))

    new_appended = 0
    with open(csv_path, "a" if file_existed else "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_existed:
            writer.writeheader()

        for rec in records:
            key = (
                str(rec.get("date")),
                str(rec.get("market")),
                str(rec.get("rule")),
                str(rec.get("signal")),
                str(rec.get("entry")),
            )
            if key not in existing_keys:
                writer.writerow({
                    "date": rec["date"],
                    "market": rec["market"],
                    "rule": rec["rule"],
                    "signal": rec["signal"],
                    "entry": rec["entry"],
                    "stop": rec["stop"],
                    "exit": rec["exit"],
                    "R": rec["R"],
                    "R_gross": rec["R_gross"],
                    "R_net": rec["R_net"],
                })
                existing_keys.add(key)
                new_appended += 1

    return new_appended


if __name__ == "__main__":
    run_forward_log()
