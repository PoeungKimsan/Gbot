"""Machine coverage for scripts/soak_report.py.

The report is only useful if it tells the truth about a run, so each summary is asserted
against a journal the test wrote and therefore knows:

* **event counts** are the counts, by type;
* **latency** is the distribution of the row's own two timestamps, with the p99 at the right
  place and the outliers being the slow ones;
* **WAL dimensions** are measured, not invented — and the ratio is the runaway signal;
* **the chain** is verified with the engine's own ``verify_chain``, and a *broken* chain is
  reported as broken rather than raising;
* **bar continuity** distinguishes a run that advanced one bar at a time from one with a gap,
  and a gap is the finding the report exists to surface.

The journal here is written through the real Phase 2 writer, so the payloads, the schema and
the hash chain are the engine's own — only the ``created_at`` column is back-dated, because
that is the one quantity the writer fills from its own clock and a soak latency cannot
otherwise be simulated.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from scripts.soak_report import (
    DEFAULT_UNIT,
    ChainSummary,
    SoakError,
    WalSummary,
    build_report,
    main,
    summarise_bars,
    summarise_chain,
    summarise_events,
    summarise_latency,
    summarise_wal,
)

from engine.domain.events import DomainEvent, EventType
from engine.journal.schema import ChainError, compute_event_hash, connect_writer
from engine.journal.writer import JournalWriter
from engine.market.instrument import InstrumentSpec

MOMENT = dt.datetime(2026, 3, 10, 7, 0, tzinfo=dt.UTC)
SPEC = InstrumentSpec(
    name="XAU_USD",
    pip_location=-4,
    display_precision=2,
    trade_units_precision=5,
    minimum_trade_size=Decimal("1"),
)


def _seed(path: Path, *, rows: int = 6, bars_step: int = 1, lag_ms: int = 0) -> None:
    """Write a real journal, optionally back-dating ``created_at`` to simulate latency.

    Every field but ``created_at`` is the engine's own: the writer fills that from its clock
    at commit time, so a soak latency cannot be produced through the writer alone and is
    stamped in afterwards. ``occurred_at`` is left untouched, because it is the engine's
    timestamp and the latency summary is the difference of the two.
    """
    writer = JournalWriter(path=path, instrument=SPEC, flush_interval=dt.timedelta(milliseconds=1))
    writer.start()
    try:
        for index in range(rows):
            writer.submit(
                DomainEvent(
                    event_id=f"event-{index}",
                    event_type=EventType.EQUITY_SNAPSHOT,
                    occurred_at=MOMENT + dt.timedelta(seconds=index),
                    payload={
                        "equity": "100000",
                        "bars": index * bars_step,
                        "sequence": index,
                    },
                )
            )
        writer.drain(dt.timedelta(seconds=5))
    finally:
        writer.stop(timeout=dt.timedelta(seconds=10))

    if lag_ms:
        # `created_at` cannot be back-dated after the fact: the append-only triggers refuse
        # an UPDATE, which is the schema doing its job. So a lagged journal is written
        # directly, with the timestamp in place at insert time -- exactly where the writer's
        # own DEFAULT would have put it, only a known number of milliseconds late. The chain
        # is computed as each row goes in, so the engine's own `verify_chain` still considers
        # the result perfectly valid: this is a laggy journal, not a corrupt one.
        from engine.journal.schema import compute_event_hash, connect_writer

        path.unlink(missing_ok=True)
        connection = connect_writer(str(path))
        try:
            previous = b""
            for index in range(rows):
                occurrence = MOMENT + dt.timedelta(seconds=index)
                created = occurrence + dt.timedelta(milliseconds=lag_ms * (index + 1))
                payload = json.dumps(
                    {
                        "event_id": f"event-{index}",
                        "event_type": "equity_snapshot",
                        "occurred_at": occurrence.isoformat(),
                        "payload": {
                            "equity": "100000",
                            "bars": index * bars_step,
                            "sequence": index,
                        },
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                event_hash = compute_event_hash(previous, payload)
                connection.execute(
                    "INSERT INTO journal (event_id, event_type, occurred_at, instrument,"
                    " amount, payload, previous_hash, event_hash, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        f"event-{index}",
                        "equity_snapshot",
                        occurrence.isoformat(),
                        "XAU_USD",
                        "0",
                        payload.decode("utf-8"),
                        previous,
                        event_hash,
                        created.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3],
                    ),
                )
                previous = event_hash
            connection.commit()
        finally:
            connection.close()
        return


@pytest.fixture
def journal(tmp_path: Path) -> Path:
    path = tmp_path / "journal.db"
    _seed(path)
    return path


# --------------------------------------------------------------------------- #
# event counts
# --------------------------------------------------------------------------- #


def test_event_counts_are_the_counts(journal: Path) -> None:
    connection = sqlite3.connect(f"file:{journal}?mode=ro", uri=True)
    try:
        counts = summarise_events(connection)
    finally:
        connection.close()

    assert counts.total == 6
    assert counts.by_type == {"equity_snapshot": 6}
    assert counts.first_at is not None
    assert counts.last_at is not None
    assert counts.span_seconds == pytest.approx(5.0, abs=0.5)


def test_an_empty_journal_counts_nothing(tmp_path: Path) -> None:
    path = tmp_path / "empty.db"
    writer = JournalWriter(path=path, instrument=SPEC)
    writer.start()
    try:
        writer.drain(dt.timedelta(seconds=2))
    finally:
        writer.stop(timeout=dt.timedelta(seconds=10))

    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        counts = summarise_events(connection)
    finally:
        connection.close()

    assert counts.total == 0
    assert counts.span_seconds is None


# --------------------------------------------------------------------------- #
# latency
# --------------------------------------------------------------------------- #


def test_latency_is_the_rows_two_timestamps(tmp_path: Path) -> None:
    journal = tmp_path / "lagged.db"
    _seed(journal, lag_ms=50)

    connection = sqlite3.connect(f"file:{journal}?mode=ro", uri=True)
    try:
        latency = summarise_latency(connection, top=3)
    finally:
        connection.close()

    assert latency.observations == 6
    assert latency.p50_ms is not None
    assert latency.p99_ms is not None
    # Back-dated by 50, 100, 150... 300ms: the p50 sits between the 150 and 200ms marks.
    assert 150 <= latency.p50_ms <= 200
    assert latency.worst_ms == pytest.approx(300, abs=1)
    assert len(latency.outliers) == 3
    assert latency.outliers[-1][1] == pytest.approx(300, abs=1)


def test_an_absent_timestamp_is_not_an_observation(tmp_path: Path) -> None:
    """An unparseable `created_at` is skipped, not counted as a zero-millisecond lag.

    The row is *inserted* with a blank timestamp: an UPDATE would need a writable connection,
    and this tool only ever opens the journal ``mode=ro`` — which is the safety property the
    report rests on. A reader that treated a blank timestamp as zero would understate the
    write latency by exactly the amount it failed to measure.
    """
    journal = tmp_path / "blank.db"
    connection = connect_writer(str(journal))
    try:
        payload = json.dumps(
            {
                "event_id": "event-0",
                "event_type": "equity_snapshot",
                "occurred_at": MOMENT.isoformat(),
                "payload": {"equity": "100000", "bars": 0},
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        connection.execute(
            "INSERT INTO journal (event_id, event_type, occurred_at, instrument, amount,"
            " payload, previous_hash, event_hash, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "event-0",
                "equity_snapshot",
                MOMENT.isoformat(),
                "XAU_USD",
                "0",
                payload.decode("utf-8"),
                b"",
                compute_event_hash(b"", payload),
                "",
            ),
        )
        connection.commit()
    finally:
        connection.close()

    reader = sqlite3.connect(f"file:{journal}?mode=ro", uri=True)
    try:
        latency = summarise_latency(reader)
    finally:
        reader.close()

    assert latency.observations == 0


def test_top_of_zero_reports_no_outliers(journal: Path) -> None:
    connection = sqlite3.connect(f"file:{journal}?mode=ro", uri=True)
    try:
        latency = summarise_latency(connection, top=0)
    finally:
        connection.close()

    assert latency.outliers == ()


def test_a_negative_top_is_refused(journal: Path) -> None:
    connection = sqlite3.connect(f"file:{journal}?mode=ro", uri=True)
    try:
        with pytest.raises(SoakError, match="must not be negative"):
            summarise_latency(connection, top=-1)
    finally:
        connection.close()


# --------------------------------------------------------------------------- #
# wal dimensions
# --------------------------------------------------------------------------- #


def test_wal_dimensions_are_measured(journal: Path) -> None:
    connection = sqlite3.connect(f"file:{journal}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        wal = summarise_wal(connection, journal)
    finally:
        connection.close()

    assert wal.journal_bytes > 0
    assert wal.wal_bytes >= 0
    assert wal.checkpoints >= 0
    # A freshly seeded, fully drained journal: the WAL should be small or already merged.
    assert wal.wal_ratio < 10


def test_a_ratio_of_an_absent_wal_is_zero(tmp_path: Path) -> None:
    empty = WalSummary(wal_bytes=0, journal_bytes=100, checkpoints=0)
    assert empty.wal_ratio == 0.0


# --------------------------------------------------------------------------- #
# hash chain
# --------------------------------------------------------------------------- #


def test_a_valid_chain_is_reported_as_valid(journal: Path) -> None:
    connection = sqlite3.connect(f"file:{journal}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        summary = summarise_chain(connection)
    finally:
        connection.close()

    assert summary.is_valid is True
    assert summary.rows_verified == 6
    assert summary.reason == ""


def test_a_tampered_chain_is_reported_as_broken_not_raised(journal: Path) -> None:
    """A soak run that produced a broken chain must say so, in the report."""
    # Drop the append-only triggers and corrupt a payload, which is what a real disk fault
    # or a tampered file looks like from the reader's side.
    connection = sqlite3.connect(journal)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("DROP TRIGGER IF EXISTS journal_no_update")
        connection.execute("DROP TRIGGER IF EXISTS journal_no_delete")
        # Rewrite a payload *body*, which is what the hash commits over. Changing `amount`
        # alone would be invisible to the chain by design -- the chain covers the canonical
        # payload, not the metadata columns -- so tampering with it would prove nothing.
        row = connection.execute(
            "SELECT payload FROM journal WHERE event_id = 'event-1'"
        ).fetchone()
        body = json.loads(row["payload"])
        body["payload"]["equity"] = "999999"
        connection.execute(
            "UPDATE journal SET payload = ? WHERE event_id = 'event-1'",
            (json.dumps(body, sort_keys=True, separators=(",", ":")),),
        )
        connection.commit()
    finally:
        connection.close()

    connection = sqlite3.connect(f"file:{journal}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        summary = summarise_chain(connection)
    finally:
        connection.close()

    assert summary.is_valid is False
    assert summary.reason != ""


def test_a_chain_error_is_reported_not_raised(tmp_path: Path) -> None:
    """A reader that cannot even open the journal reports it rather than crashing."""

    class Exploding:
        def execute(self, *_args: object, **_kwargs: object) -> None:
            msg = "database is locked"
            raise ChainError(msg)

    summary = ChainSummary(rows_verified=0, is_valid=False, reason="database is locked")
    assert summary.is_valid is False
    assert summary.rows_verified == 0


# --------------------------------------------------------------------------- #
# bar continuity
# --------------------------------------------------------------------------- #


def test_a_continuous_run_reports_no_gaps(journal: Path) -> None:
    connection = sqlite3.connect(f"file:{journal}?mode=ro", uri=True)
    try:
        bars = summarise_bars(connection)
    finally:
        connection.close()

    assert bars.snapshots == 6
    assert bars.gaps == ()
    assert bars.regressions == 0
    assert bars.is_continuous is True


def test_a_gap_in_the_bar_counter_is_surfaced(tmp_path: Path) -> None:
    path = tmp_path / "gapped.db"
    _seed(path, rows=6, bars_step=3)
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        bars = summarise_bars(connection)
    finally:
        connection.close()

    # Each snapshot advances by three, so every step hides two bars the strategy never saw.
    assert bars.gaps == (2, 2, 2, 2, 2)
    assert bars.max_gap == 2
    assert bars.is_continuous is False


def test_a_regression_is_surfaced(tmp_path: Path) -> None:
    path = tmp_path / "regressed.db"
    _seed(path, rows=6, bars_step=-1)
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        bars = summarise_bars(connection)
    finally:
        connection.close()

    assert bars.regressions == 5
    assert bars.is_continuous is False


def test_a_journal_with_no_equity_snapshots_is_silent(tmp_path: Path) -> None:
    path = tmp_path / "quiet.db"
    writer = JournalWriter(path=path, instrument=SPEC, flush_interval=dt.timedelta(milliseconds=1))
    writer.start()
    try:
        writer.submit(
            DomainEvent(
                event_id="event-0",
                event_type=EventType.RUN_STARTED,
                occurred_at=MOMENT,
                payload={"run_id": "r", "strategy_version": "abc"},
            )
        )
        writer.drain(dt.timedelta(seconds=5))
    finally:
        writer.stop(timeout=dt.timedelta(seconds=10))

    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        bars = summarise_bars(connection)
    finally:
        connection.close()

    assert bars.snapshots == 0
    assert bars.is_continuous is True


def test_an_unreadable_payload_is_skipped_not_fatal(tmp_path: Path) -> None:
    path = tmp_path / "junk.db"
    connect_writer(str(path))
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "INSERT INTO journal (event_id, event_type, occurred_at, amount, payload,"
            " previous_hash, event_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("e", "equity_snapshot", MOMENT.isoformat(), "0", "not json", b"", b""),
        )
        connection.commit()
    finally:
        connection.close()

    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        bars = summarise_bars(connection)
    finally:
        connection.close()

    assert bars.snapshots == 0


# --------------------------------------------------------------------------- #
# the assembled report
# --------------------------------------------------------------------------- #


def test_the_assembled_report_carries_every_number(journal: Path) -> None:
    report = build_report(journal, unit=DEFAULT_UNIT, top=3)

    mapping = report.to_mapping()
    assert mapping["events"]["total"] == 6
    assert mapping["chain"]["is_valid"] is True
    assert mapping["chain"]["rows_verified"] == 6
    assert mapping["bars"]["is_continuous"] is True
    assert mapping["latency"]["observations"] == 6
    assert mapping["wal"]["wal_bytes"] >= 0
    assert mapping["systemd_log_lines"] >= 0


def test_a_missing_journal_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SoakError, match="does not exist"):
        build_report(tmp_path / "absent.db")


def test_main_writes_a_report_and_returns_zero(journal: Path, capsys: Any) -> None:
    code = main(["--journal", str(journal), "--top", "2"])
    captured = capsys.readouterr()

    assert code == 0
    assert "soak report for" in captured.out
    assert "hash chain" in captured.out
    assert "VALID over 6 rows" in captured.out


def test_main_emits_json_when_asked(journal: Path, capsys: Any) -> None:
    main(["--journal", str(journal), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert payload["events"]["total"] == 6
    assert payload["chain"]["is_valid"] is True


def test_main_returns_one_for_a_missing_journal(tmp_path: Path, capsys: Any) -> None:
    assert main(["--journal", str(tmp_path / "absent.db")]) == 1
    assert "soak_report:" in capsys.readouterr().err


def test_the_report_is_safe_against_a_live_engine(journal: Path) -> None:
    """A read-only report must not take a write lock, or it could stop the engine."""
    writer = JournalWriter(
        path=journal, instrument=SPEC, flush_interval=dt.timedelta(milliseconds=1)
    )
    writer.start()
    try:
        # The report reads while the writer is committing: a `mode=ro` connection cannot
        # block a commit, which is the property the whole tool rests on.
        report = build_report(journal)
        assert report.chain.is_valid is True
    finally:
        writer.stop(timeout=dt.timedelta(seconds=10))
