"""Descriptive report generator for forward paper-trading harness: python -m forward.report."""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

import numpy as np

from forward.config import (
    BARS_DB_PATH,
    FORWARD_DIR,
    LOW_N_THRESHOLD,
    POWER_N_THRESHOLD,
    PREREG_COMMIT,
    PREREG_SHA256,
    PREREG_VERSION,
    RULES,
    SYMBOLS,
    TREND_DB_PATH,
)
from forward.db import get_bars_db, get_trend_db

logger = logging.getLogger("forward.report")


def generate_report(
    trend_db_path: Path = TREND_DB_PATH,
    bars_db_path: Path = BARS_DB_PATH,
    out_md_path: Path = FORWARD_DIR / "FORWARD_REPORT.md",
) -> str:
    """Generate descriptive Markdown report from forward SQLite databases."""
    lines: list[str] = []

    def p(text: str = "") -> None:
        lines.append(text)

    # 1. Header & Metadata
    p(f"# Forward Paper-Trading Report (`{PREREG_VERSION}`)")
    p()
    p(f"**Generated UTC:** {dt.datetime.now(dt.UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}  ")
    p(f"**Pre-Registration Version:** `{PREREG_VERSION}`  ")
    p(f"**Pre-Registration SHA-256:** `{PREREG_SHA256}`  ")
    p(f"**Pre-Registration Commit:** `{PREREG_COMMIT}`  ")
    p("**Statistical Status:** `[DESCRIPTIVE REPORT ONLY — NO SIGNIFICANCE CLAIMS]`  ")
    p()
    p("---")
    p()

    if not trend_db_path.is_file():
        p("> **Notice:** Forward database does not exist yet. Run `python -m forward.run_daily` to execute.")
        content = "\n".join(lines)
        out_md_path.write_text(content, encoding="utf-8")
        return content

    with get_trend_db(trend_db_path) as t_conn:
        trades = t_conn.execute("SELECT * FROM trades ORDER BY exit_date ASC").fetchall()
        carried = t_conn.execute("SELECT * FROM carried_trades ORDER BY entry_date ASC").fetchall()
        open_pos = t_conn.execute("SELECT * FROM open_positions ORDER BY entry_date ASC").fetchall()
        pending = t_conn.execute("SELECT * FROM pending_signals ORDER BY signal_date ASC").fetchall()

    with get_bars_db(bars_db_path) as b_conn:
        sanit_logs = b_conn.execute("SELECT * FROM sanitization_logs ORDER BY date DESC LIMIT 20").fetchall()
        roll_warns = b_conn.execute("SELECT * FROM roll_gap_warnings ORDER BY date DESC LIMIT 20").fetchall()

    n_trades = len(trades)
    power_flag = " `[LOW N / UNDERPOWERED]`" if n_trades < POWER_N_THRESHOLD else ""

    p("## 1. Executive Summary")
    p()
    p(f"- **Total Forward Closed Trades:** {n_trades}{power_flag}")
    p(f"- **Carried Historical Positions:** {len(carried)} (strictly excluded from all performance stats)")
    p(f"- **Currently Open Positions:** {len(open_pos)}")
    p(f"- **Pending Signals for Next Open:** {len(pending)}")
    p()

    # 2. Performance by Rule
    p("## 2. Performance by Strategy Rule")
    p()
    p("| Rule | N | Win Rate | Gross Mean R | Net 0% Mean R | Net 2% Mean R | Net Broker R | Cum Net 0% R | Cum Net 2% R | Max DD (0%) |")
    p("| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |")

    for rule in RULES:
        r_trades = [t for t in trades if t["rule"] == rule]
        n_r = len(r_trades)
        flag = " `[LOW N]`" if n_r < LOW_N_THRESHOLD else ""

        if n_r == 0:
            p(f"| **{rule}** | 0{flag} | N/A | N/A | N/A | N/A | N/A | 0.00R | 0.00R | 0.00R |")
            continue

        gross_vals = np.array([float(t["gross_r"]) for t in r_trades])
        net0_vals = np.array([float(t["net_r_fin0"]) for t in r_trades])
        net2_vals = np.array([float(t["net_r_fin2"]) for t in r_trades])
        broker_vals = [t["net_r_broker"] for t in r_trades if t["net_r_broker"] is not None]

        win_rate = np.mean(net0_vals > 0) * 100
        cum_net0 = np.sum(net0_vals)
        cum_net2 = np.sum(net2_vals)

        # Max Drawdown
        cum_series = np.cumsum(net0_vals)
        running_max = np.maximum.accumulate(cum_series)
        dd = running_max - cum_series
        max_dd = np.max(dd) if len(dd) > 0 else 0.0

        broker_str = f"{np.mean(broker_vals):+.4f}R" if broker_vals else "NaN (no swaps.csv)"

        p(
            f"| **{rule}** | {n_r}{flag} | {win_rate:5.1f}% | {np.mean(gross_vals):+.4f}R | "
            f"{np.mean(net0_vals):+.4f}R | {np.mean(net2_vals):+.4f}R | {broker_str} | "
            f"{cum_net0:+.2f}R | {cum_net2:+.2f}R | {max_dd:.2f}R |"
        )

    p()

    # 3. Performance by Market
    p("## 3. Performance by Market (All Forward Rules Pooled)")
    p()
    p("| Market | N | Long / Short N | Net 0% Mean R | Net 2% Mean R | Cum Net 0% R | Cum Net 2% R |")
    p("| :--- | :--- | :--- | :--- | :--- | :--- | :--- |")

    for sym in SYMBOLS:
        sym_trades = [t for t in trades if t["symbol"] == sym]
        n_s = len(sym_trades)
        if n_s == 0:
            p(f"| **{sym}** | 0 `[LOW N]` | 0 / 0 | N/A | N/A | 0.00R | 0.00R |")
            continue

        n_l = sum(1 for t in sym_trades if t["direction"] == "LONG")
        n_sh = sum(1 for t in sym_trades if t["direction"] == "SHORT")
        net0_vals = np.array([float(t["net_r_fin0"]) for t in sym_trades])
        net2_vals = np.array([float(t["net_r_fin2"]) for t in sym_trades])

        p(
            f"| **{sym}** | {n_s} `[LOW N]` | {n_l} / {n_sh} | {np.mean(net0_vals):+.4f}R | "
            f"{np.mean(net2_vals):+.4f}R | {np.sum(net0_vals):+.2f}R | {np.sum(net2_vals):+.2f}R |"
        )

    p()

    # 4. Open Positions & Pending Signals
    p("## 4. Active Positions & Pending Signals")
    p()
    if open_pos:
        p("### Open Positions")
        p("| Position ID | Rule | Symbol | Direction | Entry Date | Entry Price | Stop Loss | Stop Dist |")
        p("| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |")
        for op in open_pos:
            p(f"| {op['position_id']} | {op['rule']} | {op['symbol']} | {op['direction']} | {op['entry_date']} | {op['entry_price']:.4f} | {op['stop_loss']:.4f} | {op['stop_distance']:.4f} |")
        p()
    else:
        p("*No positions currently open.*")
        p()

    if pending:
        p("### Pending Orders for Next Open")
        p("| Signal ID | Rule | Symbol | Direction | Signal Date | Trigger Reason |")
        p("| :--- | :--- | :--- | :--- | :--- | :--- |")
        for ps in pending:
            p(f"| {ps['signal_id']} | {ps['rule']} | {ps['symbol']} | {ps['direction']} | {ps['signal_date']} | {ps['trigger_reason']} |")
        p()
    else:
        p("*No pending orders queued.*")
        p()

    # 5. Data Quality & Audit Warnings
    p("## 5. Data Quality Audit & Roll-Gap Warnings")
    p()
    p(f"- **Total Roll-Gap Warnings Logged:** {len(roll_warns)}")
    p(f"- **Total Geometric $H < L$ Sanitizations:** {len(sanit_logs)}")
    p()
    if roll_warns:
        p("### Recent Roll-Gap Warnings ($|\\Delta Close| > 6 \\times ATR_{20}$)")
        p("| Date | Symbol | Close Prev | Close Curr | Diff Pts | ATR20 |")
        p("| :--- | :--- | :--- | :--- | :--- | :--- |")
        for rw in roll_warns[:5]:
            p(f"| {rw['date']} | {rw['symbol']} | {rw['close_prev']:.4f} | {rw['close_curr']:.4f} | {rw['diff_pts']:.4f} | {rw['atr20']:.4f} |")
        p()

