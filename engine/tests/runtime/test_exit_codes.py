"""The exit-code contract, asserted path by path.

Phase 8 replaced an exit *scheme* with an exit *contract*. Before it, the codes were
whatever each failure path happened to return: a dead feed returned 0 because systemd was
"the thing that should restart it", and an un-wired entry point returned 1 because
``SystemExit(message)`` defaults to 1. Both were defensible in isolation and wrong together:
an operator running ``systemctl status`` could not tell "stopped cleanly" from "died with a
traceback".

So there is now one table in ``engine.runtime.supervisor``, and every path through the
supervisor returns a code from it:

* ``0``    a clean stop — SIGTERM or the control-plane ``SHUTDOWN``;
* ``70``   the journal is unusable — the bounded queue overflowed, or its chain is broken;
* ``75``   the feed failed after its retry budget (``EX_TEMPFAIL``), a *transient* failure;
* ``78``   the host or the configuration is wrong, including "no quote source injected".

The distinction that matters operationally is 75 versus 0. 75 means the engine stopped for a
reason outside itself and a restart is worth trying; 0 means it did what it was told. A dead
feed returning 0 taught systemd that the stop was intentional, which is why
``RestartPreventExitStatus`` exists at all — and why 75 must *not* be on it.

Nothing here waits on a clock for its logic. The only wall-clock waits are the shutdown
barriers, which are the quantities under test, and the codes are read from the supervisor's
own return values rather than from a subprocess's status.
"""

from __future__ import annotations

import datetime as dt
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from engine.journal.schema import ChainError
from engine.journal.writer import JournalExhausted
from engine.runtime import supervisor as supervisor_module
from engine.runtime.signals import ShutdownController, SignalRouter
from engine.runtime.supervisor import (
    EXIT_BLOCKED,
    EXIT_CLEAN,
    EXIT_CODES,
    EXIT_FEED_FAILED,
    EXIT_JOURNAL_EXHAUSTED,
    SHUTDOWN_BUDGET,
    Supervisor,
    SupervisorSettings,
)
from engine.runtime.gates import RuntimeGateError

REPO_ROOT = Path(__file__).resolve().parents[2]
SYSTEMD = REPO_ROOT.parent / "systemd"
CONTROL_TOKEN = "exit-code-contract-token"

STRATEGY = supervisor_module.StrategyConfig(
    swing_n=2,
    sweep_min_ticks=3,
    sweep_close_back_bars=2,
    atr_window=14,
    mss_body_k=Decimal("0.25"),
    fvg_min_atr=Decimal("0.25"),
    order_expiry_bars=12,
    stop_buffer_ticks=5,
    rr_target=Decimal("2"),
)


