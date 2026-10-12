"""Daily forward logging runner for trend_v1.2 T2 and T3 strategies.

Pre-registration: research/prereg/trend_v1.2.md (commit f1affec).
Applies T2 (SMA 50/200 cross, 3xATR(20) stop) and T3 (12-month TSMOM sign flip, 3xATR(20) stop).
Zero live engine imports. Zero MT5 order execution.
Reads completed MT5 D1 bars, warm-ups on bars < 2026-10-12, and logs trades on bars >= 2026-10-12.
"""

from __future__ import annotations

import csv
import datetime as dt
import sys
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
    mt5_client: Any = mt5,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Fetch MT5 D1 bars, filter flat/duplicate bars, enforce geometric sanity."""
    flags = {"duplicate_bars": 0, "flat_bars": 0, "total_raw": 0, "cleaned_bars": 0}

    mt5_client.symbol_select(broker_symbol, True)
    # Start at pos 1 to strictly fetch completed/closed bars (index 0 is still-forming)
    rates = mt5_client.copy_rates_from_pos(broker_symbol, mt5_client.TIMEFRAME_D1, 1, max_bars)
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

    cleaned = fold_sunday_into_monday(cleaned)
    flags["cleaned_bars"] = len(cleaned)
    return cleaned, flags


def fold_sunday_into_monday(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fold any Sunday D1 bar into the subsequent Monday D1 bar per canonical convention.

    If a Sunday bar is followed by a Monday bar, they are merged into Monday:
    - date: Monday's date
    - open: Sunday's open
    - high: max(Sunday's high, Monday's high)
    - low: min(Sunday's low, Monday's low)
    - close: Monday's close
    - volume: Sunday's volume + Monday's volume
    """
    folded: list[dict[str, Any]] = []
    i = 0
    n = len(bars)
    while i < n:
        curr = bars[i]
        curr_date = curr["date"]
        # If curr is Sunday (weekday == 6) and next bar is Monday (weekday == 0)
        if curr_date.weekday() == 6 and i + 1 < n:
            nxt = bars[i + 1]
            if nxt["date"].weekday() == 0:
                merged_bar = {
                    "date": nxt["date"],
                    "open": curr["open"],
                    "high": max(curr["high"], nxt["high"]),
                    "low": min(curr["low"], nxt["low"]),
                    "close": nxt["close"],
                    "volume": int(curr.get("volume", 0)) + int(nxt.get("volume", 0)),
                    "is_forming": False,
                }
                folded.append(merged_bar)
                i += 2
                continue
        folded.append(dict(curr))
        i += 1
    return folded


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


