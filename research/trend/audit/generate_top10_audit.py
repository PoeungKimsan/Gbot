"""Generate Top-10 Trade Audit CSV for T2 and T3 at 0% carry."""

from __future__ import annotations

import csv
from pathlib import Path
from research.trend.indicators import load_daily_bars

DATA_DIR = Path("research/trend/data_fxfixed")
OUTPUT_DIR = Path("research/trend/output_fxfixed")
AUDIT_DIR = Path("research/trend/audit")

SYMBOLS = [
    "XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "USDJPY",
    "AUDUSD", "USDCAD", "USDCHF", "US500", "GER40", "Brent", "NDX",
]


def main() -> None:
    bars_by_sym = {s: load_daily_bars(DATA_DIR / f"{s}_daily.csv") for s in SYMBOLS}

    rows = []

    for rule in ["T2_SMA_CROSS", "T3_TSMOM"]:
        with open(OUTPUT_DIR / f"{rule}_trades.csv", encoding="utf-8") as f:
            all_trades = list(csv.DictReader(f))
        trade_map = {(t["symbol"], t["entry_date"]): t for t in all_trades}

        with open(AUDIT_DIR / "concentration_top10_trades.csv", encoding="utf-8") as f:
            top10 = [r for r in csv.DictReader(f) if r["rule"] == rule]

        for item in top10:
            sym = item["symbol"]
            entry_d = item["entry_date"]
            t = trade_map.get((sym, entry_d))
            if t is None:
                # Fallback to trade_id
                t = next((x for x in all_trades if x["trade_id"] == item["trade_id"]), None)
            if t is None:
                continue
            tid = t["trade_id"]
            direction = t["direction"]
            bars = bars_by_sym[sym]
            date_to_idx = {b.date.isoformat(): i for i, b in enumerate(bars)}
            idx = date_to_idx[t["entry_date"]]
            sig_bar = bars[idx - 1]

            entry_p = float(t["entry_price"])
            exit_p = float(t["exit_price"])
            stop_dist = float(t["stop_distance"])
            stop_pct = (stop_dist / entry_p) * 100.0
            atr_pct = (sig_bar.atr20 / entry_p) * 100.0
            net_r_0 = float(t["net_r_fin0"])
            flag = "FLAG (R>15 & stop<2%)" if (net_r_0 > 15.0 and stop_pct < 2.0) else "OK"

            if rule == "T2_SMA_CROSS":
                sig_val = f"SMA50={sig_bar.sma50:.2f}, SMA200={sig_bar.sma200:.2f}"
            else:
                sig_val = f"12m_ret={sig_bar.return_252 * 100:+.2f}%"

            rows.append({
                "rule": rule,
                "rank": item["rank"],
                "trade_id": tid,
                "market": sym,
                "side": direction,
                "signal_date": sig_bar.date.isoformat(),
                "entry_date": t["entry_date"],
                "exit_date": t["exit_date"],
                "entry_price": f"{entry_p:.4f}",
                "exit_price": f"{exit_p:.4f}",
                "stop_distance": f"{stop_dist:.4f}",
                "stop_pct_price": f"{stop_pct:.2f}%",
                "atr20_pct": f"{atr_pct:.2f}%",
                "signal_values": sig_val,
                "net_r_0": f"{net_r_0:+.2f}",
                "flag": flag,
            })

    # Add Silver 2020-04-14 audit row for explicit reconciliation
    # As audited, on 2020-04-14 Silver price was ~15.50. T1 trade T1_XAGUSD_0084 (+7.34R Net 0%) entered 2020-05-20.
    # Neither T2 nor T3 generated a trade on 2020-04-14.
    xag_bars = bars_by_sym["XAGUSD"]
    xag_map = {b.date.isoformat(): b for b in xag_bars}
    b_20200414 = xag_map.get("2020-04-14")
    atr_pct_xag = (b_20200414.atr20 / b_20200414.close) * 100.0 if b_20200414 else 0.0

    rows.append({
        "rule": "AUDIT_QUERY",
        "rank": "REF",
        "trade_id": "Silver_2020-04-14",
        "market": "XAGUSD",
        "side": "N/A",
        "signal_date": "2020-04-14",
        "entry_date": "N/A",
        "exit_date": "N/A",
        "entry_price": f"{b_20200414.close:.4f}" if b_20200414 else "N/A",
        "exit_price": "N/A",
        "stop_distance": "N/A",
        "stop_pct_price": "N/A",
        "atr20_pct": f"{atr_pct_xag:.2f}%" if b_20200414 else "N/A",
        "signal_values": f"SMA50={b_20200414.sma50:.2f}, SMA200={b_20200414.sma200:.2f}, 12m={b_20200414.return_252 * 100:+.2f}%" if b_20200414 else "N/A",
        "net_r_0": "+27.34 (NON-EXISTENT)",
        "flag": "DISCREPANCY: Transcribed level/T1 mixup; 0 trades in T2/T3",
    })

    out_file = AUDIT_DIR / "top10_audit.csv"
    with open(out_file, "w", newline="", encoding="utf-8") as f_out:
        writer = csv.DictWriter(f_out, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"Saved {len(rows)} audit rows to {out_file}")


if __name__ == "__main__":
    main()