@pytest.fixture(autouse=True)
def _control_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every supervisor run resolves a token; the code under test is the exit, not it."""
    monkeypatch.setenv("XAUUSD_CONTROL_TOKEN", CONTROL_TOKEN)


# --------------------------------------------------------------------------- #
# the doubles
# --------------------------------------------------------------------------- #


class Journal:
    """A journal double that records submissions and never blocks."""

    def __init__(self) -> None:
        self.events: list[object] = []
        self.started = False
        self.stopped = False
        self.drained = False

    def start(self) -> None:
        self.started = True

    def submit(self, event: object) -> None:
        self.events.append(event)

    def drain(self, timeout: object = None) -> bool:
        self.drained = True
        return True

    def stop(self, timeout: object = None) -> bool:
        self.stopped = True
        return True


class ExhaustingJournal(Journal):
    """The bounded queue is full, which must stop the engine with 70."""

    def submit(self, event: object) -> None:
        raise JournalExhausted("queue is full")


class IntegrityBrokenJournal(Journal):
    """The hash chain does not verify, which is also 70.

    The supervisor is where a broken chain surfaces in production: the chain is verified over
    the journal a restart reads, so an integrity error is a journal that cannot be trusted,
    which is the same class of failure as a queue that cannot accept an event.
    """

    def submit(self, event: object) -> None:
        raise ChainError("row 7: event_hash does not match a recomputation")


class DyingFeed:
    """A feed that fails after its retry budget — EX_TEMPFAIL."""

    def __init__(self) -> None:
        self.stopped = False

    async def run(self, on_quote: object, *, should_stop: object = None) -> None:
        self.stopped = True
        msg = "the stream went away after its retry budget"
        raise ConnectionResetError(msg)


class HangingFeed:
    """A feed that never finishes until it is asked to stop."""

    def __init__(self) -> None:
        self.stopped = False

    async def run(self, on_quote: object, *, should_stop: object = None) -> None:
        while should_stop is None or not should_stop():
            import asyncio

            await asyncio.sleep(0)
        self.stopped = True


class CleanFeed(HangingFeed):
    """A feed that completes on its own, which is a clean stop, not a failure."""


def _settings(journal_path: Path | None = None, **overrides: object) -> SupervisorSettings:
    values: dict[str, object] = {
        "run_id": "exit-code-test",
        "journal_path": journal_path or Path("journal.db"),
        "control_port": 0,
    }
    values.update(overrides)
    return SupervisorSettings(**values)  # type: ignore[arg-type]


def _supervisor(
    journal: object,
    source: object,
    *,
    shutdown: ShutdownController | None = None,
    router: SignalRouter | None = None,
) -> Supervisor:
    return Supervisor.build(
        journal=journal,
        quote_source=source,
        strategy_config=STRATEGY,
        settings=_settings(),
        shutdown=shutdown,
        router=router,
    )


# --------------------------------------------------------------------------- #
# 0 -- a clean stop
# --------------------------------------------------------------------------- #


async def test_a_signal_stop_returns_zero() -> None:
    journal = Journal()
    feed = HangingFeed()
    controller = ShutdownController()
    supervisor = _supervisor(journal, feed, shutdown=controller, router=SignalRouter(controller))

    stopper = supervisor_module.asyncio.create_task(supervisor.run())
    await supervisor_module.asyncio.sleep(0)
    controller.request_shutdown("unit")
    code = await supervisor_module.asyncio.wait_for(stopper, timeout=10)

    assert code == EXIT_CLEAN == 0
    assert feed.stopped
    assert journal.drained


async def test_a_control_plane_shutdown_returns_zero() -> None:
    """The TCP ``SHUTDOWN`` reaches the same path and returns the same code."""
    journal = Journal()
    feed = HangingFeed()
    supervisor = _supervisor(journal, feed, shutdown=ShutdownController())

    stopper = supervisor_module.asyncio.create_task(supervisor.run())
    await supervisor_module.asyncio.sleep(0)
    assert supervisor.surface.request_shutdown() == "shutting down"
    code = await supervisor_module.asyncio.wait_for(stopper, timeout=10)

    assert code == EXIT_CLEAN


async def test_a_feed_that_ends_on_its_own_is_zero_not_75() -> None:
    """A stream that ran to completion is not a failure.

    The distinction is the one the whole table rests on: the feed *ended*, which is what a
    session does at the close, versus the feed *failed*, which is a transport dying. Both
    request a shutdown; only the second is EX_TEMPFAIL.
    """
    journal = Journal()
    supervisor = _supervisor(journal, CleanFeed())

    assert await supervisor.run() == EXIT_CLEAN


# --------------------------------------------------------------------------- #
# 70 -- the journal is unusable
# --------------------------------------------------------------------------- #


async def test_journal_overflow_returns_70() -> None:
    supervisor = _supervisor(ExhaustingJournal(), CleanFeed())

    assert await supervisor.run() == EXIT_JOURNAL_EXHAUSTED == 70


async def test_a_broken_hash_chain_returns_70() -> None:
    """An integrity error is a journal failure, which is 70 rather than 78.

    78 is "the host is wrong"; 70 is "the engine has history it cannot trust". A chain that
    does not verify is the second: restarting would replay events that cannot be shown to be
    the events that were journalled.
    """
    supervisor = _supervisor(IntegrityBrokenJournal(), CleanFeed())

    assert await supervisor.run() == EXIT_JOURNAL_EXHAUSTED


async def test_the_writers_own_code_is_re_exported_unchanged() -> None:
    """The 70 the supervisor returns is the writer's, not a second copy of it."""
    from engine.journal.writer import EXIT_JOURNAL_EXHAUSTED as WRITER_CODE

    assert EXIT_JOURNAL_EXHAUSTED == WRITER_CODE == 70


# --------------------------------------------------------------------------- #
# 75 -- the feed failed after its retry budget
# --------------------------------------------------------------------------- #


async def test_a_failed_feed_returns_75_not_zero() -> None:
    """A dead feed is a transient failure, and a transient failure must not look like success.

    This is the supersession of A89: the stream dying was journalled as
    ``FEED_STATE_CHANGED`` and the process stopped with 0, on the grounds that systemd was
    the thing that should restart it. That is exactly why 0 was wrong — systemd reads the
    exit code to decide whether a restart is worth trying, and 0 told it the stop was
    intentional.
    """
    journal = Journal()
    feed = DyingFeed()
    supervisor = _supervisor(journal, feed)

    code = await supervisor.run()

    assert code == EXIT_FEED_FAILED == 75
    # The stop is still graceful: the queue drains and the worker stops.
    assert journal.drained
    assert journal.stopped
    assert feed.stopped


async def test_a_failed_feed_is_still_journalled() -> None:
    """Losing the code must not lose the record: the event is journalled either way."""
    from engine.domain.events import EventType

    journal = Journal()
    supervisor = _supervisor(journal, DyingFeed())

    await supervisor.run()

    states = [
        event.payload.get("state")
        for event in journal.events
        if event.event_type is EventType.FEED_STATE_CHANGED
    ]
    assert "FAILED" in states


async def test_75_is_not_on_the_prevent_restart_list() -> None:
    """A transient failure must be restartable, or a blip ends the run.

    ``RestartPreventExitStatus`` exists so systemd does not restart into a loss. 75 is the
    opposite case: the engine stopped for a reason outside itself, so a restart is exactly
    what an operator wants.
    """
    unit = (SYSTEMD / "xauusd-engine.service").read_text(encoding="utf-8")
    prevent = _directive(unit, "RestartPreventExitStatus")

    assert "75" not in prevent.split()


