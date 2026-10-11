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

    p("---")
    p("*(Report automatically generated by `python -m forward.report`. All figures computed from database records.)*")

    content = "\n".join(lines)
    out_md_path.write_text(content, encoding="utf-8")
    return content


if __name__ == "__main__":
    rep = generate_report()
    print(rep)
