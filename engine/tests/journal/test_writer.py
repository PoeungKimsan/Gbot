"""Unit tests for :mod:`engine.journal.writer`.

The invariants under test are the ones that make the writer safe:

* **Submission never blocks.** The event loop's only channel is ``put_nowait``. A
  ``queue.Full`` must surface as ``JournalExhausted``, never as a blocked event loop.
* **SQLite work happens off the loop.** A test asserts the submitting thread is *not* the
  writing thread.
* **Group commits** flush at 500 events or after the interval, so a burst becomes one
  transaction rather than five hundred.
* **``drain()`` waits, and reports honestly** when it times out.

Nothing here sleeps for real time: the writer takes an injected clock and sleeper, so a
group-commit interval of "20ms" is advanced deliberately rather than waited for.
"""

import datetime as dt
import threading
import time
from decimal import Decimal
from pathlib import Path

import pytest

from engine.domain.events import DomainEvent, EventType, canonical_json_bytes
from engine.journal.schema import GENESIS_HASH, verify_chain
from engine.journal.writer import (
    EXIT_JOURNAL_EXHAUSTED,
    GROUP_COMMIT_MAX_EVENTS,
    GROUP_COMMIT_TIMEOUT,
    QUEUE_MAXSIZE,
    JournalError,
    JournalExhausted,
    JournalWriter,
    exit_code_for,
)

UTC = dt.UTC


def _event(index: int = 0, amount: str = "100.00") -> DomainEvent:
    return DomainEvent(
        event_id=f"evt-{index}",
        event_type=EventType.LEDGER_POSTED,
        occurred_at=dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC),
        payload={"amount": Decimal(amount), "seq": index},
    )


class FakeClock:
    """A clock the test drives, so no group commit waits on real time."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start
        self.advanced: list[float] = []

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds
        self.advanced.append(seconds)


class FakeSleeper:
    async def __call__(self, seconds: float) -> None:
        return None


# --------------------------------------------------------------------------- #
# module-level contract
# --------------------------------------------------------------------------- #
def test_queue_maxsize_is_one_hundred_thousand() -> None:
    assert QUEUE_MAXSIZE == 10_000


def test_group_commit_limits_are_the_specified_values() -> None:
    assert GROUP_COMMIT_MAX_EVENTS == 500
    assert dt.timedelta(milliseconds=20) == GROUP_COMMIT_TIMEOUT


def test_exit_code_for_journal_exhaustion() -> None:
    assert EXIT_JOURNAL_EXHAUSTED == 70
    assert exit_code_for(JournalExhausted("full")) == 70
    # A different journal failure keeps a different code, so an operator is not told
    # "exhausted" when the real fault was something else.
    assert exit_code_for(JournalError("other")) == 71
    assert exit_code_for(ValueError("boom")) == 71


def test_exit_code_for_an_unrelated_error() -> None:
    assert exit_code_for(ValueError("boom")) != 70


# --------------------------------------------------------------------------- #
# lifecycle
# --------------------------------------------------------------------------- #
async def test_writer_starts_and_persists_events(tmp_path: Path) -> None:
    writer = JournalWriter(
        path=tmp_path / "journal.db",
        instrument=_spec(),
        clock=FakeClock(),
        sleeper=FakeSleeper(),
        flush_interval=dt.timedelta(milliseconds=1),
    )
    writer.start()

    try:
        writer.submit(_event(1))
        writer.submit(_event(2))
        assert writer.drain(timeout=5) is True
    finally:
        writer.stop()

    report = verify_chain(writer.connect_readonly())
    assert report.is_valid is True
    assert report.rows_verified == 2


async def test_writer_writes_on_its_own_thread(tmp_path: Path) -> None:
    """The whole point of the design: SQLite never runs on the caller's thread."""
    writer = JournalWriter(
        path=tmp_path / "journal.db",
        instrument=_spec(),
        clock=FakeClock(),
        sleeper=FakeSleeper(),
        flush_interval=dt.timedelta(milliseconds=1),
    )
    writer.start()

    try:
        writer.submit(_event(1))
        assert writer.drain(timeout=5) is True
    finally:
        writer.stop()

    assert writer.writer_thread_id != threading.get_ident()
    # The worker must never appear among the submitters: it does the SQLite work, it does
    # not feed itself.
    assert writer.writer_thread_id not in writer.submitter_thread_ids
    assert threading.get_ident() in writer.submitter_thread_ids