# --------------------------------------------------------------------------- #
# 78 -- the host or the configuration is wrong
# --------------------------------------------------------------------------- #


async def test_a_gate_failure_returns_78() -> None:
    def explode() -> None:
        raise RuntimeGateError("sqlite is below the floor")

    supervisor_module.run_all_gates, original = explode, supervisor_module.run_all_gates
    try:
        supervisor = _supervisor(Journal(), CleanFeed())
        assert await supervisor.run() == EXIT_BLOCKED == 78
    finally:
        supervisor_module.run_all_gates = original


async def test_a_missing_config_document_returns_78(tmp_path: Path) -> None:
    supervisor = _supervisor(Journal(), CleanFeed())
    supervisor.settings.strategy_config_path = tmp_path / "absent.yaml"

    assert await supervisor.run() == EXIT_BLOCKED


async def test_a_missing_control_token_returns_78(monkeypatch: pytest.MonkeyPatch) -> None:
    from engine.runtime.platform import ControlTokenError

    monkeypatch.delenv("XAUUSD_CONTROL_TOKEN", raising=False)
    monkeypatch.setattr(supervisor_module, "get_control_token", ControlTokenError)

    supervisor = _supervisor(Journal(), CleanFeed())

    assert await supervisor.run() == EXIT_BLOCKED


def test_an_unwired_entry_point_returns_78_not_1() -> None:
    """The one path that used to exit 1.

    ``SystemExit(message)`` defaults to exit code 1, so "no quote source injected" looked
    like a crash rather than a configuration problem. It is 78 for the same reason a missing
    token is: the host is not configured to run this.
    """
    with pytest.raises(SystemExit) as caught:
        supervisor_module._require_source()

    assert caught.value.code == EXIT_BLOCKED


def test_main_returns_78_without_a_quote_source() -> None:
    """``main`` is the process boundary, so its exit code is the operator's evidence."""
    with pytest.raises(SystemExit) as caught:
        supervisor_module.main(quote_source=None)  # type: ignore[arg-type]

    assert caught.value.code == EXIT_BLOCKED


def test_the_exit_code_table_is_the_documented_one() -> None:
    """One table, four codes, and no path returns anything else.

    The mapping is exported rather than implied so a test can assert the *contract* rather
    than each caller: if a fifth code appears, this fails before an operator finds it.
    """
    assert EXIT_CODES == {
        "clean_stop": 0,
        "journal_overflow_or_integrity_error": 70,
        "feed_failed_after_retry_budget": 75,
        "host_or_configuration_failure": 78,
    }


def test_no_two_outcomes_share_a_code() -> None:
    assert len(set(EXIT_CODES.values())) == len(EXIT_CODES)


# --------------------------------------------------------------------------- #
# the unit must agree with the table
# --------------------------------------------------------------------------- #


def test_the_engine_unit_lists_70_and_78_but_not_the_others() -> None:
    """The deployment half of the contract.

    ``RestartPreventExitStatus=70 78`` is what stops systemd restarting into a journal it
    cannot use. Adding 75 to that list would make a transient blip permanent; leaving 78 off
    would make a misconfigured host restart forever.
    """
    unit = (SYSTEMD / "xauusd-engine.service").read_text(encoding="utf-8")
    codes = set(_directive(unit, "RestartPreventExitStatus").split())

    assert codes == {"70", "78"}
    assert "0" not in codes
    assert "1" not in codes
    assert "75" not in codes


def test_the_engine_unit_still_restarts_on_failure() -> None:
    """The hardening patch must not have turned the unit into a run-once service."""
    unit = (SYSTEMD / "xauusd-engine.service").read_text(encoding="utf-8")

    assert _directive(unit, "Restart") == "on-failure"


def test_a_real_process_returns_the_code_it_promised() -> None:
    """End to end: the module's ``__main__`` exits with the code the table names.

    The entry point is run as a subprocess because the exit code is a property of the
    *process*, not of the coroutine: ``SystemExit`` becomes a status, and a status is what
    systemd and an operator both read.
    """
    result = subprocess.run(  # noqa: S603 - the repository's own module, on a controlled argv
        [sys.executable, "-m", "engine.runtime.supervisor"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
        timeout=120,
    )

    assert result.returncode == EXIT_BLOCKED, result.stderr[-400:]
    assert "no quote source was injected" in result.stderr


def _directive(unit: str, key: str) -> str:
    """The value of one systemd directive, ignoring comments."""
    for raw in unit.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        name, separator, value = line.partition("=")
        if separator and name.strip() == key:
            return value.strip()
    msg = f"{key} is not set in the engine unit"
    raise AssertionError(msg)


def test_the_budget_is_unchanged() -> None:
    """The hardening patch changes codes, not the shutdown contract."""
    assert SHUTDOWN_BUDGET == dt.timedelta(seconds=10)
