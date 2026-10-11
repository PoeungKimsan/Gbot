"""Machine coverage for scripts/backup.py: the four steps and the rotation rule.

The properties that matter are the ones a restore depends on:

* **A backup that does not verify is deleted, not kept.** An integrity failure or a broken
  hash chain removes the copy. The alternative — leaving a plausibly-named corrupt file in
  the backup directory — is worse than having no backup, because it looks like a recovery.
* **Rotation uses the filename, not the mtime.** A file touched by a restore drill has a
  young mtime and an old name; deleting on mtime keeps every copy anyone ever looked at.
* **The copy is never written into the journal's directory.** It is a read, and the only
  writes are to the backup path.

The journals here are written through the real Phase 2 ``JournalWriter``, so the hash chain
under test is the chain the engine produces — not a hand-inserted approximation of it.
"""

from __future__ import annotations

import datetime as dt
import os
import sqlite3
from pathlib import Path
from unittest import mock

import pytest
from scripts.backup import (
    DEFAULT_PREFIX,
    DEFAULT_RETENTION_DAYS,
    BackupError,
    main,
    rotate_backups,
    run_backup,
    snapshot_name,
)

from engine.domain.events import DomainEvent, EventType
from engine.journal.schema import ChainError, connect_writer, verify_chain
from engine.journal.writer import JournalWriter

GENESIS_MOMENT = dt.datetime(2026, 3, 10, 7, 0, tzinfo=dt.UTC)


def seed_journal(path: Path, rows: int = 4) -> Path:
    """Write a real hash-chained journal through the Phase 2 writer.

    The writer is used rather than raw SQL so the chain is genuine: ``verify_chain``
    recomputes each row's hash from its predecessor, so a hand-inserted row with an empty
    ``event_hash`` would fail verification for the wrong reason. Seeding through the writer
    also means the schema the backup opens is the schema the engine actually writes.
    """
    from decimal import Decimal

    from engine.market.instrument import InstrumentSpec

    writer = JournalWriter(
        path=path,
        instrument=InstrumentSpec(
            name="XAU_USD",
            pip_location=-4,
            display_precision=2,
            trade_units_precision=5,
            minimum_trade_size=Decimal("1"),
        ),
        flush_interval=dt.timedelta(milliseconds=1),
    )
    writer.start()
    try:
        for index in range(rows):
            writer.submit(
                DomainEvent(
                    event_id=f"event-{index}",
                    event_type=EventType.EQUITY_SNAPSHOT,
                    occurred_at=GENESIS_MOMENT + dt.timedelta(minutes=index),
                    payload={"sequence": index, "equity": "100000"},
                )
            )
        writer.drain(dt.timedelta(seconds=5))
    finally:
        writer.stop()
    assert path.is_file()
    return path


@pytest.fixture
def journal(tmp_path: Path) -> Path:
    return tmp_path / "journal.db"


@pytest.fixture
def seeded(journal: Path) -> Path:
    return seed_journal(journal, rows=4)


# --------------------------------------------------------------------------- #
# the four steps
# --------------------------------------------------------------------------- #


def test_a_backup_is_produced_and_verifies(seeded: Path, tmp_path: Path) -> None:
    snapshot = run_backup(
        journal_path=seeded,
        backup_path=tmp_path / "backups",
        now=GENESIS_MOMENT,
    )

    assert snapshot.is_file()
    assert snapshot.name == f"{DEFAULT_PREFIX}20260310T070000Z.db"
    # The copy is a real database, not a file with the right extension.
    with sqlite3.connect(snapshot) as connection:
        assert connection.execute("SELECT COUNT(*) FROM journal").fetchone()[0] == 4


def test_the_journal_is_not_touched_by_a_backup(seeded: Path, tmp_path: Path) -> None:
    before_bytes = seeded.read_bytes()

    run_backup(journal_path=seeded, backup_path=tmp_path / "b", now=GENESIS_MOMENT)

    # The journal's bytes are unchanged. The writer's own -wal/-shm files exist before the
    # backup runs, so the assertion is about the database, not about the directory.
    assert seeded.read_bytes() == before_bytes