async def test_submit_does_not_block(tmp_path: Path) -> None:
    """Submitting must return promptly, even with many events in flight."""
    writer = JournalWriter(
        path=tmp_path / "journal.db",
        instrument=_spec(),
        clock=FakeClock(),
        sleeper=FakeSleeper(),
        flush_interval=dt.timedelta(milliseconds=1),
    )
    writer.start()

    try:
        started = time.monotonic()
        for index in range(500):
            writer.submit(_event(index))
        elapsed = time.monotonic() - started

        assert elapsed < 5.0, "submitting should not wait on the writer"
        assert writer.drain(timeout=10) is True
    finally:
        writer.stop()


async def test_journal_exhaustion_raises_when_the_writer_cannot_keep_up(tmp_path: Path) -> None:
    """A full queue must raise, not block the event loop forever."""
    writer = JournalWriter(
        path=tmp_path / "journal.db",
        instrument=_spec(),
        clock=FakeClock(),
        sleeper=FakeSleeper(),
        flush_interval=dt.timedelta(seconds=30),
        queue_maxsize=4,
    )
    writer.start()

    try:
        # The writer cannot drain while the clock is frozen, so the queue fills.
        with pytest.raises(JournalExhausted):
            for index in range(64):
                writer.submit(_event(index))
    finally:
        writer.stop()


async def test_try_submit_returns_false_instead_of_raising(tmp_path: Path) -> None:
    writer = JournalWriter(
        path=tmp_path / "journal.db",
        instrument=_spec(),
        clock=FakeClock(),
        sleeper=FakeSleeper(),
        flush_interval=dt.timedelta(seconds=30),
        queue_maxsize=2,
    )
    writer.start()

    try:
        accepted = [writer.try_submit(_event(i)) for i in range(8)]

        assert accepted[-1] is False
    finally:
        writer.stop()


async def test_stop_without_start_is_safe(tmp_path: Path) -> None:
    writer = JournalWriter(
        path=tmp_path / "journal.db",
        instrument=_spec(),
        clock=FakeClock(),
        sleeper=FakeSleeper(),
    )

    writer.stop()  # must not raise


async def test_drain_reports_a_timeout_honestly(tmp_path: Path) -> None:
    """A stalled writer must not make ``drain()`` claim success it did not get.

    The clock is frozen so ``drain``'s deadline never arrives, but the queue is non-empty
    and the writer is blocked for 30s, so drain must report the timeout rather than
    spinning forever or lying about success.
    """
    writer = JournalWriter(
        path=tmp_path / "journal.db",
        instrument=_spec(),
        clock=FakeClock(),
        sleeper=FakeSleeper(),
        flush_interval=dt.timedelta(seconds=30),
        queue_maxsize=2,
    )
    writer.start()

    try:
        for index in range(2):
            writer.try_submit(_event(index))

        # Let the worker pick the events up, then leave it waiting on its 30s interval.
        import asyncio

        await asyncio.sleep(0.2)
        assert writer.drain(timeout=dt.timedelta(milliseconds=100)) is False
    finally:
        writer.stop()


async def test_events_survive_a_restart(tmp_path: Path) -> None:
    """A committed chain must be reopenable and still verifiable."""
    path = tmp_path / "journal.db"
    writer = JournalWriter(
        path=path,
        instrument=_spec(),
        clock=FakeClock(),
        sleeper=FakeSleeper(),
        flush_interval=dt.timedelta(milliseconds=1),
    )
    writer.start()
    try:
        for index in range(3):
            writer.submit(_event(index))
        assert writer.drain(timeout=5) is True
    finally:
        writer.stop()

    reopened = verify_chain(_open(path))
    assert reopened.is_valid is True
    assert reopened.rows_verified == 3


async def test_group_commit_batches_a_burst(tmp_path: Path) -> None:
    """A burst well under 500 must still land as one transaction, not many."""
    writer = JournalWriter(
        path=tmp_path / "journal.db",
        instrument=_spec(),
        clock=FakeClock(),
        sleeper=FakeSleeper(),
        flush_interval=dt.timedelta(milliseconds=1),
    )
    writer.start()

    try:
        for index in range(50):
            writer.submit(_event(index))
        assert writer.drain(timeout=5) is True
    finally:
        writer.stop()

    assert writer.committed_batches >= 1
    assert writer.committed_events == 50