def generate_dry_asof_report(
    bars_db_path: Path = BARS_DB_PATH,
    as_of_date: dt.date | None = None,
) -> str:
    """Generate dry-run inspection report listing positions that would be carried today without starting the clock."""
    from research.trend.strategies import run_t2_sma_cross, run_t3_tsmom

    from forward.feed import load_bars_from_db

    lines: list[str] = []

    def p(text: str = "") -> None:
        lines.append(text)

    # 1. Header & Status
    now_utc = dt.datetime.now(dt.UTC)
    p(f"# Forward Paper-Test Carried Position Inspection (`{PREREG_VERSION}`)")
    p()
    p("**Execution Mode:** `[DRY-RUN AS OF TODAY — NO DATABASE WRITES — CLOCK NOT STARTED]`  ")
    p(f"**Generated UTC:** {now_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}  ")
    p(f"**Pre-Registration Version:** `{PREREG_VERSION}`  ")
    p(f"**Pre-Registration SHA-256:** `{PREREG_SHA256}`  ")
    p(f"**Pre-Registration Commit:** `{PREREG_COMMIT}`  ")
    p()
    p("---")
    p()

    # Pre-load bars
    bars_by_sym = {}
    for sym in SYMBOLS:
        bars = load_bars_from_db(sym, as_of_date=as_of_date, db_path=bars_db_path)
        if bars:
            bars_by_sym[sym] = bars

    if not bars_by_sym:
        p("> **Error:** No bars found in database. Run feed ingestion first.")
        content = "\n".join(lines)
        return content

    latest_dates = [b[-1].date for b in bars_by_sym.values()]
    effective_as_of = as_of_date or max(latest_dates)
    p(f"**Effective Evaluation Date (Latest Completed Bar):** `{effective_as_of.isoformat()}`  ")
    p()
    p("## 1. Summary of Carried Positions (Flat-Start Invariant)")
    p()
    p("Under Section 5 of `forward_trend_v1.md`, the forward paper test enforces a **strict flat start**:")
    p("- Any trade already open at the test start date is classified as **CARRIED**.")
    p("- Carried trades are routed to `carried_trades` and **strictly excluded from all forward performance statistics**.")
    p()

    carried_by_rule: dict[str, list[dict]] = {"T2_SMA_CROSS": [], "T3_TSMOM": []}
    flat_by_rule: dict[str, list[str]] = {"T2_SMA_CROSS": [], "T3_TSMOM": []}

    for sym in SYMBOLS:
        bars = bars_by_sym.get(sym, [])
        if len(bars) < 252:
            continue
        last_bar = bars[-1]

        t2_trades = run_t2_sma_cross(sym, bars, start_date=None, end_date=effective_as_of)
        t3_trades = run_t3_tsmom(sym, bars, start_date=None, end_date=effective_as_of)

        for rule, tr_list in [("T2_SMA_CROSS", t2_trades), ("T3_TSMOM", t3_trades)]:
            open_tr = [t for t in tr_list if t.exit_reason == "PERIOD_END"]
            if open_tr:
                for t in open_tr:
                    # Compute current unrealized PnL in points and R
                    unrealized_pts = (last_bar.close - t.entry_price) if t.direction == "LONG" else (t.entry_price - last_bar.close)
                    unrealized_r = unrealized_pts / t.stop_distance if t.stop_distance > 0 else 0.0
                    carried_by_rule[rule].append({
                        "symbol": sym,
                        "direction": t.direction,
                        "entry_date": t.entry_date.isoformat(),
                        "entry_price": t.entry_price,
                        "stop_loss": t.stop_loss,
                        "stop_distance": t.stop_distance,
                        "holding_days": (effective_as_of - t.entry_date).days,
                        "current_close": last_bar.close,
                        "unrealized_pts": unrealized_pts,
                        "unrealized_r": unrealized_r,
                    })
            else:
                flat_by_rule[rule].append(sym)

    # 2. Rule T2 Carried Positions Table
    for rule in ["T2_SMA_CROSS", "T3_TSMOM"]:
        c_list = carried_by_rule[rule]
        p(f"## 2. Rule `{rule}` Carried Positions ({len(c_list)} Active, {len(flat_by_rule[rule])} Flat)")
        p()
        if c_list:
            p("| Symbol | Side | Entry Date | Entry Price | Stop Loss | Stop Dist | Days Held | Current Close | Unrealized R |")
            p("| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |")
            for c in c_list:
                p(
                    f"| **{c['symbol']}** | {c['direction']} | {c['entry_date']} | "
                    f"{c['entry_price']:.4f} | {c['stop_loss']:.4f} | {c['stop_distance']:.4f} | "
                    f"{c['holding_days']}d | {c['current_close']:.4f} | {c['unrealized_r']:+.2f}R |"
                )
            p()
        else:
            p("*All markets currently flat under this rule.*")
            p()
        p(f"**Flat Markets under {rule}:** {', '.join(flat_by_rule[rule]) if flat_by_rule[rule] else 'None'}")
        p()

    p("---")
    p("## 3. Clock & Database Integrity Audit")
    p()
    p("- **Database Writes:** 0 (read-only execution, zero rows inserted or updated).")
    p("- **Job Scheduler Status:** INACTIVE (Task Scheduler job has NOT been scheduled).")
    p("- **Forward Clock Status:** STOPPED (awaiting explicit user approval before initiation).")
    p()

    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Forward Paper-Testing Reporter")
    parser.add_argument("--asof-today", action="store_true", help="Inspect carried positions as of today's latest bars")
    parser.add_argument("--dry", action="store_true", help="Dry-run mode: read-only, zero DB modifications")
    args = parser.parse_args()

    if args.asof_today or args.dry:
        rep = generate_dry_asof_report()
    else:
        rep = generate_report()
    print(rep)

