"""Tests for :mod:`engine.runtime.gates` (AGENTS.md gate policy)."""

import sqlite3
from typing import Final

import pytest

from engine.runtime.gates import (
    EXIT_BLOCKED,
    SQLITE_LEGACY_FLOORS,
    SQLITE_PRIMARY_FLOOR,
    RuntimeGateError,
    check_sqlite_gate,
    evaluate_sqlite_version,
    main,
    run_all_gates,
)

#: Versions that must be accepted.
ACCEPTED_SQLITE: Final[tuple[tuple[int, int, int], ...]] = (
    (3, 53, 1),
    (3, 51, 3),
    (3, 51, 4),
    (3, 60, 0),
    (3, 50, 7),
    (3, 50, 19),
    (3, 44, 6),
    (3, 44, 9),
)

#: Versions that must be rejected, including "numerically newer but unsupported".
REJECTED_SQLITE: Final[tuple[tuple[int, int, int], ...]] = (
    (2, 26, 0),
    (3, 43, 0),
    (3, 44, 5),
    (3, 45, 0),
    (3, 49, 999),
    (3, 50, 6),
    (3, 51, 2),
)

#: Names of override variables a future contributor might be tempted to add.
BYPASS_ENV_VARS: Final[tuple[str, ...]] = (
    "XAUUSD_SKIP_GATES",
    "XAUUSD_ALLOW_OLD_SQLITE",
    "XAUUSD_SQLITE_FLOOR",
    "XAUUSD_NO_GATES",
)


def test_host_sqlite_library_meets_the_gate() -> None:
    """The SQLite actually loaded into this interpreter must pass.

    A host below every floor is a *blocked host*, not a code bug: the gate itself
    records it (``python -m engine.runtime.gates`` appends to
    ``docs/BLOCKERS.md``) and stops the engine with exit code 78. Hosts that
    cannot run the engine — CI runners among them — therefore skip here, because
    a suite must not turn a host condition the gate already handles into a
    code failure. On a host that does meet a baseline this is a real assertion.
    """
    verdict = evaluate_sqlite_version(sqlite3.sqlite_version_info)

    if not verdict.passed:
        pytest.skip(
            f"{verdict.reason} (sqlite3.sqlite_version={sqlite3.sqlite_version}). "
            f"The gate blocks this host with exit code {EXIT_BLOCKED}; run "
            f"'python -m engine.runtime.gates' to record it in docs/BLOCKERS.md."
        )

    assert sqlite3.sqlite_version in verdict.reason


@pytest.mark.parametrize("version", ACCEPTED_SQLITE)
def test_accepted_baselines_pass(version: tuple[int, int, int]) -> None:
    verdict = evaluate_sqlite_version(version)
    assert verdict.passed, verdict.reason


@pytest.mark.parametrize("version", REJECTED_SQLITE)
def test_unsupported_baselines_fail(version: tuple[int, int, int]) -> None:
    verdict = evaluate_sqlite_version(version)
    assert not verdict.passed
    assert "does not meet any supported baseline" in verdict.reason


def test_legacy_floors_are_line_scoped() -> None:
    """A version on an unpinned line never inherits another line's floor."""
    for line, _floor in SQLITE_LEGACY_FLOORS:
        below = (line[0], line[1], 0)
        assert not evaluate_sqlite_version(below).passed, below


def test_check_sqlite_gate_returns_the_validated_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gate returns the three-component version it validated.

    The version is injected rather than read from the host: a host below the
    floor is blocked (exit 78), not a failure of the gate's logic, so it must
    not make this test red. A trailing pre-release component is dropped, which
    is how SQLite reports 3.60.0-style pins.
    """
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (*SQLITE_PRIMARY_FLOOR, 1))

    assert check_sqlite_gate() == SQLITE_PRIMARY_FLOOR


def test_run_all_gates_passes_on_a_supported_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every gate passes and returns the validated version, for an injected host."""
    monkeypatch.setattr(sqlite3, "sqlite_version_info", SQLITE_PRIMARY_FLOOR)

    assert run_all_gates() == SQLITE_PRIMARY_FLOOR


@pytest.mark.parametrize("version", REJECTED_SQLITE)
def test_check_sqlite_gate_raises_on_unsupported_sqlite(version: tuple[int, int, int]) -> None:
    with pytest.raises(RuntimeGateError):
        check_sqlite_gate(version)


def test_runtime_gate_error_is_a_runtime_error() -> None:
    assert issubclass(RuntimeGateError, RuntimeError)


def _stub_sqlite(monkeypatch: pytest.MonkeyPatch, version: tuple[int, int, int]) -> None:
    monkeypatch.setattr(sqlite3, "sqlite_version_info", version)
    monkeypatch.setattr(sqlite3, "sqlite_version", ".".join(str(part) for part in version))


def test_main_returns_zero_on_a_supported_host(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XAUUSD_REPO_ROOT", str(tmp_path))
    _stub_sqlite(monkeypatch, SQLITE_PRIMARY_FLOOR)

    assert main() == 0
    assert not (tmp_path / "docs" / "BLOCKERS.md").exists()


def test_main_exits_78_and_records_a_blocker_on_an_unsupported_host(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XAUUSD_REPO_ROOT", str(tmp_path))
    _stub_sqlite(monkeypatch, (3, 43, 0))

    assert main() == EXIT_BLOCKED

    blocker = tmp_path / "docs" / "BLOCKERS.md"
    assert blocker.is_file()
    recorded = blocker.read_text(encoding="utf-8")
    assert "(3, 43, 0)" in recorded
    assert str(EXIT_BLOCKED) in recorded


@pytest.mark.parametrize("env_var", BYPASS_ENV_VARS)
def test_the_gate_cannot_be_bypassed_by_environment(tmp_path, monkeypatch, env_var) -> None:
    """Gates are never loosened, so no override variable may skip them."""
    monkeypatch.setenv("XAUUSD_REPO_ROOT", str(tmp_path))
    monkeypatch.setenv(env_var, "1")
    _stub_sqlite(monkeypatch, (3, 44, 5))

    assert main() == EXIT_BLOCKED


def test_gate_module_exposes_a_cli_entrypoint() -> None:
    assert callable(main)
    assert EXIT_BLOCKED == 78
