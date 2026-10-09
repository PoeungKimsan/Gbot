"""Startup gates that must pass before any engine subsystem runs.

A gate is a cheap, deterministic host check whose failure aborts the process
*before* it touches market data, opens a socket or writes a journal row.  Gates
are append-only: once shipped, a gate is never removed and never loosened.

The first gate protects journal integrity.  The journal relies on WAL behaviour
plus a row-level hash chain, both of which are only trustworthy on SQLite
versions carrying every relevant upstream fix; anything older is rejected even
though the module would import cleanly.
"""

import platform
import sqlite3
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, NamedTuple

from engine.runtime import repository_root

__all__ = [
    "EXIT_BLOCKED",
    "SQLITE_LEGACY_FLOORS",
    "SQLITE_PRIMARY_FLOOR",
    "GateVerdict",
    "RuntimeGateError",
    "check_sqlite_gate",
    "evaluate_sqlite_version",
    "main",
    "run_all_gates",
]

#: Process exit code used when a gate blocks startup. ``78`` is ``EX_CONFIG``
#: from ``sysexits.h``, so supervisors distinguish a host-configuration problem
#: from an actual crash.
EXIT_BLOCKED: Final[int] = 78

#: Minimum acceptable SQLite version. Any version at or above this passes.
SQLITE_PRIMARY_FLOOR: Final[tuple[int, int, int]] = (3, 51, 3)

#: Older maintenance lines that are still accepted. Each entry pairs a line
#: ``(major, minor)`` with the oldest release on that line known to be safe.
#: These floors are line-scoped: a hypothetical 3.45.0 does *not* satisfy the
#: 3.50.x floor just because it compares lower.
SQLITE_LEGACY_FLOORS: Final[tuple[tuple[tuple[int, int], tuple[int, int, int]], ...]] = (
    ((3, 50), (3, 50, 7)),
    ((3, 44), (3, 44, 6)),
)


class RuntimeGateError(RuntimeError):
    """A mandatory startup gate failed; startup must abort."""


class GateVerdict(NamedTuple):
    """Outcome of evaluating a single gate."""

    passed: bool
    reason: str


def _dotted(version: tuple[int, int, int]) -> str:
    """Render a version tuple as ``3.51.3``."""
    return ".".join(str(part) for part in version)


def _coerce(version_info: Iterable[int]) -> tuple[int, int, int]:
    """Normalise any 3-component version iterable to ``(major, minor, patch)``."""
    parts = tuple(int(part) for part in version_info)
    if len(parts) < 3:
        msg = f"expected at least 3 version components, got {parts!r}"
        raise ValueError(msg)
    return (parts[0], parts[1], parts[2])


def _supported_baselines() -> str:
    """Human-readable description of every accepted SQLite baseline."""
    entries = [f">= {_dotted(SQLITE_PRIMARY_FLOOR)}"]
    entries.extend(
        f"{_dotted(line)}.x >= {_dotted(floor)}" for line, floor in SQLITE_LEGACY_FLOORS
    )
    return "; ".join(entries)


def evaluate_sqlite_version(version_info: Iterable[int]) -> GateVerdict:
    """Decide whether a SQLite version is an accepted baseline.

    A version passes when it is at or above the primary floor, or when it sits
    on a pinned legacy maintenance line at or above that line's floor.

    Args:
        version_info: ``major, minor, patch`` components. Extra components are
            ignored, matching how SQLite reports pre-release tags.

    """
    version = _coerce(version_info)

    if version >= SQLITE_PRIMARY_FLOOR:
        return GateVerdict(
            True,
            f"sqlite {_dotted(version)} meets the >= {_dotted(SQLITE_PRIMARY_FLOOR)} floor",
        )

    for line, floor in SQLITE_LEGACY_FLOORS:
        if version[:2] == line and version >= floor:
            return GateVerdict(
                True,
                f"sqlite {_dotted(version)} meets the {_dotted(floor)} floor",
            )

    return GateVerdict(
        False,
        f"sqlite {_dotted(version)} does not meet any supported baseline "
        f"({_supported_baselines()})",
    )


def check_sqlite_gate(version_info: Iterable[int] | None = None) -> tuple[int, int, int]:
    """Validate the SQLite library actually loaded into this interpreter.

    ``sqlite3.sqlite_version_info`` describes the library the running process
    linked against, not the OS package manager's notion of "installed", so it is
    the only trustworthy source.

    Args:
        version_info: Override for testing. Defaults to the loaded library.

    Returns:
        The validated ``(major, minor, patch)`` tuple.

    Raises:
        RuntimeGateError: if the loaded SQLite is below every supported floor.

    """
    raw = sqlite3.sqlite_version_info if version_info is None else version_info
    version = _coerce(raw)
    verdict = evaluate_sqlite_version(version)
    if not verdict.passed:
        raise RuntimeGateError(verdict.reason)
    return version


def run_all_gates() -> tuple[int, int, int]:
    """Run every mandatory startup gate.

    Returns:
        The SQLite version tuple that was validated.

    Raises:
        RuntimeGateError: on the first gate that does not pass.

    """
    return check_sqlite_gate()


def _append_blocker(version: tuple[int, int, int], reason: str) -> Path:
    """Append the failing host/runtime fingerprint to ``docs/BLOCKERS.md``."""
    path = repository_root() / "docs" / "BLOCKERS.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    entry = (
        f"## {stamp} - sqlite_version gate\n\n"
        f"- host: {platform.platform()}\n"
        f"- machine: {platform.machine()}\n"
        f"- python: {sys.version.split()[0]}\n"
        f"- sqlite3.sqlite_version: {sqlite3.sqlite_version}\n"
        f"- sqlite3.sqlite_version_info: {version}\n"
        f"- exit_code: {EXIT_BLOCKED}\n"
        f"- reason: {reason}\n\n"
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(entry)
    return path


def main() -> int:
    """Run every gate; record a blocker and exit ``EXIT_BLOCKED`` on failure."""
    version = _coerce(sqlite3.sqlite_version_info)
    try:
        run_all_gates()
    except RuntimeGateError as exc:
        recorded = _append_blocker(version, str(exc))
        print(f"[gate] BLOCKED: {exc}", file=sys.stderr)
        print(f"[gate] recorded in {recorded}", file=sys.stderr)
        return EXIT_BLOCKED
    print(f"[gate] ok: sqlite {_dotted(version)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