def run_forward_log(
    log_csv_path: Path = LOG_CSV_PATH,
    run_log_path: Path = RUN_LOG_PATH,
    mt5_client: Any = mt5,
    now: dt.datetime | None = None,
) -> None:
    print("=" * 100)
    print("TASK C: Forward Daily Trade Log Runner (trend_v1.2 T2 & T3 per commit f1affec)")
    print(f"Timestamp UTC: {dt.datetime.now(dt.UTC).isoformat()}")
    print("=" * 100)

    now_dt = now or dt.datetime.now(dt.UTC)
    now_iso = now_dt.isoformat()
    today_str = now_dt.date().isoformat()
    today_weekday = now_dt.date().weekday()

    if not mt5_client.initialize():
        last_err = mt5_client.last_error() if hasattr(mt5_client, "last_error") else "Unknown"
        err_msg = f"MT5 failed to initialize: {last_err}"
        print(f"FATAL: {err_msg}")
        run_log_line = f"timestamp={now_iso} | date={today_str} | status=FAILED | error={err_msg}\n"
        with open(run_log_path, "a", encoding="utf-8") as f:
            f.write(run_log_line)
        sys.exit(1)

    symbols_map = load_symbols()
    print(f"Config loaded: {len(symbols_map)} markets from {CONFIG_PATH}\n")

    all_forward_records: list[dict[str, Any]] = []
    market_audit: list[dict[str, Any]] = []

    for research_sym, meta in symbols_map.items():
        broker_sym = meta["broker_symbol"]
        cleaned_bars, flags = fetch_and_clean_d1_bars(broker_sym, max_bars=1200, mt5_client=mt5_client)

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

    mt5_client.shutdown()

    # Check if new bars arrived
    total_bars_used = sum(a["cleaned_bars"] for a in market_audit)
    latest_bar_date = max(
        (a["last_bar_date"] for a in market_audit if a.get("last_bar_date")),
        default=None,
    )

    is_weekend = today_weekday in (5, 6)
    is_monday_morning = (today_weekday == 0 and latest_bar_date is not None and latest_bar_date.weekday() in (4, 6))

    missing_market_bar = False
    if not is_weekend and not is_monday_morning:
        if any(a["cleaned_bars"] == 0 for a in market_audit):
            missing_market_bar = True
        elif latest_bar_date and any(a["last_bar_date"] is None or a["last_bar_date"] < latest_bar_date for a in market_audit):
            missing_market_bar = True

    if missing_market_bar:
        err_msg = "Market missing bar on a normal trading day"
        print(f"ERROR: {err_msg}")
        run_log_line = f"timestamp={now_iso} | date={today_str} | status=FAILED | error={err_msg}\n"
        with open(run_log_path, "a", encoding="utf-8") as f:
            f.write(run_log_line)
        sys.exit(2)

    no_new_bars = False
    if total_bars_used == 0 or latest_bar_date is None:
        no_new_bars = True
    elif run_log_path.is_file():
        with open(run_log_path, encoding="utf-8") as f:
            for line in reversed(f.readlines()):
                if "status=FAILED" not in line and f"latest_bar={latest_bar_date}" in line:
                    no_new_bars = True
                    break

    if no_new_bars:
        print(f"NO NEW BARS EXPECTED: latest closed bar {latest_bar_date} already processed.")
        run_log_line = f"timestamp={now_iso} | date={today_str} | status=NO_NEW_BARS_EXPECTED | bars_used={total_bars_used} | rows_added=0\n"
        with open(run_log_path, "a", encoding="utf-8") as f:
            f.write(run_log_line)
        sys.exit(0)

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
    new_appended = append_forward_log_records(all_forward_records, csv_path=log_csv_path)
    print(f"forward_log.csv status: updated -> {log_csv_path.resolve()}")
    print(f"New rows written: {new_appended}")

    # Append every run record to forward_run.log
    run_log_line = f"timestamp={now_iso} | date={today_str} | latest_bar={latest_bar_date} | bars_used={total_bars_used} | rows_added={new_appended}\n"
    with open(run_log_path, "a", encoding="utf-8") as f:
        f.write(run_log_line)
    print(f"forward_run.log entry: {run_log_line.strip()}")

    if log_csv_path.is_file():
        print("\nCurrent forward_log.csv content:")
        with open(log_csv_path, encoding="utf-8") as f:
            print(f.read())

    # Summary performance metrics
    stats = compute_forward_stats(csv_path=log_csv_path)
    print("\n" + "=" * 60)
    print("FORWARD LOG PERFORMANCE SUMMARY (Requirement 4: OPEN + exit = 1 trade):")
    print(f"  Total Trades: {stats['total_trades']} [FLAG: N < 200, N={stats['total_trades']}]")
    print(f"  Open Trades: {stats['open_trades']}")
    print(f"  Closed Trades: {stats['closed_trades']}")
    if stats["closed_trades"] > 0:
        print(f"  Win Rate: {stats['win_rate']:.2f}% ({stats['wins']}/{stats['closed_trades']})")
        print(f"  Mean Net R: {stats['mean_net_r']:+.4f}R")
        print(f"  Cum Net R: {stats['cum_net_r']:+.4f}R")
        print(f"  Max Drawdown: {stats['max_dd']:.4f}R")
    else:
        print("  Win Rate: N/A (0 closed trades)")
        print("  Mean Net R: 0.0000R")
        print("  Cum Net R: 0.0000R")
        print("  Max Drawdown: 0.0000R")
    print("=" * 60)