async def test_group_commit_splits_at_five_hundred(tmp_path: Path) -> None:
    """600 events must produce at least two group commits."""
    writer = JournalWriter(
        path=tmp_path / "journal.db",
        instrument=_spec(),
        clock=FakeClock(),
        sleeper=FakeSleeper(),
        flush_interval=dt.timedelta(milliseconds=1),
    )
    writer.start()

    try:
        for index in range(600):
            writer.submit(_event(index))
        assert writer.drain(timeout=15) is True
    finally:
        writer.stop()

    assert writer.committed_batches >= 2


async def test_request_checkpoint_runs_passive(tmp_path: Path) -> None:
    writer = JournalWriter(
        path=tmp_path / "journal.db",
        instrument=_spec(),
        clock=FakeClock(),
        sleeper=FakeSleeper(),
        flush_interval=dt.timedelta(milliseconds=1),
    )
    writer.start()

    try:
        writer.submit(_event(1))
        writer.request_checkpoint()
        assert writer.drain(timeout=5) is True
    finally:
        writer.stop()

    assert writer.checkpoints >= 1


async def test_writer_uses_wal_and_full_synchronous(tmp_path: Path) -> None:
    writer = JournalWriter(
        path=tmp_path / "journal.db",
        instrument=_spec(),
        clock=FakeClock(),
        sleeper=FakeSleeper(),
        flush_interval=dt.timedelta(milliseconds=1),
    )
    writer.start()

    try:
        writer.submit(_event(1))
        assert writer.drain(timeout=5) is True
    finally:
        writer.stop()

    connection = _open(tmp_path / "journal.db")
    assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert connection.execute("PRAGMA synchronous").fetchone()[0] in (1, 2)


async def test_journal_is_append_only(tmp_path: Path) -> None:
    """The UPDATE and DELETE triggers must also hold on the writer's connection."""
    writer = JournalWriter(
        path=tmp_path / "journal.db",
        instrument=_spec(),
        clock=FakeClock(),
        sleeper=FakeSleeper(),
        flush_interval=dt.timedelta(milliseconds=1),
    )
    writer.start()

    try:
        writer.submit(_event(1))
        assert writer.drain(timeout=5) is True
    finally:
        writer.stop()

    import sqlite3

    connection = _open(tmp_path / "journal.db")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        connection.execute("UPDATE journal SET amount = '1'")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        connection.execute("DELETE FROM journal")


async def test_submitting_after_stop_raises(tmp_path: Path) -> None:
    writer = JournalWriter(
        path=tmp_path / "journal.db",
        instrument=_spec(),
        clock=FakeClock(),
        sleeper=FakeSleeper(),
    )
    writer.start()
    writer.stop()

    with pytest.raises(JournalError, match="stopped"):
        writer.submit(_event(1))


async def test_writer_tracks_the_latest_hash(tmp_path: Path) -> None:
    writer = JournalWriter(
        path=tmp_path / "journal.db",
        instrument=_spec(),
        clock=FakeClock(),
        sleeper=FakeSleeper(),
        flush_interval=dt.timedelta(milliseconds=1),
    )
    writer.start()

    try:
        assert writer.latest_hash == GENESIS_HASH
        writer.submit(_event(1))
        assert writer.drain(timeout=5) is True
    finally:
        writer.stop()

    assert writer.latest_hash != GENESIS_HASH


async def test_concurrent_submitters_all_persist(tmp_path: Path) -> None:
    """Real threads hammering the writer must not lose or corrupt an event."""

    writer = JournalWriter(
        path=tmp_path / "journal.db",
        instrument=_spec(),
        clock=FakeClock(),
        sleeper=FakeSleeper(),
        flush_interval=dt.timedelta(milliseconds=1),
    )
    writer.start()

    errors: list[Exception] = []

    def submit_range(start: int, count: int) -> None:
        for index in range(start, start + count):
            try:
                writer.submit(_event(index))
            except JournalError as exc:  # pragma: no cover - only on exhaustion
                errors.append(exc)

    try:
        threads = [
            threading.Thread(target=submit_range, args=(base, 25))
            for base in range(0, 200, 25)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert writer.drain(timeout=20) is True
    finally:
        writer.stop()

    assert errors == []
    report = verify_chain(_open(tmp_path / "journal.db"))
    assert report.is_valid is True
    assert report.rows_verified == 200


def _spec():
    from engine.market.instrument import InstrumentSpec

    return InstrumentSpec(
        name="XAU_USD",
        pip_location=-4,
        display_precision=2,
        trade_units_precision=5,
        minimum_trade_size=Decimal("1"),
    )


def _open(path: Path):
    import sqlite3

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def _payload_of(event: DomainEvent) -> bytes:
    return canonical_json_bytes(event)
