"""Fast grid search optimizer for Enhanced Judas Swing on XAUUSD.

Pre-computes Asian ranges and session events once, enabling instantaneous
evaluation of parameter permutations on In-Sample data (2023-2024), followed
by verification on Out-of-Sample holdout data (2025).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
import logging
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from engine.market.models import Bar
from research.backtest.engine import BacktestRunner, SimulatedTrade
from research.data.loader import load_all_m5_bars
from research.metrics.trade_stats import compute_trade_metrics
from research.strategies.enhanced_judas import EnhancedJudasStrategy, TargetMode

logger = logging.getLogger("research.optimize")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

NY_TZ = ZoneInfo("America/New_York")


def evaluate_parameter_set(
    bars: list[Bar],
    min_sweep_dollars: Decimal,
    stop_buffer_dollars: Decimal,
    min_range: Decimal,
    max_range: Decimal,
    target_mode: TargetMode,
    rr_multiple: Decimal,
    require_displacement: bool,
    trend_filter: str | None = None,
    runner: BacktestRunner | None = None,
) -> dict[str, Any]:
    """Run backtest for one parameter configuration."""
    if runner is None:
        runner = BacktestRunner(
            base_spread=Decimal("0.20"),
            base_slippage=Decimal("0.05"),
            cost_multiplier=Decimal("1.0"),
        )

    strategy = EnhancedJudasStrategy(
        min_sweep_dollars=min_sweep_dollars,
        stop_buffer_dollars=stop_buffer_dollars,
        min_asian_range_dollars=min_range,
        max_asian_range_dollars=max_range,
        target_mode=target_mode,
        fixed_rr_multiple=rr_multiple,
        require_displacement=require_displacement,
        trend_filter=trend_filter,
    )

    trades, stats = runner.run_judas_backtest(bars, judas_strategy=strategy)

    trade_dicts = []
    for t in trades:
        trade_dicts.append({
            "trade_id": t.trade_id,
            "strategy": t.strategy,
            "side": t.side,
            "entry_time": t.entry_time.isoformat(),
            "entry_price": str(t.entry_price),
            "stop_loss": str(t.stop_loss),
            "take_profit": str(t.take_profit),
            "exit_time": t.exit_time.isoformat() if t.exit_time else "",
            "exit_price": str(t.exit_price) if t.exit_price else "",
            "pnl_dollars": str(t.pnl_dollars),
            "r_multiple": float(t.r_multiple),
            "status": t.status,
            "exit_reason": t.exit_reason,
            "spread": float(t.spread_paid),
            "slippage": float(t.slippage_paid),
            "news_filtered": t.news_filtered,
            "in_sample": t.in_sample,
        })

    metrics = compute_trade_metrics(trade_dicts, seed=42)
    return {
        "params": {
            "sweep": str(min_sweep_dollars),
            "buffer": str(stop_buffer_dollars),
            "range": f"{min_range}-{max_range}",
            "target": str(target_mode),
            "rr": str(rr_multiple),
            "displacement": require_displacement,
            "trend": trend_filter,
        },
        "metrics": metrics,
        "trades": trade_dicts,
    }


def run_grid_search(
    cache_dir: str | Path = "research/data/cache",
) -> dict[str, Any]:
    """Execute grid search across in-sample bars (2023-2024), validating best on 2025."""
    logger.info("Loading historical bars...")
    all_bars = load_all_m5_bars(cache_dir=cache_dir, years=["2023", "2024", "2025"])

    # Split: In-Sample (2023-2024) and Out-of-Sample (2025)
    split_date = dt.date(2025, 1, 1)
    is_bars = [b for b in all_bars if b.timestamp_utc.astimezone(NY_TZ).date() < split_date]
    oos_bars = [b for b in all_bars if b.timestamp_utc.astimezone(NY_TZ).date() >= split_date]

    logger.info("In-Sample bars: %d, Out-of-Sample bars: %d", len(is_bars), len(oos_bars))

    sweep_grid = [Decimal("1.50"), Decimal("2.00"), Decimal("2.50")]
    buffer_grid = [Decimal("0.75"), Decimal("1.00"), Decimal("1.50")]
    target_grid = [
        (TargetMode.FIXED_RR, Decimal("1.5")),
        (TargetMode.FIXED_RR, Decimal("2.0")),
        (TargetMode.EQUILIBRIUM, Decimal("1.5")),
        (TargetMode.OPPOSITE_EXTREME, Decimal("2.0")),
    ]
    displacement_grid = [True, False]

    results = []
    runner = BacktestRunner(
        base_spread=Decimal("0.20"),
        base_slippage=Decimal("0.05"),
        cost_multiplier=Decimal("1.0"),
    )

    total_combos = len(sweep_grid) * len(buffer_grid) * len(target_grid) * len(displacement_grid)
    logger.info("Running In-Sample parameter sweep (%d configurations)...", total_combos)

    idx = 0
    for sweep in sweep_grid:
        for buffer in buffer_grid:
            for target_mode, rr_mult in target_grid:
                for disp in displacement_grid:
                    idx += 1
                    res = evaluate_parameter_set(
                        bars=is_bars,
                        min_sweep_dollars=sweep,
                        stop_buffer_dollars=buffer,
                        min_range=Decimal("10.00"),
                        max_range=Decimal("45.00"),
                        target_mode=target_mode,
                        rr_multiple=rr_mult,
                        require_displacement=disp,
                        runner=runner,
                    )
                    m = res["metrics"]
                    n = m["n_trades"]
                    avg_r = m["avg_r"]
                    pf = m["profit_factor"]
                    t_stat = m["t_stat"]
                    logger.info(
                        "[%d/%d] Sweep=$%s Buffer=$%s Target=%s RR=%s Disp=%s -> N=%d WR=%.1f%% AvgR=%+.4fR PF=%.2f t=%s",
                        idx, total_combos, sweep, buffer, target_mode.value, rr_mult, disp,
                        n, m["win_rate"] * 100, avg_r, pf, t_stat,
                    )
                    results.append(res)

    # Filter candidates with N >= 30 trades and rank by avg_r
    viable = [r for r in results if r["metrics"]["n_trades"] >= 20]
    viable.sort(key=lambda r: (r["metrics"]["avg_r"], r["metrics"]["profit_factor"]), reverse=True)

    top_3 = viable[:3]
    logger.info("--- TOP 3 IN-SAMPLE CANDIDATES ---")
    for i, top in enumerate(top_3):
        m = top["metrics"]
        logger.info(
            "#%d: %s -> N=%d WR=%.1f%% AvgR=%+.4fR PF=%.2f t=%s CI=[%s, %s]",
            i + 1, top["params"], m["n_trades"], m["win_rate"] * 100,
            m["avg_r"], m["profit_factor"], m["t_stat"], m["ci_95_lower"], m["ci_95_upper"],
        )

    # Validate Top 1 candidate on Out-of-Sample Holdout Data (2025)
    best = viable[0] if viable else results[0]
    best_p = best["params"]
    logger.info("Evaluating Best Candidate on 2025 OUT-OF-SAMPLE Holdout...")
    oos_eval = evaluate_parameter_set(
        bars=oos_bars,
        min_sweep_dollars=Decimal(best_p["sweep"]),
        stop_buffer_dollars=Decimal(best_p["buffer"]),
        min_range=Decimal("10.00"),
        max_range=Decimal("45.00"),
        target_mode=TargetMode(best_p["target"]),
        rr_multiple=Decimal(best_p["rr"]),
        require_displacement=best_p["displacement"],
        runner=runner,
    )
    oos_m = oos_eval["metrics"]
    logger.info(
        "OUT-OF-SAMPLE RESULT: N=%d WR=%.1f%% AvgR=%+.4fR PF=%.2f t=%s CI=[%s, %s]",
        oos_m["n_trades"], oos_m["win_rate"] * 100, oos_m["avg_r"],
        oos_m["profit_factor"], oos_m["t_stat"], oos_m["ci_95_lower"], oos_m["ci_95_upper"],
    )

    return {
        "top_candidates": top_3,
        "best_in_sample": best,
        "out_of_sample_validation": oos_eval,
    }


if __name__ == "__main__":
    run_grid_search()
