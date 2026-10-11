"""Null model simulations (Null A and Null B) with fixed seed = 42."""

from __future__ import annotations

import csv
import datetime as dt
import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
from research.trend.costs import compute_costs
from research.trend.indicators import load_daily_bars

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("trend.nulls")

DATA_DIR = Path("research/trend/data")
OUTPUT_DIR = Path("research/trend/output")
RULES = ["T1_DONCHIAN", "T2_SMA_CROSS", "T3_TSMOM"]
NULL_ROUNDS = 2_000
NULL_SEED = 42
END_DATE = dt.date(2025, 12, 31)


def load_symbol_market_data(data_dir: Path = DATA_DIR) -> dict[str, dict[str, Any]]:
    symbols = [
        "XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "USDJPY",
        "AUDUSD", "USDCAD", "USDCHF", "US500", "GER40", "Brent", "NDX",
    ]
    market_data: dict[str, dict[str, Any]] = {}
    for sym in symbols:
        bars = load_daily_bars(data_dir / f"{sym}_daily.csv", hard_stop_date=END_DATE)
        dates = [b.date for b in bars]
        date_to_idx = {d: i for i, d in enumerate(dates)}
        market_data[sym] = {
            "bars": bars,
            "dates": dates,
            "date_to_idx": date_to_idx,
            "opens": np.array([b.open for b in bars], dtype=np.float64),
            "highs": np.array([b.high for b in bars], dtype=np.float64),
            "lows": np.array([b.low for b in bars], dtype=np.float64),
            "closes": np.array([b.close for b in bars], dtype=np.float64),
            "atrs": np.array([b.atr20 for b in bars], dtype=np.float64),
            "n": len(bars),
        }
    return market_data


