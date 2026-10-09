"""Tests for the strategy document and the version that names it.

Two rules are under test. The first is AGENTS.md 2.1 at the config boundary: an
unquoted ``0.25`` in ``strategy.yaml`` is a binary float, and the loader refuses the
file rather than coercing it. The second is traceability: the engine stamps the
SHA-256 of the parsed document into the journal header, so two runs are only
comparable when they ran the same policy. A digest that changed when the file was
merely reformatted, or that did not change when a threshold moved, would both be
worse than useless.
"""

import datetime as dt
import hashlib
from decimal import Decimal
from pathlib import Path

import pytest

from engine.domain.events import DomainEvent, EventType, canonical_json_bytes
from engine.risk.decimal_yaml import load_decimal_yaml
from engine.strategy.config import (
    DEFAULT_STRATEGY_CONFIG_PATH,
    StrategyConfig,
    StrategyConfigError,
    StrategyVersion,
    load_strategy_config,
    run_header_event,
    strategy_version_of,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
COMMITTED = REPO_ROOT / DEFAULT_STRATEGY_CONFIG_PATH

VALID = """
swing_n: 2
sweep_min_ticks: 3
sweep_close_back_bars: 2
atr_window: 14
mss_body_k: "0.25"
fvg_min_atr: "0.25"
order_expiry_bars: 12
stop_buffer_ticks: 5
rr_target: "2"
"""

AT_NOON = dt.datetime(2026, 3, 9, 12, 0, tzinfo=dt.UTC)


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, "utf-8")
    return path


def _mapping(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "swing_n": 2,
        "sweep_min_ticks": 3,
        "sweep_close_back_bars": 2,
        "atr_window": 14,
        "mss_body_k": "0.25",
        "fvg_min_atr": "0.25",
        "order_expiry_bars": 12,
        "stop_buffer_ticks": 5,
        "rr_target": "2",
    }
    document.update(overrides)
    return document


# --------------------------------------------------------------------------- #
# the committed document
# --------------------------------------------------------------------------- #
def test_the_committed_strategy_config_loads() -> None:
    config = load_strategy_config(COMMITTED)

    assert isinstance(config, StrategyConfig)
    assert config.swing_n == 2
    assert config.sweep_min_ticks == 3
    assert config.sweep_close_back_bars == 2
    assert config.atr_window == 14
    assert config.mss_body_k == Decimal("0.25")
    assert config.fvg_min_atr == Decimal("0.25")
    assert config.order_expiry_bars == 12
    assert config.stop_buffer_ticks == 5
    assert config.rr_target == Decimal("2")


def test_the_default_path_is_the_committed_document() -> None:
    assert COMMITTED.is_file()


def test_a_config_is_frozen() -> None:
    config = load_strategy_config(COMMITTED)

    with pytest.raises(Exception):  # noqa: B017 - a frozen dataclass refuses
        config.swing_n = 5  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# float refusal (AGENTS.md 2.1)
# --------------------------------------------------------------------------- #
def test_an_unquoted_float_is_refused_at_load_time(tmp_path: Path) -> None:
    path = _write(tmp_path, "float.yaml", VALID.replace('mss_body_k: "0.25"', "mss_body_k: 0.25"))

    with pytest.raises(ValueError) as excinfo:
        load_strategy_config(path)

    assert "mss_body_k" in str(excinfo.value)
    assert "float" in str(excinfo.value)


def test_a_digit_in_a_comment_is_not_a_value(tmp_path: Path) -> None:
    path = _write(tmp_path, "commented.yaml", "# this threshold used to be 0.5\n" + VALID)

    assert load_strategy_config(path).mss_body_k == Decimal("0.25")


def test_an_absent_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        load_strategy_config(tmp_path / "absent.yaml")


def test_an_unparseable_file_is_refused(tmp_path: Path) -> None:
    path = _write(tmp_path, "broken.yaml", VALID + "\n  broken: [\n")

    with pytest.raises(ValueError):
        load_strategy_config(path)


def test_a_missing_key_is_refused(tmp_path: Path) -> None:
    path = _write(tmp_path, "missing.yaml", VALID.replace("stop_buffer_ticks: 5\n", ""))

    with pytest.raises(StrategyConfigError):
        load_strategy_config(path)


# --------------------------------------------------------------------------- #
# policy validation
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "field",
    ["swing_n", "sweep_min_ticks", "sweep_close_back_bars", "atr_window", "order_expiry_bars"],
)
def test_an_integer_field_refuses_zero(field: str) -> None:
    with pytest.raises(StrategyConfigError):
        StrategyConfig.from_mapping(_mapping(**{field: 0}))