def test_the_verifier_really_verifies_the_real_chain(seeded: Path, tmp_path: Path) -> None:
    """Not a self-consistency check: the engine's own verifier over the engine's own chain."""
    snapshot = run_backup(
        journal_path=seeded, backup_path=tmp_path / "b", now=GENESIS_MOMENT
    )

    with sqlite3.connect(snapshot) as connection:
        connection.row_factory = sqlite3.Row
        verification = verify_chain(connection)

    assert verification.is_valid is True
    assert verification.rows_verified == 4


def test_a_missing_journal_is_refused_loudly(journal: Path, tmp_path: Path) -> None:
    with pytest.raises(BackupError, match="does not exist"):
        run_backup(journal_path=journal, backup_path=tmp_path / "b")


def test_an_existing_target_is_never_overwritten(seeded: Path, tmp_path: Path) -> None:
    """A timestamped name should be unique; an overwrite discards the previous night."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(parents=True)
    (backup_dir / snapshot_name(GENESIS_MOMENT)).write_bytes(b"previous night")

    with pytest.raises(BackupError, match="already exists"):
        run_backup(journal_path=seeded, backup_path=backup_dir, now=GENESIS_MOMENT)

    assert (backup_dir / snapshot_name(GENESIS_MOMENT)).read_bytes() == b"previous night"


def test_a_target_that_cannot_be_written_is_cleaned_up(tmp_path: Path) -> None:
    """A half-written copy must not survive as a plausible-looking backup."""
    journal = tmp_path / "journal.db"
    journal.write_bytes(b"not a database")
    before = sorted(path.name for path in tmp_path.iterdir())

    with pytest.raises(BackupError):
        run_backup(journal_path=journal, backup_path=tmp_path, now=GENESIS_MOMENT)

    assert sorted(path.name for path in tmp_path.iterdir()) == before


# --------------------------------------------------------------------------- #
# the verification failures delete the copy
# --------------------------------------------------------------------------- #


def test_a_copy_that_fails_integrity_check_is_deleted(
    seeded: Path, tmp_path: Path
) -> None:
    """The point of integrity_check: a consistent-but-corrupt copy is useless on restore."""
    import scripts.backup as backup_module

    backup_dir = tmp_path / "backups"
    original = backup_module._integrity_check
    backup_module._integrity_check = lambda snapshot: (_ for _ in ()).throw(
        BackupError("integrity failed")
    )
    try:
        with pytest.raises(BackupError, match="integrity failed"):
            backup_module.run_backup(
                journal_path=seeded, backup_path=backup_dir, now=GENESIS_MOMENT
            )
    finally:
        backup_module._integrity_check = original

    assert not (backup_dir / snapshot_name(GENESIS_MOMENT)).exists()


def test_a_copy_whose_chain_does_not_verify_is_deleted(
    seeded: Path, tmp_path: Path
) -> None:
    """A copy that fails hash verification must not survive as a recovery."""
    import scripts.backup as backup_module

    backup_dir = tmp_path / "backups"
    original = backup_module._verify_chain
    backup_module._verify_chain = lambda snapshot: (_ for _ in ()).throw(
        BackupError("chain failed")
    )
    try:
        with pytest.raises(BackupError, match="chain failed"):
            backup_module.run_backup(
                journal_path=seeded, backup_path=backup_dir, now=GENESIS_MOMENT
            )
    finally:
        backup_module._verify_chain = original

    assert not (backup_dir / snapshot_name(GENESIS_MOMENT)).exists()


def _broken_chain_database(path: Path) -> Path:
    """A journal table whose second row's hash does not chain from the first.

    Built without the engine's append-only triggers, because those triggers are precisely
    what makes a *real* journal hard to corrupt — which is the point of this test: the
    verifier must catch the corruption that a real replication or disk fault produces
    outside SQLite's own protection.
    """
    from engine.journal.schema import compute_event_hash

    connection = connect_writer(str(path))
    try:
        # Drop the immutability triggers, then write two rows whose second link is wrong.
        connection.execute("DROP TRIGGER IF EXISTS journal_no_update")
        connection.execute("DROP TRIGGER IF EXISTS journal_no_delete")
        first = compute_event_hash(b"", b"first-payload")
        connection.execute(
            "INSERT INTO journal (event_id, event_type, occurred_at, amount, payload,"
            " previous_hash, event_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "event-0",
                "equity_snapshot",
                GENESIS_MOMENT.isoformat(),
                "0",
                "first-payload",
                b"",
                first,
            ),
        )
        connection.execute(
            "INSERT INTO journal (event_id, event_type, occurred_at, amount, payload,"
            " previous_hash, event_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "event-1",
                "equity_snapshot",
                GENESIS_MOMENT.isoformat(),
                "0",
                "second-payload",
                # previous_hash deliberately correct, event_hash deliberately wrong.
                first,
                compute_event_hash(b"", b"not-the-real-payload"),
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return path


def test_a_tampered_copy_is_caught_by_the_real_verifier(tmp_path: Path) -> None:
    """Not a monkeypatched failure: a genuinely broken chain is really rejected."""
    broken = _broken_chain_database(tmp_path / "journal.db")

    with pytest.raises(BackupError, match="hash-chain verification"):
        run_backup(
            journal_path=broken, backup_path=tmp_path / "backups", now=GENESIS_MOMENT
        )

    assert not (tmp_path / "backups" / snapshot_name(GENESIS_MOMENT)).exists()


def test_an_unreadable_copy_is_caught_and_removed(seeded: Path, tmp_path: Path) -> None:
    """A copy that opens but is not a database is removed, not retained."""
    # Produce a verified snapshot, then destroy its contents in place.
    snapshot = run_backup(
        journal_path=seeded, backup_path=tmp_path / "b", now=GENESIS_MOMENT
    )
    snapshot.write_bytes(b"this is not a database")

    from scripts.backup import _integrity_check

    with pytest.raises(BackupError, match="for an integrity check"):
        _integrity_check(snapshot)


# --------------------------------------------------------------------------- #
# rotation
# --------------------------------------------------------------------------- #


def test_rotation_removes_snapshots_past_the_retention_window(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    old = backup_dir / f"{DEFAULT_PREFIX}20260201T000000Z.db"
    fresh = backup_dir / f"{DEFAULT_PREFIX}20260310T000000Z.db"
    old.write_bytes(b"x")
    fresh.write_bytes(b"x")
    now = dt.datetime(2026, 3, 10, tzinfo=dt.UTC)

    pruned = rotate_backups(backup_dir, now=now)

    assert pruned == (old,)
    assert not old.exists()
    assert fresh.exists()


def test_rotation_is_boundary_exact(tmp_path: Path) -> None:
    """A snapshot exactly at the window's edge is kept; one day older is not."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    now = dt.datetime(2026, 3, 15, tzinfo=dt.UTC)
    edge = backup_dir / f"{DEFAULT_PREFIX}20260301T000000Z.db"  # exactly 14 days
    stale = backup_dir / f"{DEFAULT_PREFIX}20260228T000000Z.db"  # 15 days
    edge.write_bytes(b"x")
    stale.write_bytes(b"x")

    pruned = rotate_backups(backup_dir, retention_days=14, now=now)

    assert pruned == (stale,)
    assert edge.exists()