def append_forward_log_records(
    records: list[dict[str, Any]],
    csv_path: Path = LOG_CSV_PATH,
) -> int:
    """Append forward records idempotently. Returns count of new rows added."""
    fieldnames = ["date", "market", "rule", "signal", "entry", "stop", "exit", "R", "R_gross", "R_net"]

    def _norm(v: Any) -> str:
        return "" if v is None else str(v)

    existing_keys = set()
    file_existed = csv_path.is_file()
    if file_existed:
        with open(csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                existing_keys.add((
                    _norm(row.get("date")),
                    _norm(row.get("market")),
                    _norm(row.get("rule")),
                    _norm(row.get("signal")),
                    _norm(row.get("entry")),
                    _norm(row.get("exit")),
                ))

    new_appended = 0
    with open(csv_path, "a" if file_existed else "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_existed:
            writer.writeheader()

        for rec in records:
            key = (
                _norm(rec.get("date")),
                _norm(rec.get("market")),
                _norm(rec.get("rule")),
                _norm(rec.get("signal")),
                _norm(rec.get("entry")),
                _norm(rec.get("exit")),
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


def compute_forward_stats(csv_path: Path = LOG_CSV_PATH) -> dict[str, Any]:
    """Compute summary performance statistics on forward_log.csv.

    Invariant (Requirement 4):
    An OPEN row and its later exit row for the same trade (matched by market, rule,
    signal, entry, stop) are counted as ONE single trade.
    """
    if not csv_path.is_file():
        return {
            "total_trades": 0,
            "open_trades": 0,
            "closed_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "mean_net_r": 0.0,
            "cum_net_r": 0.0,
            "max_dd": 0.0,
            "trades": [],
        }

    trades_map: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    trade_order: list[tuple[str, str, str, str, str]] = []

    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            market = row.get("market", "").strip()
            rule = row.get("rule", "").strip()
            signal = row.get("signal", "").strip()
            entry = row.get("entry", "").strip()
            stop = row.get("stop", "").strip()
            exit_val = row.get("exit", "").strip()
            r_net_str = row.get("R_net", "").strip()
            r_gross_str = row.get("R_gross", "").strip()
            row_date = row.get("date", "").strip()

            if not market or not rule:
                continue

            trade_key = (market, rule, signal, entry, stop)

            if trade_key not in trades_map:
                trades_map[trade_key] = {
                    "market": market,
                    "rule": rule,
                    "signal": signal,
                    "entry_date": row_date,
                    "exit_date": None,
                    "entry": float(entry) if entry else 0.0,
                    "stop": float(stop) if stop else 0.0,
                    "exit": exit_val,
                    "status": "OPEN" if exit_val == "OPEN" else "CLOSED",
                    "R_net": None,
                    "R_gross": None,
                }
                trade_order.append(trade_key)

            t = trades_map[trade_key]
            if exit_val == "OPEN":
                t["entry_date"] = row_date
            else:
                t["exit_date"] = row_date
                t["exit"] = exit_val
                t["status"] = "CLOSED"
                try:
                    t["R_net"] = float(r_net_str)
                except (ValueError, TypeError):
                    t["R_net"] = 0.0
                try:
                    t["R_gross"] = float(r_gross_str)
                except (ValueError, TypeError):
                    t["R_gross"] = 0.0

    all_trades = [trades_map[k] for k in trade_order]
    closed_trades = [t for t in all_trades if t["status"] == "CLOSED"]
    open_trades = [t for t in all_trades if t["status"] == "OPEN"]

    n_closed = len(closed_trades)
    wins = [t for t in closed_trades if t["R_net"] is not None and t["R_net"] > 0]
    losses = [t for t in closed_trades if t["R_net"] is not None and t["R_net"] < 0]
    win_rate = (len(wins) / n_closed * 100.0) if n_closed > 0 else 0.0

    r_vals = [t["R_net"] for t in closed_trades if t["R_net"] is not None]
    mean_net_r = (sum(r_vals) / n_closed) if n_closed > 0 else 0.0
    cum_net_r = sum(r_vals) if r_vals else 0.0

    cum_series = []
    running = 0.0
    for r in r_vals:
        running += r
        cum_series.append(running)

    max_dd = 0.0
    if cum_series:
        peak = 0.0
        for val in cum_series:
            if val > peak:
                peak = val
            dd = peak - val
            if dd > max_dd:
                max_dd = dd

    return {
        "total_trades": len(all_trades),
        "open_trades": len(open_trades),
        "closed_trades": n_closed,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 2),
        "mean_net_r": round(mean_net_r, 4),
        "cum_net_r": round(cum_net_r, 4),
        "max_dd": round(max_dd, 4),
        "trades": all_trades,
    }


if __name__ == "__main__":
    if "--stats" in sys.argv or "--summary" in sys.argv:
        res = compute_forward_stats()
        print("=" * 60)
        print("FORWARD LOG PERFORMANCE SUMMARY (Requirement 4: OPEN + exit = 1 trade):")
        print(f"  Total Trades: {res['total_trades']} [FLAG: N < 200, N={res['total_trades']}]")
        print(f"  Open Trades: {res['open_trades']}")
        print(f"  Closed Trades: {res['closed_trades']}")
        if res["closed_trades"] > 0:
            print(f"  Win Rate: {res['win_rate']:.2f}% ({res['wins']}/{res['closed_trades']})")
            print(f"  Mean Net R: {res['mean_net_r']:+.4f}R")
            print(f"  Cum Net R: {res['cum_net_r']:+.4f}R")
            print(f"  Max Drawdown: {res['max_dd']:.4f}R")
        else:
            print("  Win Rate: N/A (0 closed trades)")
            print("  Mean Net R: 0.0000R")
            print("  Cum Net R: 0.0000R")
            print("  Max Drawdown: 0.0000R")
        print("=" * 60)
    else:
        run_forward_log()
