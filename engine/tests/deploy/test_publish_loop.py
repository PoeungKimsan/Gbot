"""Machine coverage for scripts/publish_loop.py.

The publisher unit needs a process to exec, and Phase 5 shipped a library rather than a
daemon. This loop is the bridge, so the properties worth pinning are the ones that keep it
from being a crash-loop:

* a journal that does not exist yet is *waited on*, not an error — the publisher starts
  before the engine has written a row on a cold host;
* a projection path must be absolute, because a relative one resolves against whatever
  working directory systemd started the unit in;
* a stop signal arriving **during a fold** still stops the process. That is the reason
  ``_Stop`` derives from ``BaseException``: an ``Exception`` subclass would be caught by the
  loop's generic handler, logged as a failed publish, and the loop would carry on to the
  next sleep — turning an operator's ``systemctl stop`` into a warning line.

Every test that runs ``main()`` delivers a real ``SIGTERM`` from a timer thread, so the
signal path under test is the real one and no test can hang.
"""

from __future__ import annotations

import datetime as dt
import os
import signal
import threading
from pathlib import Path

import pytest
from scripts.publish_loop import (
    DEFAULT_INTERVAL,
    DEFAULT_JOURNAL,
    DEFAULT_PROJECTION,
    main,
    resolve_paths,
)

GENESIS = dt.datetime(2026, 3, 10, 7, 0, tzinfo=dt.UTC)

_POSIX_ONLY = pytest.mark.skipif(
    os.name == "nt",
    reason="Windows has no catchable SIGTERM: os.kill terminates the process outright",
)