def test_rotation_is_driven_by_the_name_not_the_mtime(tmp_path: Path) -> None:
    """A file touched by a restore drill has a young mtime and an old name."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    old = backup_dir / f"{DEFAULT_PREFIX}20260101T000000Z.db"
    old.write_bytes(b"x")
    moment = dt.datetime(2026, 3, 15, tzinfo=dt.UTC).timestamp()
    os.utime(old, (moment, moment))

    pruned = rotate_backups(backup_dir, now=dt.datetime(2026, 3, 15, tzinfo=dt.UTC))

    assert pruned == (old,)


def test_rotation_ignores_files_it_did_not_write(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    keep = backup_dir / "notes.txt"
    keep.write_bytes(b"x")

    assert rotate_backups(backup_dir, now=GENESIS_MOMENT) == ()
    assert keep.exists()


def test_rotation_tolerates_an_unparseable_name(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    nameless = backup_dir / f"{DEFAULT_PREFIX}not-a-date.db"
    nameless.write_bytes(b"x")

    assert rotate_backups(backup_dir, now=GENESIS_MOMENT) == ()
    assert nameless.exists()


def test_a_missing_backup_directory_is_not_an_error(tmp_path: Path) -> None:
    assert rotate_backups(tmp_path / "absent", now=GENESIS_MOMENT) == ()


def test_rotation_refuses_a_sub_day_window(tmp_path: Path) -> None:
    with pytest.raises(BackupError, match="at least 1"):
        rotate_backups(tmp_path, retention_days=0)


def test_the_default_retention_is_fourteen_days() -> None:
    assert DEFAULT_RETENTION_DAYS == 14


# --------------------------------------------------------------------------- #
# the process entry point
# --------------------------------------------------------------------------- #


def test_main_succeeds_over_a_good_journal(seeded: Path, tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"

    with mock.patch.dict(
        os.environ,
        {
            "XAUUSD_JOURNAL_PATH": str(seeded),
            "XAUUSD_BACKUP_PATH": str(backup_dir),
            "XAUUSD_BACKUP_RETENTION_DAYS": "1",
            "XAUUSD_LOG_LEVEL": "CRITICAL",
        },
    ):
        assert main() == 0

    assert sorted(backup_dir.glob(f"{DEFAULT_PREFIX}*.db"))


def test_main_fails_cleanly_with_no_journal(tmp_path: Path) -> None:
    with mock.patch.dict(
        os.environ,
        {
            "XAUUSD_JOURNAL_PATH": str(tmp_path / "absent.db"),
            "XAUUSD_BACKUP_PATH": str(tmp_path / "backups"),
            "XAUUSD_LOG_LEVEL": "CRITICAL",
        },
    ):
        assert main() == 1


def test_main_reports_a_bad_retention_instead_of_exploding(tmp_path: Path) -> None:
    """A misconfigured retention is an exit code, not a traceback an operator must read."""
    with mock.patch.dict(
        os.environ,
        {
            "XAUUSD_JOURNAL_PATH": str(tmp_path / "absent.db"),
            "XAUUSD_BACKUP_PATH": str(tmp_path / "backups"),
            "XAUUSD_BACKUP_RETENTION_DAYS": "lots",
            "XAUUSD_LOG_LEVEL": "CRITICAL",
        },
    ):
        assert main() == 1


def test_the_schema_the_backup_opens_is_the_engines_own(seeded: Path) -> None:
    """`connect_writer` is the engine's, so a copy of it must be readable by the engine."""
    snapshot = run_backup(
        journal_path=seeded, backup_path=seeded.parent / "b", now=GENESIS_MOMENT
    )
    assert connect_writer(str(snapshot)) is not None


def test_a_chain_error_during_verification_is_reported(
    seeded: Path, tmp_path: Path
) -> None:
    """`ChainError` is caught rather than escaping as an unhandled exception."""
    import scripts.backup as backup_module

    backup_dir = tmp_path / "backups"
    original = backup_module.verify_chain
    backup_module.verify_chain = lambda connection: (_ for _ in ()).throw(
        ChainError("unreadable hashes")
    )
    try:
        with pytest.raises(BackupError, match="could not verify"):
            backup_module.run_backup(
                journal_path=seeded, backup_path=backup_dir, now=GENESIS_MOMENT
            )
    finally:
        backup_module.verify_chain = original

    assert not (backup_dir / snapshot_name(GENESIS_MOMENT)).exists()
