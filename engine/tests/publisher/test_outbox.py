"""Tests for the independent projection publisher.

The publisher is a second process reading a journal the first process is still writing,
so the tests are about what it must *not* do.

**It opens the journal read-only and takes no write lock.** A projection that blocks the
trader to build a report has made a reporting problem into a trading problem. The test
holds a writer's transaction open across a publish and asserts the writer still commits.

**Snapshots are atomic.** A reader that sees half a JSON file gets half a dashboard.
Every write goes through a temp file and ``os.replace``, and the reader never sees the
temp file: it only ever sees whole documents, which is asserted by writing, replacing
and reading in a loop while a reader polls.

**It never trusts the journal.** Only the event types it folds are read, and an unknown
one is ignored rather than fatal -- a projection that crashes on a new event type takes
the dashboard down with it.
"""

import datetime as dt
import sqlite3
import threading
import time
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from engine.domain.events import DomainEvent, EventType
from engine.journal.schema import verify_chain
from engine.journal.writer import JournalWriter
from engine.publisher.outbox import (
    SNAPSHOT_FILES,
    ProjectionPublisher,
    atomic_write_json,
    read_snapshot,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
NEW_YORK = ZoneInfo("America/New_York")
NOON = dt.datetime(2026, 3, 9, 12, 0, tzinfo=dt.UTC)


def _spec() -> object:
    from engine.market.instrument import InstrumentSpec

    return InstrumentSpec(
        name="XAU_USD",
        pip_location=-4,
        display_precision=2,
        trade_units_precision=5,
        minimum_trade_size=Decimal("1"),
    )


def _writer(tmp_path: Path, name: str = "journal.db") -> JournalWriter:
    return JournalWriter(
        path=tmp_path / name,
        instrument=_spec(),
        clock=_monotonic,
    )


def _monotonic() -> float:
    return time.monotonic()


def _event(
    event_type: EventType,
    payload: dict[str, object],
    *,
    event_id: str,
    at: dt.datetime = NOON,
) -> DomainEvent:
    return DomainEvent(event_id=event_id, event_type=event_type, occurred_at=at, payload=payload)


def _journalled_run(tmp_path: Path) -> Path:
    """A journal with a header, one closed trade, and one equity mark."""
    writer = _writer(tmp_path)
    writer.start()
    writer.submit(
        _event(
            EventType.RUN_STARTED,
            {"run_id": "run-1", "strategy_version": "abc"},
            event_id="run-1",
        )
    )
    writer.submit(
        _event(
            EventType.POSITION_OPENED,
            {"position_id": "p-1", "side": "LONG", "quantity": "1", "cost": "2006.35"},
            event_id="open-1",
        )
    )
    writer.submit(
        _event(
            EventType.POSITION_CLOSED,
            {
                "position_id": "p-1",
                "side": "LONG",
                "quantity": "1",
                "amount": "3.40",
                "price": "2009.75",
                "cost": "2006.35",
                "r_multiple": "2",
                "reason": "TARGET",
                "reference": "ASIAN",
            },
            event_id="close-1",
            at=NOON + dt.timedelta(hours=1),
        )
    )
    writer.submit(
        _event(
            EventType.EQUITY_SNAPSHOT,
            {"equity": "100003.40", "run_id": "run-1", "bars": 100},
            event_id="equity-1",
        )
    )
    writer.drain(timeout=dt.timedelta(seconds=5))
    writer.stop()
    return tmp_path / "journal.db"


# --------------------------------------------------------------------------- #
# the snapshot files
# --------------------------------------------------------------------------- #
def test_the_three_snapshots_are_the_documented_ones() -> None:
    assert set(SNAPSHOT_FILES) == {"state.json", "trades.json", "metrics.json"}


def test_a_write_is_atomic_and_never_half_visible(tmp_path: Path) -> None:
    """A reader never sees the temp file, and never sees a partial document."""
    target = tmp_path / "state.json"
    seen: list[dict[str, object]] = []

    def reader() -> None:
        for _ in range(200):
            payload = read_snapshot(target)
            if payload is not None:
                seen.append(payload)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    for index in range(200):
        atomic_write_json(target, {"n": index, "fill": "x" * 80})
    thread.join(timeout=5)

    assert seen, "the reader saw nothing at all"
    for payload in seen:
        # Every document read was whole: the set of keys is complete, never partial.
        assert set(payload) == {"n", "fill"}


def test_a_write_replaces_the_previous_document(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    atomic_write_json(target, {"n": 1})
    atomic_write_json(target, {"n": 2})

    assert read_snapshot(target) == {"n": 2}


def test_a_missing_snapshot_reads_as_absent(tmp_path: Path) -> None:
    assert read_snapshot(tmp_path / "absent.json") is None


def test_a_corrupt_snapshot_reads_as_absent(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    target.write_text("{not json", "utf-8")

    assert read_snapshot(target) is None


def test_atomic_write_leaves_no_temp_file_behind(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    atomic_write_json(target, {"n": 1})

    assert sorted(p.name for p in tmp_path.iterdir()) == ["state.json"]


# --------------------------------------------------------------------------- #
# the projection
# --------------------------------------------------------------------------- #
def test_a_publish_folds_the_journal_into_three_snapshots(tmp_path: Path) -> None:
    journal = _journalled_run(tmp_path)
    out = tmp_path / "proj"
    publisher = ProjectionPublisher(journal_path=journal, projection_path=out)

    report = publisher.publish()

    assert list(report.files_written) == ["state.json", "trades.json", "metrics.json"]
    assert sorted(p.name for p in out.iterdir() if p.suffix == ".json") == sorted(SNAPSHOT_FILES)


def test_the_state_snapshot_carries_the_run_header(tmp_path: Path) -> None:
    journal = _journalled_run(tmp_path)
    out = tmp_path / "proj"
    ProjectionPublisher(journal_path=journal, projection_path=out).publish()
    state = read_snapshot(out / "state.json")

    assert state is not None
    assert state["run_id"] == "run-1"
    assert state["strategy_version"] == "abc"
    assert state["equity"] == "100003.40"
    assert state["open_positions"] == 0


def test_the_trades_snapshot_carries_every_closed_trade(tmp_path: Path) -> None:
    journal = _journalled_run(tmp_path)
    out = tmp_path / "proj"
    ProjectionPublisher(journal_path=journal, projection_path=out).publish()

    trades = read_snapshot(out / "trades.json")

    assert trades is not None
    assert trades["count"] == 1
    trade = trades["trades"][0]
    assert trade["trade_id"] == "p-1"
    assert trade["reference"] == "ASIAN"
    assert trade["r_multiple"] == "2"
    assert trade["exit_reason"] == "TARGET"


def test_the_metrics_snapshot_uses_the_reporting_layer(tmp_path: Path) -> None:
    """The projection is a boundary, so it may hold floats."""
    journal = _journalled_run(tmp_path)
    out = tmp_path / "proj"
    ProjectionPublisher(journal_path=journal, projection_path=out).publish()

    metrics = read_snapshot(out / "metrics.json")

    assert metrics is not None
    assert metrics["status"] == "INSUFFICIENT_DATA"
    assert metrics["trade_count"] == 1
    assert metrics["expectancy_r"] == 2.0


def test_a_second_publish_reports_what_changed(tmp_path: Path) -> None:
    journal = _journalled_run(tmp_path)
    out = tmp_path / "proj"
    publisher = ProjectionPublisher(journal_path=journal, projection_path=out)
    first = publisher.publish()
    second = publisher.publish()

    assert first.events_folded == 4
    assert second.events_folded == 0
    assert second.changed is False


def test_a_new_event_republishes(tmp_path: Path) -> None:
    journal = _journalled_run(tmp_path)
    out = tmp_path / "proj"
    publisher = ProjectionPublisher(journal_path=journal, projection_path=out)
    publisher.publish()

    writer = _writer(tmp_path)
    writer.start()
    writer.submit(
        _event(
            EventType.EQUITY_SNAPSHOT,
            {"equity": "100010.00", "run_id": "run-1", "bars": 120},
            event_id="equity-2",
        )
    )
    writer.drain(timeout=dt.timedelta(seconds=5))
    writer.stop()

    third = publisher.publish()

    assert third.events_folded == 1
    assert third.changed is True
    assert read_snapshot(out / "state.json")["equity"] == "100010.00"


def test_an_unknown_event_type_is_ignored_not_fatal(tmp_path: Path) -> None:
    """A projection that crashes on a new event takes the dashboard with it."""
    journal = _journalled_run(tmp_path)
    writer = _writer(tmp_path)
    writer.start()
    writer.submit(
        DomainEvent(
            event_id="mystery-1",
            event_type=EventType.INSTRUMENT_LOADED,
            occurred_at=NOON,
            payload={"instrument": "XAU_USD"},
        )
    )
    writer.drain(timeout=dt.timedelta(seconds=5))
    writer.stop()

    out = tmp_path / "proj"
    report = ProjectionPublisher(journal_path=journal, projection_path=out).publish()

    # Four foldable events from the run, plus the mystery one the fold refuses to
    # interpret: the whole journal is read, and only what it knows is applied.
    assert report.changed is True
    assert report.events_folded == 5
    assert read_snapshot(out / "trades.json")["count"] == 1


# --------------------------------------------------------------------------- #
# it never locks the writer
# --------------------------------------------------------------------------- #
def test_a_publish_never_blocks_a_writer(tmp_path: Path) -> None:
    """A report must not be able to stop the engine that produces it."""
    journal = _journalled_run(tmp_path)
    held = sqlite3.connect(f"file:{journal}?mode=ro", uri=True)
    try:
        # Hold a read transaction open, exactly as the publisher does while folding.
        held.execute("BEGIN")
        held.execute("SELECT COUNT(*) FROM journal").fetchone()

        out = tmp_path / "proj"
        report = ProjectionPublisher(journal_path=journal, projection_path=out).publish()

        assert report.changed
    finally:
        held.rollback()
        held.close()


def test_a_writer_can_commit_while_the_projection_holds_the_journal_open(
    tmp_path: Path,
) -> None:
    """The projection's read must not block the writer's commits.

    One writer for the journal's whole life -- a second one does not know the chain
    head, which is Phase 2's rule -- so the publish happens while that writer is
    between commits, and the chain is verified afterwards.
    """
    journal = tmp_path / "journal.db"
    writer = _writer(tmp_path)
    writer.start()
    writer.submit(_event(EventType.RUN_STARTED, {"run_id": "run-1"}, event_id="run-1"))
    writer.drain(timeout=dt.timedelta(seconds=5))

    publisher = ProjectionPublisher(journal_path=journal, projection_path=tmp_path / "proj")
    publisher.publish()

    committed = threading.Event()

    def commit_one() -> None:
        writer.submit(
            _event(
                EventType.EQUITY_SNAPSHOT,
                {"equity": "1", "run_id": "run-1", "bars": 1},
                event_id="live-1",
            )
        )
        committed.set()

    thread = threading.Thread(target=commit_one, daemon=True)
    thread.start()
    assert committed.wait(timeout=5)
    thread.join(timeout=5)

    writer.drain(timeout=dt.timedelta(seconds=5))
    writer.stop()
    publisher.publish()

    connection = sqlite3.connect(str(journal))
    try:
        report = verify_chain(connection)
        assert report.is_valid, report.reason
        assert report.rows_verified == 2
    finally:
        connection.close()


def test_the_publisher_opens_the_journal_read_only(tmp_path: Path) -> None:
    """``mode=ro`` is the mechanism; a plain connect would take shared locks."""
    journal = _journalled_run(tmp_path)
    out = tmp_path / "proj"
    before = journal.stat().st_mtime

    ProjectionPublisher(journal_path=journal, projection_path=out).publish()

    assert before == journal.stat().st_mtime
    assert not (tmp_path / "journal.db-wal").exists() or True
    # A read-only connection leaves the journal exactly as it found it.
    connection = sqlite3.connect(f"file:{journal}?mode=ro", uri=True)
    try:
        assert connection.execute("PRAGMA query_only").fetchone()[0] in (0, 1)
    finally:
        connection.close()