def run_null_simulations(
    data_dir: Path = DATA_DIR,
    output_dir: Path = OUTPUT_DIR,
    trades_dir: Path | None = None,
    n_rounds: int = NULL_ROUNDS,
    seed: int = NULL_SEED,
) -> dict[str, Any]:
    market_data = load_symbol_market_data(data_dir=data_dir)
    null_results: dict[str, Any] = {}
    src_dir = trades_dir if trades_dir is not None else output_dir

    for rule in RULES:
        csv_p = src_dir / f"{rule}_trades.csv"
        trades: list[dict[str, Any]] = []
        with open(csv_p, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                trades.append({
                    "symbol": row["symbol"],
                    "direction": row["direction"],
                    "entry_date": dt.date.fromisoformat(row["entry_date"]),
                    "exit_date": dt.date.fromisoformat(row["exit_date"]),
                    "stop_multiple": 2.0 if rule == "T1_DONCHIAN" else 3.0,
                    "real_net_r": float(row["net_r"]),
                    "real_net_r_fin0": float(row["net_r_fin0"]),
                })

        real_mean_net_r = float(np.mean([t["real_net_r"] for t in trades]))
        real_mean_net_r_fin0 = float(np.mean([t["real_net_r_fin0"] for t in trades]))
        n_trades = len(trades)
        logger.info("Running Null A and Null B for %s (%d trades, %d rounds)...", rule, n_trades, n_rounds)

        # Pre-resolve bar indices and duration in bars
        trade_specs: list[dict[str, Any]] = []
        for t in trades:
            sym = t["symbol"]
            m_dict = market_data[sym]
            d_to_i = m_dict["date_to_idx"]
            e_idx = d_to_i.get(t["entry_date"])
            x_idx = d_to_i.get(t["exit_date"])
            if e_idx is None or x_idx is None:
                # Fallback to closest
                continue
            h_bars = max(x_idx - e_idx, 1)
            trade_specs.append({
                "symbol": sym,
                "direction": t["direction"],
                "entry_idx": e_idx,
                "h_bars": h_bars,
                "stop_multiple": t["stop_multiple"],
            })

        n_specs = len(trade_specs)
        rng = np.random.default_rng(seed)

        # -------------------------------------------------------------
        # NULL A: Drift-matched random entry
        # -------------------------------------------------------------
        null_a_means_fin2 = np.empty(n_rounds, dtype=np.float64)
        null_a_means_fin0 = np.empty(n_rounds, dtype=np.float64)

        for b in range(n_rounds):
            round_net_rs_fin2 = []
            round_net_rs_fin0 = []
            for spec in trade_specs:
                sym = spec["symbol"]
                m = market_data[sym]
                n_bars = m["n"]
                h_bars = spec["h_bars"]
                dir_ = spec["direction"]
                s_mult = spec["stop_multiple"]

                # Pick random entry index within 2010-2025 window where h_bars remain
                min_idx = 252
                max_idx = max(n_bars - h_bars - 1, min_idx + 1)
                k = rng.integers(min_idx, max_idx)

                entry_p = m["opens"][k]
                atr = m["atrs"][k - 1]
                stop_dist = s_mult * atr
                if stop_dist <= 0:
                    round_net_rs_fin2.append(0.0)
                    round_net_rs_fin0.append(0.0)
                    continue

                stop_p = (entry_p - stop_dist) if dir_ == "LONG" else (entry_p + stop_dist)
                entry_date = m["dates"][k]

                is_stopped = False
                exit_p = 0.0
                exit_date = entry_date

                for j in range(k, k + h_bars):
                    if dir_ == "LONG":
                        if m["lows"][j] <= stop_p:
                            is_stopped = True
                            exit_p = m["opens"][j] if m["opens"][j] <= stop_p else stop_p
                            exit_date = m["dates"][j]
                            break
                    else:
                        if m["highs"][j] >= stop_p:
                            is_stopped = True
                            exit_p = m["opens"][j] if m["opens"][j] >= stop_p else stop_p
                            exit_date = m["dates"][j]
                            break

                if not is_stopped:
                    exit_idx = min(k + h_bars, n_bars - 1)
                    exit_p = m["opens"][exit_idx]
                    exit_date = m["dates"][exit_idx]

                cost = compute_costs(
                    symbol=sym,
                    direction=dir_,
                    entry_price=entry_p,
                    exit_price=exit_p,
                    stop_distance=stop_dist,
                    is_stop=is_stopped,
                    entry_date=entry_date,
                    exit_date=exit_date,
                )
                round_net_rs_fin2.append(cost["net_r"])
                round_net_rs_fin0.append(cost["net_r_fin0"])

            null_a_means_fin2[b] = np.mean(round_net_rs_fin2)
            null_a_means_fin0[b] = np.mean(round_net_rs_fin0)

        # -------------------------------------------------------------
        # NULL B: Direction shuffle (random direction, real entry dates)
        # -------------------------------------------------------------
        null_b_means_fin2 = np.empty(n_rounds, dtype=np.float64)
        null_b_means_fin0 = np.empty(n_rounds, dtype=np.float64)

        for b in range(n_rounds):
            round_net_rs_fin2 = []
            round_net_rs_fin0 = []
            for spec in trade_specs:
                sym = spec["symbol"]
                m = market_data[sym]
                n_bars = m["n"]
                h_bars = spec["h_bars"]
                k = spec["entry_idx"]
                s_mult = spec["stop_multiple"]

                # Random direction (50% Long, 50% Short)
                dir_ = "LONG" if rng.random() < 0.5 else "SHORT"

                entry_p = m["opens"][k]
                atr = m["atrs"][k - 1]
                stop_dist = s_mult * atr
                if stop_dist <= 0:
                    round_net_rs_fin2.append(0.0)
                    round_net_rs_fin0.append(0.0)
                    continue

                stop_p = (entry_p - stop_dist) if dir_ == "LONG" else (entry_p + stop_dist)
                entry_date = m["dates"][k]

                is_stopped = False
                exit_p = 0.0
                exit_date = entry_date

                for j in range(k, min(k + h_bars, n_bars)):
                    if dir_ == "LONG":
                        if m["lows"][j] <= stop_p:
                            is_stopped = True
                            exit_p = m["opens"][j] if m["opens"][j] <= stop_p else stop_p
                            exit_date = m["dates"][j]
                            break
                    else:
                        if m["highs"][j] >= stop_p:
                            is_stopped = True
                            exit_p = m["opens"][j] if m["opens"][j] >= stop_p else stop_p
                            exit_date = m["dates"][j]
                            break

                if not is_stopped:
                    exit_idx = min(k + h_bars, n_bars - 1)
                    exit_p = m["opens"][exit_idx]
                    exit_date = m["dates"][exit_idx]

                cost = compute_costs(
                    symbol=sym,
                    direction=dir_,
                    entry_price=entry_p,
                    exit_price=exit_p,
                    stop_distance=stop_dist,
                    is_stop=is_stopped,
                    entry_date=entry_date,
                    exit_date=exit_date,
                )
                round_net_rs_fin2.append(cost["net_r"])
                round_net_rs_fin0.append(cost["net_r_fin0"])

            null_b_means_fin2[b] = np.mean(round_net_rs_fin2)
            null_b_means_fin0[b] = np.mean(round_net_rs_fin0)

        # Metrics for Null A & B (2% financing)
        p95_null_a_fin2 = float(np.percentile(null_a_means_fin2, 95.0))
        p95_null_b_fin2 = float(np.percentile(null_b_means_fin2, 95.0))
        mean_null_a_fin2 = float(np.mean(null_a_means_fin2))
        mean_null_b_fin2 = float(np.mean(null_b_means_fin2))

        beats_null_a_fin2 = real_mean_net_r >= p95_null_a_fin2
        beats_null_b_fin2 = real_mean_net_r >= p95_null_b_fin2

        # Metrics for Null A & B (0% financing)
        p95_null_a_fin0 = float(np.percentile(null_a_means_fin0, 95.0))
        p95_null_b_fin0 = float(np.percentile(null_b_means_fin0, 95.0))
        mean_null_a_fin0 = float(np.mean(null_a_means_fin0))
        mean_null_b_fin0 = float(np.mean(null_b_means_fin0))

        beats_null_a_fin0 = real_mean_net_r_fin0 >= p95_null_a_fin0
        beats_null_b_fin0 = real_mean_net_r_fin0 >= p95_null_b_fin0

        null_results[rule] = {
            "real_mean_net_r": round(real_mean_net_r, 4),
            "real_mean_net_r_fin0": round(real_mean_net_r_fin0, 4),
            "null_a": {
                "mean": round(mean_null_a_fin2, 4),
                "p95": round(p95_null_a_fin2, 4),
                "beats_p95": beats_null_a_fin2,
            },
            "null_b": {
                "mean": round(mean_null_b_fin2, 4),
                "p95": round(p95_null_b_fin2, 4),
                "beats_p95": beats_null_b_fin2,
            },
            "passes_both_nulls": beats_null_a_fin2 and beats_null_b_fin2,
            "fin0": {
                "real_mean_net_r": round(real_mean_net_r_fin0, 4),
                "null_a": {
                    "mean": round(mean_null_a_fin0, 4),
                    "p95": round(p95_null_a_fin0, 4),
                    "beats_p95": beats_null_a_fin0,
                },
                "null_b": {
                    "mean": round(mean_null_b_fin0, 4),
                    "p95": round(p95_null_b_fin0, 4),
                    "beats_p95": beats_null_b_fin0,
                },
                "passes_both_nulls": beats_null_a_fin0 and beats_null_b_fin0,
            },
        }
        logger.info(
            "%s [Fin 2%%]: Real=%.4f | Null A: mean=%.4f, p95=%.4f (beats=%s) | Null B: mean=%.4f, p95=%.4f (beats=%s)",
            rule, real_mean_net_r, mean_null_a_fin2, p95_null_a_fin2, beats_null_a_fin2, mean_null_b_fin2, p95_null_b_fin2, beats_null_b_fin2,
        )
        logger.info(
            "%s [Fin 0%%]: Real=%.4f | Null A: mean=%.4f, p95=%.4f (beats=%s) | Null B: mean=%.4f, p95=%.4f (beats=%s)",
            rule, real_mean_net_r_fin0, mean_null_a_fin0, p95_null_a_fin0, beats_null_a_fin0, mean_null_b_fin0, p95_null_b_fin0, beats_null_b_fin0,
        )

    out_json = output_dir / "null_distributions_summary.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(null_results, f, indent=2)
    logger.info("Saved null distributions summary to %s", out_json)

    return null_results


if __name__ == "__main__":
    run_null_simulations()
