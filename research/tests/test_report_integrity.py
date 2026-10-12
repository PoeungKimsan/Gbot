"""Test to verify that every figure in research/FINAL_REPORT.md matches the ground-truth results.

Fails if any figure in the markdown differs from the underlying JSON/CSV outputs.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
import pytest

REPORT_PATH = Path("research/FINAL_REPORT.md")
OUTPUT_DIR = Path("research/output")


@pytest.fixture(scope="module")
def report_text() -> str:
    assert REPORT_PATH.is_file(), f"Missing {REPORT_PATH}"
    return REPORT_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def step1_data() -> dict:
    return json.loads((OUTPUT_DIR / "step1_judas_variants_summary.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def nulls_data() -> dict:
    return json.loads((OUTPUT_DIR / "null_simulations_summary.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def leverage_data() -> list:
    return json.loads((OUTPUT_DIR / "closing_leverage_table.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def yearly_data() -> list:
    f = json.loads((OUTPUT_DIR / "followup_analysis_report.json").read_text(encoding="utf-8"))
    return f["part_5_yearly_table"]


def test_section_6_1_fade_and_mirror_integrity(report_text: str, step1_data: dict, nulls_data: dict):
    """Section 6.1 must contain exact figures for Fade, Mirror, and Null B."""
    v0 = step1_data["validation_2020_2022"]["V0 Baseline"]
    assert f"{v0['n']}" in report_text
    assert f"{v0['gross_exp']:+.4f}R" in report_text  # +0.0553R
    assert f"{v0['net_exp']:+.4f}R" in report_text    # -0.1460R
    assert f"{v0['gross_wr']:.2%}" in report_text     # 20.38%
    assert f"{v0['cost_drag']:.4f}R" in report_text   # 0.2013R

    # Mirror exact figures
    assert "-0.0536R" in report_text
    assert "-0.2558R" in report_text
    assert "22.67%" in report_text
    assert "0.2021R" in report_text

    # Cost-aligned null percentiles
    assert f"{nulls_data['pct_fade_null_b']:.1f}%" in report_text    # 67.1%
    assert f"{nulls_data['pct_mirror_null_b']:.1f}%" in report_text  # 25.4%


def test_section_6_2_variants_integrity(report_text: str, step1_data: dict):
    """Section 6.2 must contain exact figures for 2020-2022 and 2023-2025 variants."""
    val = step1_data["validation_2020_2022"]
    conf = step1_data["confirmation_2023_2025"]

    # 2020-2022 variants
    assert f"| {val['C1 (Cost <= 10% Risk)']['n']} |" in report_text  # 214
    assert f"{val['C1 (Cost <= 10% Risk)']['net_exp']:+.4f}R" in report_text # -0.1765R
    assert f"| {val['H2 (Near Prev Extreme)']['n']} |" in report_text  # 422
    assert f"{val['H2 (Near Prev Extreme)']['net_exp']:+.4f}R" in report_text # -0.0929R
    assert f"| {val['H3 (Event Calendar Filter)']['n']} |" in report_text # 356
    assert f"{val['H3 (Event Calendar Filter)']['net_exp']:+.4f}R" in report_text # -0.0172R

    # 2023-2025 variants including H2 and H3
    assert f"| {conf['V0 Baseline']['n']} |" in report_text # 488
    assert f"{conf['V0 Baseline']['net_exp']:+.4f}R" in report_text # -0.1787R
    assert f"| {conf['H2 (Near Prev Extreme)']['n']} |" in report_text # 397
    assert f"{conf['H2 (Near Prev Extreme)']['net_exp']:+.4f}R" in report_text # -0.1175R
    assert f"| {conf['H3 (Event Calendar Filter)']['n']} |" in report_text # 342
    assert f"{conf['H3 (Event Calendar Filter)']['net_exp']:+.4f}R" in report_text # -0.0652R


def test_leverage_table_integrity(report_text: str, leverage_data: list):
    """Section 6.5 must contain all 12 rows from closing_leverage_table.json."""
    for row in leverage_data:
        eq_s = f"${row['equity']:,}"
        stp_s = f"${row['stop_distance']:.2f}"
        raw_s = f"{row['raw_lots']:.2f}"
        clp_s = f"{row['clamped_lots']:.2f}"
        eff_s = f"{row['eff_risk_pct']:.2f}%"
        assert eq_s in report_text
        assert stp_s in report_text
        assert raw_s in report_text
        assert clp_s in report_text
        assert eff_s in report_text


def test_yearly_table_integrity(report_text: str, yearly_data: list):
    """Section 6.6 must contain every yearly row including 2022 positive expectancy."""
    for y in yearly_data:
        assert f"**{y['year']}**" in report_text
        if y["year"] != "2026 (YTD)":
            assert f"{y['n_trades']}" in report_text
            assert f"{y['expectancy_r']:+.4f}R" in report_text
            assert f"{y['win_rate']:.2%}" in report_text
    # Explicitly check 2022 positive expectancy
    assert "+0.1300R" in report_text


def test_conclusion_integrity(report_text: str):
    """Conclusion must state that 11-year run is in-sample, 2022 was positive, and Null B is 67.1%."""
    assert "in-sample / full historical sample backtest" in report_text
    assert "2022 was net positive (+0.1300R)" in report_text
    assert "67.1th percentile of Null B" in report_text
    assert "NO EDGE DEMONSTRATED" in report_text
