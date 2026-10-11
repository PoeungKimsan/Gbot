"""Nightly journal backup: copy, check, verify the chain, rotate.

Four steps, in an order that matters:

1. **`VACUUM INTO`** copies the journal to a new file. It is the only SQLite operation that
   yields a consistent snapshot of a database that is being written concurrently — it takes
   a read transaction internally and cannot observe a half-committed row, which a naive file
   copy of a WAL database absolutely can.
2. **`PRAGMA integrity_check`** on the copy, because a copy that is consistent but corrupt is
   a copy that fails on restore day, which is the one day it matters.
3. **`verify_chain`** on the copy. This is the same function Phase 2's tests use to detect
   tampering; running it over the backup is what makes a backup *restorable* rather than
   merely present. A copy whose chain does not verify is deleted rather than retained.
4. **Rotation**, because a directory of unbounded nightly copies is a disk-full waiting for
   the night it is needed.

Nothing here writes to the journal. The copy is opened read-only; the only writes are to
the backup directory.

Usage:
    python scripts/backup.py

Environment:
    XAUUSD_JOURNAL_PATH     Journal to copy. Default /var/lib/xauusd/journal/journal.db.
    XAUUSD_BACKUP_PATH      Where copies go. Default /var/lib/xauusd/backups.
    XAUUSD_BACKUP_RETENTION_DAYS  Delete copies older than this. Default 14.
    XAUUSD_BACKUP_PREFIX    Copy filename prefix. Default ``journal-``.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import sqlite3
from pathlib import Path
from typing import Final

from engine.journal.schema import ChainError, verify_chain

__all__ = [
    "BACKUP_PATH_ENV",
    "DEFAULT_BACKUP_PATH",
    "DEFAULT_JOURNAL_PATH",
    "DEFAULT_PREFIX",
    "DEFAULT_RETENTION_DAYS",
    "JOURNAL_PATH_ENV",
    "PREFIX_ENV",
    "RETENTION_DAYS_ENV",
    "BackupError",
    "main",
    "rotate_backups",
    "run_backup",
]

logger = logging.getLogger("xauusd-backup")

DEFAULT_JOURNAL_PATH: Final[Path] = Path("/var/lib/xauusd/journal/journal.db")
DEFAULT_BACKUP_PATH: Final[Path] = Path("/var/lib/xauusd/backups")
DEFAULT_PREFIX: Final[str] = "journal-"
DEFAULT_RETENTION_DAYS: Final[int] = 14

JOURNAL_PATH_ENV: Final[str] = "XAUUSD_JOURNAL_PATH"
BACKUP_PATH_ENV: Final[str] = "XAUUSD_BACKUP_PATH"
RETENTION_DAYS_ENV: Final[str] = "XAUUSD_BACKUP_RETENTION_DAYS"
PREFIX_ENV: Final[str] = "XAUUSD_BACKUP_PREFIX"

#: Integrity check results that mean "fine". SQLite returns one row per problem, so a clean
#: database yields exactly ``("ok",)``.
_INTEGRITY_OK: Final[str] = "ok"


class BackupError(RuntimeError):
    """A backup could not be produced, or the one produced did not verify."""


def _read_journal_path() -> Path:
    return Path(os.environ.get(JOURNAL_PATH_ENV) or DEFAULT_JOURNAL_PATH)


def _read_backup_path() -> Path:
    return Path(os.environ.get(BACKUP_PATH_ENV) or DEFAULT_BACKUP_PATH)


def _read_prefix() -> str:
    return os.environ.get(PREFIX_ENV) or DEFAULT_PREFIX


def _read_retention_days() -> int:
    raw = os.environ.get(RETENTION_DAYS_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_RETENTION_DAYS
    try:
        days = int(raw.strip())
    except ValueError as exc:
        msg = f"{RETENTION_DAYS_ENV}={raw!r} is not an integer number of days"
        raise BackupError(msg) from exc
    if days < 1:
        msg = f"{RETENTION_DAYS_ENV}={days} must be at least 1"
        raise BackupError(msg)
    return days


def _copy(source: Path, target: Path) -> None:
    """Copy ``source`` to ``target`` with ``VACUUM INTO``, refusing to overwrite.

    ``VACUUM INTO`` fails if the target exists, which is the behaviour wanted here: a
    timestamped name should be unique by construction, and an overwrite would silently
    discard the previous night's snapshot.

    Raises:
        BackupError: if the copy fails for any reason, including a target that already
            exists.
    """
    if target.exists():
        msg = f"backup target {target} already exists; refusing to overwrite"
        raise BackupError(msg)
    # The parent directory is created by the caller; VACUUM INTO does not create it.
    target.parent.mkdir(parents=True, exist_ok=True)
    connection: sqlite3.Connection | None = None
    try:
        # mode=ro: the copy is a *read* of the journal, and a write lock here would be a
        # backup that can stop the trader.
        connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        # Quoting the path: a backup directory with a space or a comma in it would
        # otherwise parse as a SQL syntax error.
        escaped = str(target).replace("'", "''")
        connection.execute(f"VACUUM INTO '{escaped}'")
    except sqlite3.Error as exc:
        # A half-written copy must not survive as a plausible-looking backup.
        target.unlink(missing_ok=True)
        msg = f"VACUUM INTO {target} failed: {exc}"
        raise BackupError(msg) from exc
    finally:
        if connection is not None:
            connection.close()
    if not target.is_file():
        target.unlink(missing_ok=True)
        msg = f"VACUUM INTO reported success but {target} does not exist"
        raise BackupError(msg)


def _integrity_check(snapshot: Path) -> str:
    """Run ``PRAGMA integrity_check`` on the copy and return its verdict.

    Raises:
        BackupError: if the check reports anything other than ``ok``, or the copy cannot
            be opened. A copy that fails integrity is worse than none, because it looks like
            a recovery — so :func:`run_backup` deletes it. Deletion is deliberately *not*
            done here, so a failure arriving by any other route still cleans up.
    """
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)
        rows = connection.execute("PRAGMA integrity_check").fetchall()
    except sqlite3.Error as exc:
        msg = f"could not open {snapshot} for an integrity check: {exc}"
        raise BackupError(msg) from exc
    finally:
        if connection is not None:
            connection.close()

    problems = tuple(str(row[0]) for row in rows)
    if problems == (_INTEGRITY_OK,):
        return _INTEGRITY_OK
    message = "; ".join(problems)
    msg = f"{snapshot} failed integrity_check: {message}"
    raise BackupError(msg)


def _verify_chain(snapshot: Path) -> int:
    """Verify the row-level hash chain over the copy and return the row count.

    Raises:
        BackupError: if the chain does not verify, or the copy cannot be read. As with
            :func:`_integrity_check`, deleting the copy is :func:`run_backup`'s job.
    """
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        verification = verify_chain(connection)
    except (sqlite3.Error, ChainError) as exc:
        msg = f"could not verify the hash chain of {snapshot}: {exc}"
        raise BackupError(msg) from exc
    finally:
        if connection is not None:
            connection.close()

    if not verification.is_valid:
        msg = f"{snapshot} failed hash-chain verification: {verification.reason}"
        raise BackupError(msg)
    return verification.rows_verified


def rotate_backups(
    directory: Path,
    *,
    prefix: str = DEFAULT_PREFIX,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    now: dt.datetime | None = None,
) -> tuple[Path, ...]:
    """Delete snapshots older than ``retention_days`` and return what was removed.

    Age is taken from the filename, not the mtime: a file touched by a restore drill has a
    young mtime and an old name, and deleting on mtime would keep every copy an operator
    had ever looked at.

    Raises:
        BackupError: if the retention window is not at least one day.
    """
    if not isinstance(directory, Path):
        msg = f"directory must be a Path, got {type(directory).__name__}"
        raise BackupError(msg)
    if not isinstance(prefix, str) or not prefix:
        msg = f"prefix must be a non-empty string, got {prefix!r}"
        raise BackupError(msg)
    eligible = isinstance(retention_days, int) and not isinstance(retention_days, bool)
    if not eligible or retention_days < 1:
        msg = f"retention_days must be an int of at least 1, got {retention_days!r}"
        raise BackupError(msg)

    if not directory.is_dir():
        return ()

    moment = now or dt.datetime.now(dt.UTC)
    # Timestamps are written in UTC, so the cutoff is computed in UTC too.
    cutoff = moment - dt.timedelta(days=retention_days)
    pruned: list[Path] = []

    for candidate in sorted(directory.glob(f"{prefix}*.db"))[::]:
        if not candidate.is_file():
            continue
        stamp = _stamp_of(candidate.name, prefix)
        if stamp is None:
            logger.warning("[backup] skipping unparseable snapshot name %s", candidate.name)
            continue
        if stamp < cutoff:
            candidate.unlink(missing_ok=True)
            pruned.append(candidate)
            logger.info("[backup] pruned %s", candidate.name)

    return tuple(pruned)


def _stamp_of(name: str, prefix: str) -> dt.datetime | None:
    """The UTC instant encoded in a snapshot's filename, or ``None`` if it has none.

    The name is ``{prefix}YYYYMMDDTHHMMSSZ.db``, so a file whose name does not parse is a
    file this module did not write — left alone rather than guessed at.
    """
    if not name.startswith(prefix) or not name.endswith(".db"):
        return None
    stem = name[len(prefix) : -len(".db")]
    try:
        parsed = dt.datetime.strptime(stem, "%Y%m%dT%H%M%SZ")
    except ValueError:
        return None
    return parsed.replace(tzinfo=dt.UTC)


def snapshot_name(now: dt.datetime | None = None) -> str:
    """The filename for a snapshot taken at ``now``.

    A one-second-resolution UTC stamp, which is both human-readable and sortable: ``ls`` of
    the backup directory is chronological, which is what a restore operator wants.
    """
    moment = now or dt.datetime.now(dt.UTC)
    return f"{DEFAULT_PREFIX}{moment.strftime('%Y%m%dT%H%M%SZ')}.db"


def run_backup(
    *,
    journal_path: Path | None = None,
    backup_path: Path | None = None,
    retention_days: int | None = None,
    now: dt.datetime | None = None,
) -> Path:
    """Produce one verified backup and rotate the directory. Returns the snapshot's path.

    Raises:
        BackupError: if the journal is missing, the copy fails, the integrity check fails,
            or the hash chain does not verify.
    """
    journal = journal_path or _read_journal_path()
    directory = backup_path or _read_backup_path()
    retention = retention_days if retention_days is not None else _read_retention_days()

    if not journal.is_file():
        msg = f"journal {journal} does not exist; nothing to back up"
        raise BackupError(msg)

    directory.mkdir(parents=True, exist_ok=True)
    snapshot = directory / snapshot_name(now)

    logger.info("[backup] copying %s -> %s", journal, snapshot)
    _copy(journal, snapshot)

    try:
        integrity = _integrity_check(snapshot)
        rows = _verify_chain(snapshot)
    except BaseException:
        # The copy exists but is not a verified backup, so it is worse than nothing: a
        # plausibly-named corrupt file in the backup directory is a recovery that fails on
        # the one night it is needed. This lives in the orchestrator rather than in either
        # validator, so a failure by *any* route — a raised BackupError, a ChainError, an
        # unexpected Error — still removes it.
        snapshot.unlink(missing_ok=True)
        raise

    logger.info("[backup] %s verified: integrity=%s chain_rows=%d", snapshot.name, integrity, rows)

    pruned = rotate_backups(
        directory,
        prefix=DEFAULT_PREFIX,
        retention_days=retention,
        now=now,
    )
    logger.info("[backup] pruned %d snapshot(s) older than %d days", len(pruned), retention)
    return snapshot


def main() -> int:
    """Run one backup. Returns ``0`` on success, ``1`` on any failure."""
    logging.basicConfig(
        level=os.environ.get("XAUUSD_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        run_backup()
    except BackupError as exc:
        logger.error("[backup] failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
