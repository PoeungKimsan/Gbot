"""Evaluation engine for the 14 pre-registered XAUUSD strategy variants.

Evaluates each pre-registered variant on the fresh, unseen 2020-2022 validation dataset.
Tests against strict acceptance criteria:
1. N >= 200 completed trades.
2. Post-1x-cost expectancy > 0.0000R.
3. 95% Bootstrap CI lower bound > 0.0000R.
4. Beats Null Model A and Null Model B at >= 95th percentile (p <= 0.05).
5. Monte Carlo 95th Max Drawdown < 30%.
Else marks variant as REJECTED / INCONCLUSIVE.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
import json
import logging
from pathlib import Path
from typing import Any

from engine.market.models import Bar
from research.backtest.engine import BacktestRunner, SimulatedTrade
from research.baselines.null_models import run_null_models
from research.data.loader import load_all_m5_bars
from research.metrics.trade_stats import compute_trade_metrics
from research.simulation.monte_carlo import run_monte_carlo_risk
from research.strategies.enhanced_judas import (
    BufferMode,
    EnhancedJudasStrategy,
    SweepMode,
    TargetMode,
)

logger = logging.getLogger("research.eval")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def _trade_to_dict(t: SimulatedTrade) -> dict[str, Any]:
    return {
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
    }


def define_preregistered_variants() -> list[dict[str, Any]]:
    """Define the exact list of 14 pre-registered variants."""
    return [
        {
            "id": "V0",
            "name": "Baseline Live Logic",
            "desc": "5 ticks sweep, 10 ticks buffer, opposite extreme TP",
            "strategy": EnhancedJudasStrategy(
                sweep_mode=SweepMode.TICKS,
                buffer_mode=BufferMode.TICKS,
                target_mode=TargetMode.OPPOSITE_EXTREME,
            ),
            "breakeven": False,
        },
        {
            "id": "V1",
            "name": "ATR-Normalized Sweep",
            "desc": "Sweep = 0.25 * ATR(14), 10 ticks buffer, opposite extreme TP",
            "strategy": EnhancedJudasStrategy(
                sweep_mode=SweepMode.ATR,
                sweep_atr_mult=Decimal("0.25"),
                buffer_mode=BufferMode.TICKS,
                target_mode=TargetMode.OPPOSITE_EXTREME,
            ),
            "breakeven": False,
        },
        {
            "id": "V2",
            "name": "Range-Normalized Sweep",
            "desc": "Sweep = 10% of Asian Range, 10 ticks buffer, opposite extreme TP",
            "strategy": EnhancedJudasStrategy(
                sweep_mode=SweepMode.RANGE_PCT,
                sweep_range_pct=Decimal("0.10"),
                buffer_mode=BufferMode.TICKS,
                target_mode=TargetMode.OPPOSITE_EXTREME,
            ),
            "breakeven": False,
        },
        {
            "id": "V3",
            "name": "ATR-Normalized Buffer",
            "desc": "5 ticks sweep, Buffer = 0.20 * ATR(14), opposite extreme TP",
            "strategy": EnhancedJudasStrategy(
                sweep_mode=SweepMode.TICKS,
                buffer_mode=BufferMode.ATR,
                buffer_atr_mult=Decimal("0.20"),
                target_mode=TargetMode.OPPOSITE_EXTREME,
            ),
            "breakeven": False,
        },
        {
            "id": "V4",
            "name": "Asian Range Width Gate",
            "desc": "Gate: 1.5 * ATR <= Range <= 6.0 * ATR, baseline parameters",
            "strategy": EnhancedJudasStrategy(
                use_range_gate=True,
                min_range_atr=Decimal("1.5"),
                max_range_atr=Decimal("6.0"),
            ),
            "breakeven": False,
        },
        {
            "id": "V5",
            "name": "Equilibrium Target",
            "desc": "TP = 50% Asian Range Midpoint, baseline sweep and buffer",
            "strategy": EnhancedJudasStrategy(
                target_mode=TargetMode.EQUILIBRIUM,
            ),
            "breakeven": False,
        },
        {
            "id": "V6",
            "name": "Fixed 1.5R Target",
            "desc": "TP = 1.5R, baseline sweep and buffer",
            "strategy": EnhancedJudasStrategy(
                target_mode=TargetMode.FIXED_RR,
                fixed_rr_multiple=Decimal("1.5"),
            ),
            "breakeven": False,
        },
        {
            "id": "V7",
            "name": "Fixed 2.0R Target",
            "desc": "TP = 2.0R, baseline sweep and buffer",
            "strategy": EnhancedJudasStrategy(
                target_mode=TargetMode.FIXED_RR,
                fixed_rr_multiple=Decimal("2.0"),
            ),
            "breakeven": False,
        },
        {
            "id": "V8",
            "name": "Displacement Reclaim Gate",
            "desc": "Reclaim bar body >= 40% range, baseline parameters",
            "strategy": EnhancedJudasStrategy(
                require_displacement=True,
                displacement_pct=Decimal("0.40"),
            ),
            "breakeven": False,
        },
        {
            "id": "V9",
            "name": "Breakeven Trigger at +1.0R",
            "desc": "Trail stop to entry once price reaches +1.0R, baseline parameters",
            "strategy": EnhancedJudasStrategy(),
            "breakeven": True,
        },
        {
            "id": "V10",
            "name": "Flipped Contrarian Breakout",
            "desc": "Buy high sweep, Sell low sweep, 2.0R target",
            "strategy": EnhancedJudasStrategy(
                flipped_direction=True,
            ),
            "breakeven": False,
        },
        {
            "id": "V11",
            "name": "Session Open Auction Bias",
            "desc": "Only sell above London open, only buy below London open",
            "strategy": EnhancedJudasStrategy(
                use_session_bias=True,
            ),
            "breakeven": False,
        },
        {
            "id": "V12",
            "name": "Composite Thesis A (ATR + Fixed 1.5R)",
            "desc": "0.25 ATR sweep, 0.20 ATR buffer, Fixed 1.5R TP",
            "strategy": EnhancedJudasStrategy(
                sweep_mode=SweepMode.ATR,
                sweep_atr_mult=Decimal("0.25"),
                buffer_mode=BufferMode.ATR,
                buffer_atr_mult=Decimal("0.20"),
                target_mode=TargetMode.FIXED_RR,
                fixed_rr_multiple=Decimal("1.5"),
            ),
            "breakeven": False,
        },
        {
            "id": "V13",
            "name": "Composite Thesis B (ATR + Equilibrium)",
            "desc": "0.25 ATR sweep, 0.20 ATR buffer, 50% Equilibrium TP",
            "strategy": EnhancedJudasStrategy(
                sweep_mode=SweepMode.ATR,
                sweep_atr_mult=Decimal("0.25"),
                buffer_mode=BufferMode.ATR,
                buffer_atr_mult=Decimal("0.20"),
                target_mode=TargetMode.EQUILIBRIUM,
            ),
            "breakeven": False,
        },
    ]


def run_preregistered_evaluation(
    cache_dir: str | Path = "research/data/cache",
    out_dir: str | Path = "research/output",
    seed: int = 42,
) -> dict[str, Any]:
    """Execute evaluation across the fresh 2020-2022 validation dataset."""
    logger.info("Loading 2020-2022 fresh validation dataset...")
    bars = load_all_m5_bars(cache_dir=cache_dir, years=["2020", "2021", "2022"])
    logger.info("Loaded %d validation bars.", len(bars))

    variants = define_preregistered_variants()
    total_variants_count = len(variants)
    logger.info("TOTAL PRE-REGISTERED VARIANTS TRIED: %d", total_variants_count)
    assert total_variants_count <= 15, "Cannot exceed 15 pre-registered variants!"

    runner = BacktestRunner(
        base_spread=Decimal("0.20"),
        base_slippage=Decimal("0.05"),
        cost_multiplier=Decimal("1.0"),
    )

    eval_results: list[dict[str, Any]] = []

    for idx, var in enumerate(variants, start=1):
        v_id = var["id"]
        v_name = var["name"]
        strat = var["strategy"]
        be_flag = var["breakeven"]

        logger.info(
            "[%d/%d] Evaluating %s: %s (breakeven=%s)...",
            idx,
            total_variants_count,
            v_id,
            v_name,
            be_flag,
        )

        trades, stats = runner.run_judas_backtest(
            bars,
            judas_strategy=strat,
            breakeven_at_1r=be_flag,
        )

        trade_dicts = [_trade_to_dict(t) for t in trades]
        metrics = compute_trade_metrics(trade_dicts, seed=seed)

        # Monte Carlo Risk (10,000 permutations)
        r_list = [float(t["r_multiple"]) for t in trade_dicts]
        mc_risk = run_monte_carlo_risk(r_list, n_simulations=10_000, seed=seed)

        # Check Acceptance Criteria
        n = metrics["n_trades"]
        exp = metrics["expectancy"]
        ci_lower = metrics["ci_95_lower"] or -999.0
        mdd_pct = mc_risk["mdd_pct_95th"]

        c1_n_ok = n >= 200
        c2_exp_ok = exp > 0.0
        c3_ci_ok = ci_lower > 0.0
        c5_mdd_ok = mdd_pct < 30.0

        # Run Null models if N >= 50
        null_a_ok = False
        null_b_ok = False
        null_report = {}
        if n >= 50:
            null_res = run_null_models(
                bars,
                trade_dicts,
                n_iterations=500,
                seed=seed,
                window_hours=(2, 5),
            )
            p_a = null_res["model_a"]["p_value"]
            p_b = null_res["model_b"]["p_value"]
            null_a_ok = p_a <= 0.05
            null_b_ok = p_b <= 0.05
            null_report = {
                "model_a_p_value": p_a,
                "model_b_p_value": p_b,
                "model_a_percentile": null_res["model_a"]["percentile"],
                "model_b_percentile": null_res["model_b"]["percentile"],
            }

        all_criteria_met = c1_n_ok and c2_exp_ok and c3_ci_ok and null_a_ok and null_b_ok and c5_mdd_ok

        verdict = "PASSED - STATISTICAL EDGE CONFIRMED" if all_criteria_met else "REJECTED / INCONCLUSIVE"

        res_entry = {
            "variant_id": v_id,
            "name": v_name,
            "description": var["desc"],
            "n_trades": n,
            "win_rate": round(metrics["win_rate"], 4),
            "expectancy_r": round(exp, 4),
            "ci_95": [metrics["ci_95_lower"], metrics["ci_95_upper"]],
            "t_stat": metrics["t_stat"],
            "p_value": metrics["p_value"],
            "profit_factor": round(metrics["profit_factor"], 2),
            "max_drawdown_r": round(metrics["max_drawdown_r"], 1),
            "max_drawdown_pct": round(metrics["max_drawdown_pct"], 1),
            "mc_95th_mdd_pct": round(mdd_pct, 1),
            "null_models": null_report,
            "criteria_checks": {
                "c1_n_ge_200": c1_n_ok,
                "c2_post_cost_exp_gt_0": c2_exp_ok,
                "c3_ci_lower_gt_0": c3_ci_ok,
                "c4_null_a_p_le_05": null_a_ok,
                "c4_null_b_p_le_05": null_b_ok,
                "c5_mc_mdd_lt_30": c5_mdd_ok,
            },
            "verdict": verdict,
        }

        logger.info(
            "%s Result: N=%d WR=%.1f%% Exp=%+.4fR CI=[%s, %s] PF=%.2f MDD=%.1f%% -> Verdict: %s",
            v_id,
            n,
            metrics["win_rate"] * 100,
            exp,
            metrics["ci_95_lower"],
            metrics["ci_95_upper"],
            metrics["profit_factor"],
            mdd_pct,
            verdict,
        )

        eval_results.append(res_entry)

    # Summary report
    out_dir_path = Path(out_dir)
    out_dir_path.mkdir(parents=True, exist_ok=True)

    json_path = out_dir_path / "preregistered_evaluation_2020_2022.json"
    full_output = {
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "dataset": "Fresh Validation Set (2020-2022, 212,492 M5 bars)",
        "total_variants_tried": total_variants_count,
        "variants": eval_results,
    }
    json_path.write_text(json.dumps(full_output, indent=2), encoding="utf-8")

    # Generate Markdown Summary Table
    md_path = out_dir_path / "preregistered_evaluation_summary.md"
    md_lines = [
        "# Pre-Registered Strategy Variants Evaluation (Fresh Validation Set 2020–2022)",
        "",
        f"**Dataset Evaluated:** 2020-01-01 to 2022-12-31 (212,492 M5 Bars) | **Total Variants Tried:** {total_variants_count} (<= 15)",
        "",
        "### Results Table",
        "| ID | Variant Name | N | Win Rate | Avg R | 95% CI | PF | MC 95% DD | Null A/B p-val | Verdict |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]

    for r in eval_results:
        ci_str = f"[{r['ci_95'][0]}, {r['ci_95'][1]}]" if r["ci_95"][0] is not None else "N/A"
        null_str = (
            f"{r['null_models'].get('model_a_p_value', '-')}/{r['null_models'].get('model_b_p_value', '-')}"
            if r["null_models"]
            else "-"
        )
        md_lines.append(
            f"| **{r['variant_id']}** | {r['name']} | {r['n_trades']} | {r['win_rate']:.1%} | {r['expectancy_r']:+.4f}R | {ci_str} | {r['profit_factor']:.2f} | {r['mc_95th_mdd_pct']:.1f}% | {null_str} | **{r['verdict']}** |"
        )

    md_lines.extend([
        "",
        "---",
        "",
        "### Final Conclusion Against Strict Acceptance Criteria",
    ])

    passed_count = sum(1 for r in eval_results if "PASSED" in r["verdict"])
    if passed_count == 0:
        md_lines.append(
            "> [!WARNING]\n"
            "> **OUTCOME: INCONCLUSIVE — NO STATISTICAL EDGE DEMONSTRATED ON XAUUSD.**\n"
            ">\n"
            "> None of the 14 pre-registered variants satisfied all five acceptance criteria on the independent "
            "2020–2022 validation dataset. Reclaiming the Asian session extreme during London Open without higher-timeframe "
            "confirmation does not produce a statistically verifiable edge after realistic execution friction."
        )
    else:
        md_lines.append(f"> **OUTCOME: {passed_count} variant(s) satisfied all acceptance criteria.**")

    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    logger.info("Evaluation complete. Results written to %s and %s", json_path, md_path)
    return full_output


if __name__ == "__main__":
    run_preregistered_evaluation()
