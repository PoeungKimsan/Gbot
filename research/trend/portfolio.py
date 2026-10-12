"""Equal-risk portfolio simulation and drawdown calculation (report-only)."""

from __future__ import annotations

import csv
import datetime as dt
import json
import logging
from pathlib import Path
from typing import Any

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("trend.portfolio")

OUTPUT_DIR = Path("research/trend/output")
RULES = ["T1_DONCHIAN", "T2_SMA_CROSS", "T3_TSMOM"]


def compute_portfolio_drawdown(trades: list[dict[str, Any]], net_key: str = "net_r") -> dict[str, Any]:
    if not trades:
        return {"max_drawdown_r": 0.0, "total_r": 0.0, "calmar_ratio": 0.0}

    # Sort trades chronologically by exit date
    sorted_trades = sorted(trades, key=lambda t: t["exit_date"])
    net_rs = [t[net_key] for t in sorted_trades]

    cum_r = np.cumsum(net_rs)
    running_max = np.maximum.accumulate(cum_r)
    # Peak can start at 0
    running_peak = np.maximum(0.0, running_max)
    drawdowns = running_peak - cum_r
    max_dd = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0
    total_r = float(cum_r[-1]) if len(cum_r) > 0 else 0.0

    calmar = (total_r / max_dd) if max_dd > 0 else 0.0

    return {
        "total_net_r": round(total_r, 4),
        "max_drawdown_r": round(max_dd, 4),
        "calmar_ratio": round(calmar, 4),
        "n_trades": len(trades),
    }


def run_portfolio_analysis() -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for rule in RULES:
        csv_p = OUTPUT_DIR / f"{rule}_trades.csv"
        trades = []
        with open(csv_p, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                trades.append({
                    "exit_date": dt.date.fromisoformat(row["exit_date"]),
                    "net_r": float(row["net_r"]),
                    "net_r_fin0": float(row["net_r_fin0"]),
                })
        metrics = compute_portfolio_drawdown(trades, "net_r")
        metrics_fin0 = compute_portfolio_drawdown(trades, "net_r_fin0")
        metrics["fin0"] = metrics_fin0
        summary[rule] = metrics
        logger.info(
            "%s Equal-Risk Portfolio [Fin 2%%]: Total Net R=%.2f, Max DD=%.2f R, Calmar=%.2f",
            rule, metrics["total_net_r"], metrics["max_drawdown_r"], metrics["calmar_ratio"],
        )
        logger.info(
            "%s Equal-Risk Portfolio [Fin 0%%]: Total Net R=%.2f, Max DD=%.2f R, Calmar=%.2f",
            rule, metrics_fin0["total_net_r"], metrics_fin0["max_drawdown_r"], metrics_fin0["calmar_ratio"],
        )

    out_json = OUTPUT_DIR / "portfolio_drawdown_summary.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    return summary


if __name__ == "__main__":
    run_portfolio_analysis()