#: How long to let the loop run before signalling it to stop. Far longer than one fold, far
#: shorter than the default five-second sleep, so each test finishes inside a second.
_STOP_AFTER = 0.25


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in ("XAUUSD_JOURNAL_PATH", "XAUUSD_PROJECTION_PATH", "XAUUSD_PUBLISH_INTERVAL"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.fixture
def stop_signal():
    """Deliver one SIGTERM to this process after a short delay.

    A timer thread rather than a raised handler, because the property under test *is* the
    signal: that it interrupts a ``time.sleep``, that ``_Stop`` escapes the generic
    ``except Exception``, and that the loop exits zero.
    """
    original = signal.getsignal(signal.SIGTERM)

    def start() -> None:
        timer = threading.Timer(_STOP_AFTER, os.kill, args=(os.getpid(), signal.SIGTERM))
        timer.daemon = True
        timer.start()
        return timer

    yield start

    signal.signal(signal.SIGTERM, original)


def seed_journal(path: Path, rows: int = 1) -> None:
    from engine.journal.schema import connect_writer

    connection = connect_writer(str(path))
    try:
        for index in range(rows):
            connection.execute(
                "INSERT INTO journal (event_id, event_type, occurred_at, payload,"
                " previous_hash, event_hash) VALUES (?, ?, ?, ?, ?, ?)",
                (f"event-{index}", "equity_snapshot", GENESIS.isoformat(), b"{}", b"", b""),
            )
        connection.commit()
    finally:
        connection.close()


# --------------------------------------------------------------------------- #
# path resolution
# --------------------------------------------------------------------------- #


def test_the_defaults_are_the_deployment_layout(env: pytest.MonkeyPatch) -> None:
    journal, projection, interval = resolve_paths()

    assert journal == Path("/var/lib/xauusd/journal/journal.db")
    assert projection == Path("/var/lib/xauusd/projection")
    assert interval == DEFAULT_INTERVAL


def test_paths_come_from_the_environment(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    journal = tmp_path / "j.db"
    projection = tmp_path / "proj"
    env.setenv("XAUUSD_JOURNAL_PATH", str(journal))
    env.setenv("XAUUSD_PROJECTION_PATH", str(projection))
    env.setenv("XAUUSD_PUBLISH_INTERVAL", "2.5")

    resolved = resolve_paths()

    assert resolved == (journal, projection, 2.5)


def test_a_relative_projection_path_is_refused(env: pytest.MonkeyPatch) -> None:
    env.setenv("XAUUSD_PROJECTION_PATH", "projection")

    with pytest.raises(ValueError, match="must be absolute"):
        resolve_paths()


def test_a_non_numeric_interval_is_refused(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env.setenv("XAUUSD_PROJECTION_PATH", str(tmp_path / "p"))
    env.setenv("XAUUSD_PUBLISH_INTERVAL", "often")

    with pytest.raises(ValueError, match="not a number"):
        resolve_paths()


def test_a_non_positive_interval_is_refused(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env.setenv("XAUUSD_PROJECTION_PATH", str(tmp_path / "p"))
    env.setenv("XAUUSD_PUBLISH_INTERVAL", "0")

    with pytest.raises(ValueError, match="must be positive"):
        resolve_paths()


# --------------------------------------------------------------------------- #
# the loop, driven by real signals
# --------------------------------------------------------------------------- #


@_POSIX_ONLY
def test_a_missing_journal_is_not_a_failure(
    env: pytest.MonkeyPatch,
    tmp_path: Path,
    stop_signal,
    capsys: pytest.CaptureFixture,
) -> None:
    """A cold host has no journal yet; the loop waits rather than crash-looping."""
    env.setenv("XAUUSD_JOURNAL_PATH", str(tmp_path / "absent.db"))
    env.setenv("XAUUSD_PROJECTION_PATH", str(tmp_path / "proj"))
    env.setenv("XAUUSD_LOG_LEVEL", "INFO")
    stop_signal()

    code = main()

    assert code == 0
    captured = capsys.readouterr()
    assert "does not exist yet" in captured.out + captured.err


@_POSIX_ONLY
def test_a_real_journal_is_folded(
    env: pytest.MonkeyPatch,
    tmp_path: Path,
    stop_signal,
    capsys: pytest.CaptureFixture,
) -> None:
    journal = tmp_path / "journal.db"
    seed_journal(journal)
    projection = tmp_path / "proj"
    env.setenv("XAUUSD_JOURNAL_PATH", str(journal))
    env.setenv("XAUUSD_PROJECTION_PATH", str(projection))
    env.setenv("XAUUSD_LOG_LEVEL", "CRITICAL")
    stop_signal()

    code = main()

    assert code == 0
    assert (projection / "state.json").is_file()
    assert (projection / "trades.json").is_file()
    assert (projection / "metrics.json").is_file()
    del capsys


@_POSIX_ONLY
def test_a_stop_arriving_during_the_sleep_still_exits_zero(
    env: pytest.MonkeyPatch,
    tmp_path: Path,
    stop_signal,
) -> None:
    """The signal must interrupt the sleep, which is where it almost always lands."""
    env.setenv("XAUUSD_JOURNAL_PATH", str(tmp_path / "absent.db"))
    env.setenv("XAUUSD_PROJECTION_PATH", str(tmp_path / "proj"))
    env.setenv("XAUUSD_LOG_LEVEL", "CRITICAL")
    stop_signal()

    assert main() == 0


@_POSIX_ONLY
def test_the_installed_handler_is_removed_again(
    env: pytest.MonkeyPatch,
    tmp_path: Path,
    stop_signal,
) -> None:
    """A loop that leaves its handler installed poisons the process it ran in."""
    env.setenv("XAUUSD_JOURNAL_PATH", str(tmp_path / "absent.db"))
    env.setenv("XAUUSD_PROJECTION_PATH", str(tmp_path / "proj"))
    env.setenv("XAUUSD_LOG_LEVEL", "CRITICAL")
    sentinel = lambda *_: None  # noqa: E731 - a recognisable stand-in handler
    signal.signal(signal.SIGTERM, sentinel)
    stop_signal()

    try:
        main()
        restored = signal.getsignal(signal.SIGTERM)
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)

    assert restored is not sentinel


def test_a_misconfigured_environment_exits_one(env: pytest.MonkeyPatch) -> None:
    env.setenv("XAUUSD_PROJECTION_PATH", "relative/path")
    env.setenv("XAUUSD_LOG_LEVEL", "CRITICAL")

    assert main() == 1


@_POSIX_ONLY
def test_a_second_signal_stops_for_real(
    env: pytest.MonkeyPatch,
    tmp_path: Path,
    stop_signal,
) -> None:
    """`systemctl stop` waits, then sends KILL; this is the path between the two."""
    env.setenv("XAUUSD_JOURNAL_PATH", str(tmp_path / "absent.db"))
    env.setenv("XAUUSD_PROJECTION_PATH", str(tmp_path / "proj"))
    env.setenv("XAUUSD_LOG_LEVEL", "CRITICAL")

    stop_signal()
    # A second, earlier signal while the loop is still running.
    threading.Timer(_STOP_AFTER / 2, os.kill, args=(os.getpid(), signal.SIGTERM)).start()

    assert main() == 0


def test_the_constants_match_the_units() -> None:
    """The state directories the units declare must be the paths this loop defaults to."""
    assert Path("/var/lib/xauusd/journal/journal.db") == DEFAULT_JOURNAL
    assert Path("/var/lib/xauusd/projection") == DEFAULT_PROJECTION
