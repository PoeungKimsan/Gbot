"""Generate CSV artifacts for Sigma, Long/Short, Engine inspection, and Last Bar Dates."""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path
import numpy as np

OUTPUT_DIR = Path("research/trend/output_fxfixed")
OUTPUT_BASE_DIR = Path("research/trend/output")
AUDIT_DIR = Path("research/trend/audit")
BARS_DB_PATH = Path("forward/data/forward_bars.sqlite")
JOURNAL_DB_PATH = Path("journal.db")

RULES = ["T1_DONCHIAN", "T2_SMA_CROSS", "T3_TSMOM"]


def generate_sigma_csv() -> None:
    rows = []
    for rule in RULES:
        with open(OUTPUT_DIR / f"{rule}_trades.csv", encoding="utf-8") as f:
            trades = list(csv.DictReader(f))
        gross = [float(t["gross_r"]) for t in trades]
        net0 = [float(t["net_r_fin0"]) for t in trades]
        net2 = [float(t["net_r"]) for t in trades]
        net4 = [float(t["net_r_fin4"]) for t in trades]
        n = len(trades)
        rows.append({
            "rule": rule,
            "n": n,
            "sigma_gross": f"{np.std(gross, ddof=1):.4f}",
            "sigma_net_0": f"{np.std(net0, ddof=1):.4f}",
            "sigma_net_2": f"{np.std(net2, ddof=1):.4f}",
            "sigma_net_4": f"{np.std(net4, ddof=1):.4f}",
            "low_n_flag": "[LOW N]" if n < 200 else "OK",
        })

    out_file = AUDIT_DIR / "sigma_audit.csv"
    with open(out_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Saved sigma audit to {out_file}")


def generate_long_short_csv() -> None:
    rows = []
    # Using baseline research dataset (output/) as audited
    for rule in RULES:
        with open(OUTPUT_BASE_DIR / f"{rule}_trades.csv", encoding="utf-8") as f:
            trades = list(csv.DictReader(f))
        for direction in ["LONG", "SHORT"]:
            sub = [t for t in trades if t["direction"] == direction]
            n = len(sub)
            gross = np.mean([float(t["gross_r"]) for t in sub]) if n else 0.0
            net0 = np.mean([float(t["net_r_fin0"]) for t in sub]) if n else 0.0
            net2 = np.mean([float(t["net_r"]) for t in sub]) if n else 0.0
            rows.append({
                "rule": rule,
                "side": direction,
                "n": n,
                "gross_mean_r": f"{gross:+.4f}",
                "net_mean_r_0": f"{net0:+.4f}",
                "net_mean_r_2": f"{net2:+.4f}",
                "low_n_flag": "[LOW N]" if n < 200 else "OK",
            })

    out_file = AUDIT_DIR / "long_short_audit.csv"
    with open(out_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Saved long/short audit to {out_file}")


def generate_last_bar_dates_csv() -> None:
    rows = []
    conn = sqlite3.connect(BARS_DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT symbol, min(date), max(date), count(*), max(fetch_timestamp) "
        "FROM bars GROUP BY symbol ORDER BY symbol"
    )
    for sym, min_d, max_d, cnt, fetch_ts in cursor.fetchall():
        rows.append({
            "market": sym,
            "first_bar_date": min_d,
            "last_bar_date": max_d,
            "total_bars": cnt,
            "fetch_timestamp_utc": fetch_ts,
        })
    conn.close()

    out_file = AUDIT_DIR / "last_bar_dates.csv"
    with open(out_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Saved last bar dates audit to {out_file}")


def generate_engine_readonly_csv() -> None:
    rows = []
    uri = f"file:{JOURNAL_DB_PATH.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    cursor = conn.cursor()
    cursor.execute("SELECT count(*) FROM journal")
    count_journal = cursor.fetchone()[0]
    conn.close()

    rows.append({
        "daemon_role": "Live MT5 Daemon",
        "registered_strategy": "JudasSwingStrategy",
        "instrument": "XAUUSD (Gold)",
        "journal_table": "journal",
        "trade_count": count_journal,
        "access_mode": "READ-ONLY (SELECT)",
    })

    out_file = AUDIT_DIR / "engine_readonly_audit.csv"
    with open(out_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Saved engine readonly audit to {out_file}")


if __name__ == "__main__":
    generate_sigma_csv()
    generate_long_short_csv()
    generate_last_bar_dates_csv()
    generate_engine_readonly_csv()
