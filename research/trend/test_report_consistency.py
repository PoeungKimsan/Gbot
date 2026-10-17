"""Test report consistency: asserts every number in FINAL_REPORT.md matches underlying results."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from research.trend.costs import PREREG_COMMIT, PREREG_HASH

REPORT_PATH = Path("research/trend/FINAL_REPORT.md")
OUTPUT_DIR = Path("research/trend/output")
DATA_DIR = Path("research/trend/data")


@pytest.fixture
def report_text() -> str:
    assert REPORT_PATH.is_file(), f"Missing {REPORT_PATH}"
    return REPORT_PATH.read_text(encoding="utf-8")


@pytest.fixture
def stats_data() -> dict:
    target = OUTPUT_DIR / "statistics_summary.json"
    if not target.is_file():
        pytest.skip(f"Pruned artifact: {target}")
    with open(target, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def nulls_data() -> dict:
    target = OUTPUT_DIR / "null_distributions_summary.json"
    if not target.is_file():
        pytest.skip(f"Pruned artifact: {target}")
    with open(target, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def port_data() -> dict:
    target = OUTPUT_DIR / "portfolio_drawdown_summary.json"
    if not target.is_file():
        pytest.skip(f"Pruned artifact: {target}")
    with open(target, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def inst_audit_data() -> list:
    target = DATA_DIR / "instrument_identity_audit.json"
    if not target.is_file():
        pytest.skip(f"Pruned artifact: {target}")
    with open(target, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def eval_2026_data() -> dict:
    target = OUTPUT_DIR / "sealed_2026_evaluation.json"
    if not target.is_file():
        pytest.skip(f"Pruned artifact: {target}")
    with open(target, encoding="utf-8") as f:
        return json.load(f)


def test_prereg_integrity(report_text: str) -> None:
    assert PREREG_HASH in report_text, f"Prereg hash {PREREG_HASH} missing from report"
    assert PREREG_COMMIT in report_text, f"Prereg commit {PREREG_COMMIT} missing from report"
    assert "GROSS-OF-FINANCING SURVIVOR" in report_text
    assert "edge before carry; viability depends on financing terms" in report_text
    assert "INCONCLUSIVE: NO EDGE DEMONSTRATED" in report_text
    assert "Two financing levels (0.0% and 2.0%) were examined" in report_text


def test_pooled_numbers_consistency(report_text: str, stats_data: dict) -> None:
    for rule in ["T1_DONCHIAN", "T2_SMA_CROSS", "T3_TSMOM"]:
        p = stats_data[rule]["pooled"]
        p0 = stats_data[rule]["pooled_fin0"]
        b = stats_data[rule]["bootstrap"]
        b0 = stats_data[rule]["bootstrap_fin0"]

        # Assert trade count N
        assert f"| {p['n']}" in report_text or f"({p['n']})" in report_text, f"Trade count N={p['n']} missing for {rule}"
        # Assert Mean Net R at 2%
        mean_net_r_str = f"{p['mean_net_r']:+.4f}R"
        assert mean_net_r_str in report_text, f"Mean Net R (2%) {mean_net_r_str} missing for {rule}"
        # Assert Mean Net R at 0%
        mean_net_r_fin0_str = f"{p0['mean_net_r']:+.4f}R"
        assert mean_net_r_fin0_str in report_text, f"Mean Net R (0%) {mean_net_r_fin0_str} missing for {rule}"
        # Assert Median Net R
        median_net_r_str = f"{p['median_net_r']:+.4f}R"
        assert median_net_r_str in report_text, f"Median Net R {median_net_r_str} missing for {rule}"
        # Assert Profit Factor
        pf_str = f"{p['profit_factor']:.2f}"
        assert pf_str in report_text, f"Profit Factor {pf_str} missing for {rule}"
        # Assert Raw & Holm p-values
        raw_p_str = f"{b['p_value_one_sided']:.4f}"
        holm_p_str = f"{b['holm_adj_p_value']:.4f}"
        assert raw_p_str in report_text, f"Raw p-value {raw_p_str} missing for {rule}"
        assert holm_p_str in report_text, f"Holm p-value {holm_p_str} missing for {rule}"
        # Assert 95% CI
        ci_str = f"[{b['ci_95_low']:.4f}, {b['ci_95_high']:.4f}]"
        assert ci_str in report_text, f"CI {ci_str} missing for {rule}"
        ci0_str = f"[{b0['ci_95_low']:.4f}, {b0['ci_95_high']:.4f}]"
        assert ci0_str in report_text, f"CI0 {ci0_str} missing for {rule}"


def test_null_numbers_consistency(report_text: str, nulls_data: dict) -> None:
    for rule in ["T1_DONCHIAN", "T2_SMA_CROSS", "T3_TSMOM"]:
        nl = nulls_data[rule]
        na_mean_str = f"{nl['null_a']['mean']:+.4f}R"
        na_p95_str = f"{nl['null_a']['p95']:+.4f}R"
        nb_mean_str = f"{nl['null_b']['mean']:+.4f}R"
        nb_p95_str = f"{nl['null_b']['p95']:+.4f}R"

        assert na_mean_str in report_text, f"Null A mean {na_mean_str} missing for {rule}"
        assert na_p95_str in report_text, f"Null A P95 {na_p95_str} missing for {rule}"
        assert nb_mean_str in report_text, f"Null B mean {nb_mean_str} missing for {rule}"
        assert nb_p95_str in report_text, f"Null B P95 {nb_p95_str} missing for {rule}"


def test_component_drag_consistency(report_text: str, stats_data: dict) -> None:
    for rule in ["T1_DONCHIAN", "T2_SMA_CROSS", "T3_TSMOM"]:
        d = stats_data[rule]["drag_breakdown_pooled"]
        days_str = f"{d['avg_holding_days']:.2f} days"
        sp_str = f"-{d['avg_spread_drag_r']:.4f}R"
        slip_str = f"-{d['avg_slippage_drag_r']:.4f}R"
        fin_str = f"-{d['avg_financing_drag_r']:.4f}R"
        tot_str = f"-{d['avg_total_drag_r']:.4f}R"

        assert days_str in report_text, f"Days {days_str} missing for {rule}"
        assert sp_str in report_text, f"Spread drag {sp_str} missing for {rule}"
        assert slip_str in report_text, f"Slippage drag {slip_str} missing for {rule}"
        assert fin_str in report_text, f"Financing drag {fin_str} missing for {rule}"
        assert tot_str in report_text, f"Total drag {tot_str} missing for {rule}"


def test_subperiod_and_exmetals_consistency(report_text: str, stats_data: dict) -> None:
    for rule in ["T1_DONCHIAN", "T2_SMA_CROSS", "T3_TSMOM"]:
        st = stats_data[rule]
        s0_str = f"{st['by_sub_period']['earliest_2009']['mean_net_r']:+.4f}R"
        s1_str = f"{st['by_sub_period']['2010_2017']['mean_net_r']:+.4f}R"
        s2_str = f"{st['by_sub_period']['2018_2025']['mean_net_r']:+.4f}R"
        ex_str = f"{st['ex_metals']['mean_net_r']:+.4f}R"

        assert s0_str in report_text, f"Sub-period earliest-2009 {s0_str} missing for {rule}"
        assert s1_str in report_text, f"Sub-period 1 {s1_str} missing for {rule}"
        assert s2_str in report_text, f"Sub-period 2 {s2_str} missing for {rule}"
        assert ex_str in report_text, f"Ex-metals {ex_str} missing for {rule}"


def test_portfolio_drawdown_consistency(report_text: str, port_data: dict) -> None:
    for rule in ["T1_DONCHIAN", "T2_SMA_CROSS", "T3_TSMOM"]:
        pd = port_data[rule]
        tot_str = f"{pd['total_net_r']:+.2f}R"
        dd_str = f"{pd['max_drawdown_r']:.2f}R"
        calmar_str = f"{pd['calmar_ratio']:.2f}"

        assert tot_str in report_text, f"Portfolio total net R {tot_str} missing for {rule}"
        assert dd_str in report_text, f"Portfolio max drawdown {dd_str} missing for {rule}"
        assert calmar_str in report_text, f"Portfolio Calmar {calmar_str} missing for {rule}"


def test_sealed_2026_consistency(report_text: str, eval_2026_data: dict) -> None:
    assert eval_2026_data["unsealed"] is True
    for rule in ["T2_SMA_CROSS", "T3_TSMOM"]:
        assert rule in eval_2026_data["rules_evaluated"]
        rev = eval_2026_data["rules_evaluated"][rule]
        fr = rev["fresh_2026_entries"]
        cr = rev["carried_positions_exiting_2026"]
        fr_str = f"{fr['mean_net_r']:+.4f}R"
        cr_str = f"{cr['mean_net_r']:+.4f}R"
        assert fr_str in report_text, f"2026 fresh return {fr_str} missing for {rule}"
        assert cr_str in report_text, f"2026 carried return {cr_str} missing for {rule}"


def test_instrument_audit_consistency(report_text: str, inst_audit_data: list) -> None:
    assert len(inst_audit_data) == 12
    for inst in inst_audit_data:
        sym = inst["symbol"]
        assert f"**{sym}**" in report_text
        assert f"{inst['bars_count']:,}" in report_text
        assert f"{inst['first_close']:.4f}" in report_text
        assert f"{inst['last_close']:.4f}" in report_text