@pytest.mark.parametrize("field", ["mss_body_k", "fvg_min_atr", "rr_target"])
def test_a_decimal_field_refuses_zero(field: str) -> None:
    with pytest.raises(StrategyConfigError):
        StrategyConfig.from_mapping(_mapping(**{field: "0"}))


def test_a_stop_buffer_of_zero_is_allowed() -> None:
    """A stop pinned exactly at the swept extreme is a choice, not a defect."""
    assert StrategyConfig.from_mapping(_mapping(stop_buffer_ticks=0)).stop_buffer_ticks == 0


def test_a_negative_stop_buffer_is_refused() -> None:
    with pytest.raises(StrategyConfigError):
        StrategyConfig.from_mapping(_mapping(stop_buffer_ticks=-1))


def test_a_negative_decimal_threshold_is_refused() -> None:
    with pytest.raises(StrategyConfigError):
        StrategyConfig.from_mapping(_mapping(rr_target="-2"))


def test_a_string_integer_field_is_refused() -> None:
    with pytest.raises(StrategyConfigError):
        StrategyConfig.from_mapping(_mapping(swing_n="2"))


def test_a_float_reaching_from_mapping_is_refused() -> None:
    with pytest.raises(StrategyConfigError):
        StrategyConfig.from_mapping(_mapping(sweep_min_ticks=3.0))  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# the strategy version
# --------------------------------------------------------------------------- #
def test_the_version_is_the_sha256_of_the_parsed_document() -> None:
    document = load_decimal_yaml(COMMITTED)
    version = strategy_version_of(document, config_path="config/strategy.yaml")

    assert version.digest == hashlib.sha256(canonical_json_bytes(document)).digest()
    assert isinstance(version, StrategyVersion)


def test_the_digest_is_stable_across_reformatting(tmp_path: Path) -> None:
    """Key order and blank lines are not policy, so they must not move the version."""
    forward = _write(tmp_path, "forward.yaml", VALID)
    reordered = _write(
        tmp_path, "reordered.yaml", "\n".join(reversed(VALID.strip().splitlines())) + "\n"
    )

    assert strategy_version_of(load_decimal_yaml(forward), config_path="a").digest == (
        strategy_version_of(load_decimal_yaml(reordered), config_path="a").digest
    )


def test_changing_a_value_changes_the_version(tmp_path: Path) -> None:
    before = _write(tmp_path, "before.yaml", VALID)
    after = _write(tmp_path, "after.yaml", VALID.replace('rr_target: "2"', 'rr_target: "3"'))

    assert strategy_version_of(load_decimal_yaml(before), config_path="a").digest != (
        strategy_version_of(load_decimal_yaml(after), config_path="a").digest
    )


def test_the_hex_digest_is_64_hex_characters() -> None:
    version = strategy_version_of(
        load_decimal_yaml(COMMITTED), config_path="config/strategy.yaml"
    )

    assert len(version.hex_digest) == 64
    assert version.algorithm == "sha256"
    assert version.config_path == "config/strategy.yaml"
    assert str(version) == version.hex_digest


def test_the_header_payload_names_the_version() -> None:
    version = strategy_version_of(load_decimal_yaml(COMMITTED), config_path="config/strategy.yaml")

    payload = version.header_payload()

    assert payload["strategy_version"] == version.hex_digest
    assert payload["strategy_version_algorithm"] == "sha256"
    assert payload["strategy_config_path"] == "config/strategy.yaml"


def test_the_version_records_a_digest_of_bytes_only() -> None:
    with pytest.raises(Exception):  # noqa: B017 - a text digest is refused
        StrategyVersion(digest="hex text", config_path="x")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# the journal header event
# --------------------------------------------------------------------------- #
def test_the_header_event_is_a_run_started_carrying_the_version() -> None:
    version = strategy_version_of(
        load_decimal_yaml(COMMITTED), config_path="config/strategy.yaml"
    )

    event = run_header_event(version, run_id="run-1", at=AT_NOON)

    assert isinstance(event, DomainEvent)
    assert event.event_type is EventType.RUN_STARTED
    assert event.occurred_at_utc == AT_NOON
    assert event.payload["strategy_version"] == version.hex_digest
    assert event.payload["run_id"] == "run-1"


def test_the_header_event_is_canonical_and_float_free() -> None:
    """A header is journalled, so it must survive canonicalization byte for byte."""
    version = strategy_version_of(
        load_decimal_yaml(COMMITTED), config_path="config/strategy.yaml"
    )

    event = run_header_event(version, run_id="run-1", at=AT_NOON)

    assert event.canonical_bytes() == run_header_event(
        version, run_id="run-1", at=AT_NOON
    ).canonical_bytes()
    for value in event.payload.values():
        assert isinstance(value, str)
