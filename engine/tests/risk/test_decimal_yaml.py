"""Tests for the Decimal-only config path: ``config/risk.yaml`` and its loader.

The rule under test is AGENTS.md 2.1 expressed at the *input* boundary: a risk
limit is a decision about money, so it must never round-trip through a binary
float. YAML hands back a ``float`` for an unquoted ``0.01``, which means the
loader has to refuse such a file rather than coerce it and hope.
"""

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from engine.risk.config import (
    DEFAULT_RISK_CONFIG_PATH,
    RiskConfig,
    RiskConfigError,
    load_risk_config,
)
from engine.risk.decimal_yaml import (
    DecimalYamlError,
    decimal_scalar,
    load_decimal_yaml,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


# --------------------------------------------------------------------------- #
# the committed config
# --------------------------------------------------------------------------- #
def test_committed_risk_config_loads() -> None:
    config = load_risk_config(REPO_ROOT / DEFAULT_RISK_CONFIG_PATH)

    assert isinstance(config, RiskConfig)
    assert config.risk_per_trade == Decimal("0.01")
    assert config.daily_loss_limit_r == Decimal("3")


def test_default_path_points_at_the_committed_config() -> None:
    assert (REPO_ROOT / DEFAULT_RISK_CONFIG_PATH).is_file()


def test_risk_amount_is_equity_times_fraction() -> None:
    config = RiskConfig(risk_per_trade=Decimal("0.01"), daily_loss_limit_r=Decimal("3"))

    assert config.risk_amount(Decimal("100000")) == Decimal("1000")
    assert config.risk_amount(Decimal("0")) == Decimal("0")


def test_risk_amount_rejects_a_negative_equity() -> None:
    config = RiskConfig(risk_per_trade=Decimal("0.01"), daily_loss_limit_r=Decimal("3"))

    with pytest.raises(RiskConfigError):
        config.risk_amount(Decimal("-1"))


# --------------------------------------------------------------------------- #
# decimal_yaml
# --------------------------------------------------------------------------- #
def test_load_decimal_yaml_returns_a_mapping(tmp_path: Path) -> None:
    path = tmp_path / "risk.yaml"
    path.write_text("risk_per_trade: \"0.01\"\nmax_positions: 3\nflag: true\n", "utf-8")

    loaded = load_decimal_yaml(path)

    assert loaded["risk_per_trade"] == "0.01"
    assert loaded["max_positions"] == 3
    assert loaded["flag"] is True


def test_load_decimal_yaml_refuses_a_float_valued_scalar(tmp_path: Path) -> None:
    """An unquoted 0.01 parses as a float in YAML. That file must not load."""
    path = tmp_path / "risk.yaml"
    path.write_text("risk_per_trade: 0.01\n", "utf-8")

    with pytest.raises(DecimalYamlError) as excinfo:
        load_decimal_yaml(path)

    assert "risk_per_trade" in str(excinfo.value)


def test_load_decimal_yaml_refuses_a_float_in_a_nested_mapping(tmp_path: Path) -> None:
    path = tmp_path / "risk.yaml"
    path.write_text("limits:\n  daily: 2.5\n", "utf-8")

    with pytest.raises(DecimalYamlError):
        load_decimal_yaml(path)


def test_load_decimal_yaml_reports_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(DecimalYamlError):
        load_decimal_yaml(tmp_path / "absent.yaml")


def test_load_decimal_yaml_rejects_invalid_yaml(tmp_path: Path) -> None:
    path = tmp_path / "risk.yaml"
    path.write_text("risk_per_trade: \"0.01\"\n  bad indent: [\n", "utf-8")

    with pytest.raises(DecimalYamlError):
        load_decimal_yaml(path)


def test_load_decimal_yaml_rejects_a_non_mapping_document(tmp_path: Path) -> None:
    path = tmp_path / "risk.yaml"
    path.write_text("- just\n- a\n- list\n", "utf-8")

    with pytest.raises(DecimalYamlError):
        load_decimal_yaml(path)


def test_decimal_scalar_accepts_a_quoted_string() -> None:
    assert decimal_scalar({"risk_per_trade": "0.01"}, "risk_per_trade") == Decimal("0.01")


def test_decimal_scalar_accepts_an_integer() -> None:
    assert decimal_scalar({"daily_loss_limit_r": 3}, "daily_loss_limit_r") == Decimal("3")


def test_decimal_scalar_refuses_a_float() -> None:
    with pytest.raises(DecimalYamlError):
        decimal_scalar({"risk_per_trade": 0.01}, "risk_per_trade")


def test_decimal_scalar_refuses_a_bool() -> None:
    with pytest.raises(DecimalYamlError):
        decimal_scalar({"risk_per_trade": True}, "risk_per_trade")


def test_decimal_scalar_refuses_a_missing_key() -> None:
    with pytest.raises(DecimalYamlError):
        decimal_scalar({"other": "1"}, "risk_per_trade")


def test_decimal_scalar_refuses_unparseable_text() -> None:
    with pytest.raises(DecimalYamlError):
        decimal_scalar({"risk_per_trade": "one percent"}, "risk_per_trade")


def test_decimal_scalar_refuses_a_non_finite_value() -> None:
    with pytest.raises(DecimalYamlError):
        decimal_scalar({"risk_per_trade": "NaN"}, "risk_per_trade")


# --------------------------------------------------------------------------- #
# RiskConfig validation
# --------------------------------------------------------------------------- #
def test_risk_config_refuses_a_float_risk_fraction() -> None:
    with pytest.raises(RiskConfigError):
        RiskConfig(risk_per_trade=0.01, daily_loss_limit_r=Decimal("3"))  # type: ignore[arg-type]


def test_risk_config_refuses_a_risk_fraction_above_one() -> None:
    with pytest.raises(RiskConfigError):
        RiskConfig(risk_per_trade=Decimal("1.5"), daily_loss_limit_r=Decimal("3"))


def test_risk_config_refuses_a_zero_risk_fraction() -> None:
    with pytest.raises(RiskConfigError):
        RiskConfig(risk_per_trade=Decimal("0"), daily_loss_limit_r=Decimal("3"))


def test_risk_config_refuses_a_zero_daily_limit() -> None:
    with pytest.raises(RiskConfigError):
        RiskConfig(risk_per_trade=Decimal("0.01"), daily_loss_limit_r=Decimal("0"))


def test_risk_config_refuses_a_non_finite_daily_limit() -> None:
    with pytest.raises(RiskConfigError):
        RiskConfig(risk_per_trade=Decimal("0.01"), daily_loss_limit_r=Decimal("Infinity"))


def test_loading_a_config_missing_a_key_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "risk.yaml"
    path.write_text("risk_per_trade: \"0.01\"\n", "utf-8")

    with pytest.raises(DecimalYamlError):
        load_risk_config(path)


def test_loading_a_config_with_a_float_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "risk.yaml"
    path.write_text("risk_per_trade: 0.01\ndaily_loss_limit_r: \"3\"\n", "utf-8")

    with pytest.raises(DecimalYamlError):
        load_risk_config(path)


def test_a_parsed_datetime_is_untouched_by_the_decimal_scan(tmp_path: Path) -> None:
    """YAML still has to be usable for the non-numeric parts of a risk document."""
    path = tmp_path / "risk.yaml"
    path.write_text(
        'risk_per_trade: "0.01"\ndaily_loss_limit_r: "3"\n'
        "review_date: 2026-01-15\nnote: \"risk is per trade\"\n",
        "utf-8",
    )

    loaded = load_decimal_yaml(path)

    assert loaded["review_date"] == dt.date(2026, 1, 15)
    assert loaded["note"] == "risk is per trade"
