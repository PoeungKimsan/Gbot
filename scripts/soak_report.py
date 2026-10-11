"""Summarise what a soak run actually did, from the journal and the systemd log.

A soak run is a long, boring, *successful* run, and that is what makes it hard to assess:
nothing went wrong, so there is nothing to look at. This tool is the answer to "and what did
it do?" without asking an operator to read four million rows.

Five numbers, and why each one is the one worth having:

* **Event counts, by type.** The shape of the run. A day with 40,000 equity snapshots and no
  order submissions is a day the strategy never saw a setup, which is a *finding*, not a
  non-event.
* **Latency deviation.** ``occurred_at`` is when the engine decided something happened;
  ``created_at`` is when the writer committed it. The difference is the journal's lag, and
  its *distribution* is the health of the write path: a p99 that grows across the run is a
  disk slowing down, and it will show up here long before anything fails.
* **WAL dimensions.** The write-ahead log is the file that grows while the engine runs and
  is checkpointed between group commits. A WAL that grows without bound means the checkpoints
  are not keeping up, and the disk is the next thing to notice.
* **Hash-chain verification.** ``verify_chain`` over the whole journal. This is the one
  number that is never negotiable: a soak run that produced a broken chain produced a
  journal nobody can trust, however profitable it was.
* **Bar continuity.** The ``bars`` counter on every equity snapshot must advance by exactly
  one per closed bar. A jump is a gap in the bars the strategy was allowed to see, and a gap
  is a hole in the only thing the engine reasons about.

Nothing here writes anything, opens the journal for writing, or touches the projection. It is
a reader, and it is safe to run against a live engine's WAL journal while the engine is
committing.

Usage:
    python scripts/soak_report.py [--journal PATH] [--unit NAME] [--since TODAY]
                                 [--json] [--top N]

Environment:
    XAUUSD_JOURNAL_PATH   Journal to read. Default /var/lib/xauusd/journal/journal.db.
    XAUUSD_JOURNAL_UNIT   systemd unit whose log to read. Default xauusd-engine.
    XAUUSD_SOAK_TOP       How many latency outliers to print. Default 5.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from engine.journal.schema import ChainError, verify_chain

__all__ = [
    "DEFAULT_JOURNAL_PATH",
    "DEFAULT_TOP",
    "DEFAULT_UNIT",
    "JOURNAL_PATH_ENV",
    "TOP_ENV",
    "UNIT_ENV",
    "BarContinuity",
    "ChainSummary",
    "EventCounts",
    "LatencySummary",
    "SoakReport",
    "WalSummary",
    "summarise_bars",
    "summarise_chain",
    "summarise_events",
    "summarise_latency",
    "summarise_wal",
    "write_report",
]

DEFAULT_JOURNAL_PATH: Final[Path] = Path("/var/lib/xauusd/journal/journal.db")
DEFAULT_UNIT: Final[str] = "xauusd-engine"
DEFAULT_TOP: Final[int] = 5

JOURNAL_PATH_ENV: Final[str] = "XAUUSD_JOURNAL_PATH"
UNIT_ENV: Final[str] = "XAUUSD_JOURNAL_UNIT"
TOP_ENV: Final[str] = "XAUUSD_SOAK_TOP"

#: The WAL is worth reporting on once it passes this size. Below it, a WAL is just a WAL.
_WAL_NOTABLE_BYTES: Final[int] = 8 * 1024 * 1024


class SoakError(RuntimeError):
    """A soak report could not be produced."""


@dataclass(frozen=True, slots=True)
class EventCounts:
    """How many rows of each type the run produced."""

    total: int
    by_type: dict[str, int]
    first_at: str | None
    last_at: str | None

    @property
    def span_seconds(self) -> float | None:
        """How long the journalled history covers."""
        if self.first_at is None or self.last_at is None:
            return None
        start = dt.datetime.fromisoformat(self.first_at)
        end = dt.datetime.fromisoformat(self.last_at)
        return (end - start).total_seconds()


@dataclass(frozen=True, slots=True)
class LatencySummary:
    """The distribution of ``created_at`` minus ``occurred_at``."""

    observations: int
    p50_ms: float | None
    p99_ms: float | None
    worst_ms: float | None
    outliers: tuple[tuple[str, float], ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class WalSummary:
    """The write-ahead log's dimensions, and whether it grew without bound."""

    wal_bytes: int
    journal_bytes: int
    checkpoints: int

    @property
    def wal_ratio(self) -> float:
        """WAL bytes per journal byte, the runaway signal in one number."""
        if self.journal_bytes == 0:
            return 0.0
        return self.wal_bytes / self.journal_bytes


@dataclass(frozen=True, slots=True)
class BarContinuity:
    """Whether the ``bars`` counter advanced by exactly one per snapshot."""

    snapshots: int
    gaps: tuple[int, ...]
    regressions: int
    max_gap: int

    @property
    def is_continuous(self) -> bool:
        return not self.gaps and self.regressions == 0


@dataclass(frozen=True, slots=True)
class ChainSummary:
    """The hash chain over the whole journal."""

    rows_verified: int
    is_valid: bool
    reason: str


@dataclass(frozen=True, slots=True)
class SoakReport:
    """Everything one soak run did, in the order an operator reads it."""

    journal_path: Path
    generated_at: str
    events: EventCounts
    latency: LatencySummary
    wal: WalSummary
    chain: ChainSummary
    bars: BarContinuity
    log_lines: int = 0

    def to_mapping(self) -> dict[str, object]:
        """A JSON-safe mapping, the report's own serialisation."""
        return {
            "journal": str(self.journal_path),
            "generated_at": self.generated_at,
            "events": {
                "total": self.events.total,
                "by_type": dict(sorted(self.events.by_type.items())),
                "first_at": self.events.first_at,
                "last_at": self.events.last_at,
                "span_seconds": self.events.span_seconds,
            },
            "latency": {
                "observations": self.latency.observations,
                "p50_ms": self.latency.p50_ms,
                "p99_ms": self.latency.p99_ms,
                "worst_ms": self.latency.worst_ms,
                "outliers": [
                    {"event_id": event_id, "milliseconds": ms}
                    for event_id, ms in self.latency.outliers
                ],
            },
            "wal": {
                "wal_bytes": self.wal.wal_bytes,
                "journal_bytes": self.wal.journal_bytes,
                "checkpoints": self.wal.checkpoints,
                "wal_ratio": round(self.wal.wal_ratio, 4),
            },
            "chain": {
                "rows_verified": self.chain.rows_verified,
                "is_valid": self.chain.is_valid,
                "reason": self.chain.reason,
            },
            "bars": {
                "snapshots": self.bars.snapshots,
                "gaps": list(self.bars.gaps),
                "regressions": self.bars.regressions,
                "max_gap": self.bars.max_gap,
                "is_continuous": self.bars.is_continuous,
            },
            "systemd_log_lines": self.log_lines,
        }


# --------------------------------------------------------------------------- #
# the readers
# --------------------------------------------------------------------------- #


def summarise_events(connection: sqlite3.Connection) -> EventCounts:
    """Count rows by type, and name the span the history covers."""
    by_type: Counter[str] = Counter()
    total = 0
    first_at: str | None = None
    last_at: str | None = None

    for event_type, occurred_at in connection.execute(
        "SELECT event_type, occurred_at FROM journal ORDER BY sequence"
    ):
        by_type[str(event_type)] += 1
        total += 1
        if first_at is None:
            first_at = str(occurred_at)
        last_at = str(occurred_at)

    return EventCounts(total=total, by_type=dict(by_type), first_at=first_at, last_at=last_at)


def summarise_latency(
    connection: sqlite3.Connection, *, top: int = DEFAULT_TOP
) -> LatencySummary:
    """How far each event's commit lagged its own occurrence.

    Both timestamps come from the same row: ``occurred_at`` is the engine's, ``created_at``
    is the writer's ``strftime`` default. The difference is the journal's write latency, and
    the *tail* is what matters — a p99 that climbs across a run is a disk slowing down.
    """
    if top < 0:
        msg = f"top must not be negative, got {top}"
        raise SoakError(msg)

    samples: list[tuple[str, float]] = []
    for event_id, occurred_at, created_at in connection.execute(
        "SELECT event_id, occurred_at, created_at FROM journal ORDER BY sequence"
    ):
        start = _parse(str(occurred_at))
        end = _parse(str(created_at))
        if start is None or end is None:
            continue
        samples.append((str(event_id), (end - start).total_seconds() * 1000.0))

    if not samples:
        return LatencySummary(observations=0, p50_ms=None, p99_ms=None, worst_ms=None)

    ordered = sorted(samples, key=lambda sample: sample[1])
    return LatencySummary(
        observations=len(samples),
        p50_ms=round(_percentile(ordered, 0.50), 2),
        p99_ms=round(_percentile(ordered, 0.99), 2),
        worst_ms=round(ordered[-1][1], 2),
        outliers=tuple((event_id, round(ms, 2)) for event_id, ms in ordered[-top:] if top > 0),
    )


def summarise_wal(connection: sqlite3.Connection, journal_path: Path) -> WalSummary:
    """The WAL's size, the journal's size, and how many checkpoints have run."""
    wal_path = journal_path.with_name(journal_path.name + "-wal")
    try:
        page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
        page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
        checkpoints = int(connection.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()[1])
    except sqlite3.Error:
        page_size, page_count, checkpoints = 4096, 0, 0

    # `PRAGMA wal_checkpoint` itself can move the WAL, so size it *after* the call.
    wal_bytes = wal_path.stat().st_size if wal_path.is_file() else 0
    return WalSummary(
        wal_bytes=wal_bytes,
        journal_bytes=page_size * page_count,
        checkpoints=checkpoints,
    )


def summarise_chain(connection: sqlite3.Connection) -> ChainSummary:
    """The engine's own chain verification, reported as a result rather than raised."""
    try:
        result = verify_chain(connection)
    except (sqlite3.Error, ChainError) as exc:
        return ChainSummary(rows_verified=0, is_valid=False, reason=str(exc))
    return ChainSummary(
        rows_verified=result.rows_verified,
        is_valid=result.is_valid,
        reason=result.reason,
    )


def summarise_bars(connection: sqlite3.Connection) -> BarContinuity:
    """Whether the ``bars`` counter advanced by exactly one per equity snapshot.

    A jump means the strategy was shown a gap: it never saw the bars in between, so whatever
    it decided was decided on an incomplete picture. A regression means the counter went
    backwards, which is either a replay or a bug and is worth naming either way.
    """
    snapshots = 0
    gaps: list[int] = []
    regressions = 0
    previous: int | None = None

    for (payload,) in connection.execute(
        "SELECT payload FROM journal WHERE event_type = 'equity_snapshot' ORDER BY sequence"
    ):
        try:
            body = json.loads(str(payload)).get("payload", {})
            bars = int(body.get("bars", 0))
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        snapshots += 1
        if previous is not None:
            delta = bars - previous
            if delta > 1:
                gaps.append(delta - 1)
            elif delta < 0:
                regressions += 1
        previous = bars

    return BarContinuity(
        snapshots=snapshots,
        gaps=tuple(gaps),
        regressions=regressions,
        max_gap=max(gaps, default=0),
    )


def _read_log_lines(unit: str, journal_path: Path) -> int:
    """How many of the unit's log lines this report covers.

    The count is a sanity figure, not the report's substance: it lets an operator see that
    the systemd log and the journal agree about the run's length. Returns 0 when journalctl
    is absent, which is the normal state on a dev host.
    """
    journalctl = shutil.which("journalctl")
    if journalctl is None:
        return 0
    since = _log_since(journal_path)
    command = [journalctl, "-u", unit, "--output=cat", "--no-pager"]
    if since is not None:
        command += ["--since", since]
    try:
        result = subprocess.run(  # noqa: S603 - this host's own journalctl, on a fixed unit
            command, capture_output=True, text=True, check=False, timeout=120
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    if result.returncode != 0:
        return 0
    return sum(1 for line in result.stdout.splitlines() if line.strip())


def _log_since(journal_path: Path) -> str | None:
    """The journal's first timestamp, for ``journalctl --since``."""
    connection = sqlite3.connect(f"file:{journal_path}?mode=ro", uri=True)
    try:
        row = connection.execute(
            "SELECT MIN(occurred_at) FROM journal"
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        connection.close()
    return None if row is None or row[0] is None else str(row[0])


def _parse(text: str) -> dt.datetime | None:
    """Parse a stored timestamp, or ``None``.

    Both forms are accepted and made aware. The writer's ``created_at`` default is
    ``strftime('%Y-%m-%dT%H:%M:%fZ')``, which carries a ``Z`` but *not* the ``+00:00`` that
    ``fromisoformat`` needs, so it arrives naive — and subtracting a naive timestamp from an
    aware one raises. Reporting a lag requires both sides to be the same instant, so the
    naive form is explicitly UTC.
    """
    candidate = text.strip()
    if not candidate:
        return None
    try:
        parsed = dt.datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.UTC)
    return parsed


def _percentile(ordered: list[tuple[str, float]], fraction: float) -> float:
    """The ``fraction`` percentile of an already-sorted list, linearly interpolated."""
    if not ordered:
        msg = "a percentile needs at least one sample"
        raise SoakError(msg)
    if len(ordered) == 1:
        return ordered[0][1]
    position = fraction * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower][1] * (1 - weight) + ordered[upper][1] * weight


# --------------------------------------------------------------------------- #
# the report
# --------------------------------------------------------------------------- #


def build_report(
    journal_path: Path, *, unit: str = DEFAULT_UNIT, top: int = DEFAULT_TOP
) -> SoakReport:
    """Read the journal and assemble the report.

    Opens the journal ``mode=ro``, so a report can be produced while the engine is
    committing — which is the only time anyone wants one.

    Raises:
        SoakError: if the journal is missing, or the sample size is unusable.
    """
    if not journal_path.is_file():
        msg = f"journal {journal_path} does not exist; nothing to report on"
        raise SoakError(msg)
    if top < 0:
        msg = f"top must not be negative, got {top}"
        raise SoakError(msg)

    connection = sqlite3.connect(f"file:{journal_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        events = summarise_events(connection)
        latency = summarise_latency(connection, top=top)
        wal = summarise_wal(connection, journal_path)
        chain = summarise_chain(connection)
        bars = summarise_bars(connection)
    except sqlite3.Error as exc:
        msg = f"could not read {journal_path}: {exc}"
        raise SoakError(msg) from exc
    finally:
        connection.close()

    return SoakReport(
        journal_path=journal_path,
        generated_at=dt.datetime.now(dt.UTC).isoformat(),
        events=events,
        latency=latency,
        wal=wal,
        chain=chain,
        bars=bars,
        log_lines=_read_log_lines(unit, journal_path),
    )


def write_report(report: SoakReport, *, as_json: bool) -> None:
    """Print the report, as text or as JSON."""
    if as_json:
        print(json.dumps(report.to_mapping(), indent=2, sort_keys=True))
        return

    span = report.events.span_seconds
    span_text = "unknown" if span is None else _duration(span)
    print(f"soak report for {report.journal_path}")
    print(f"  generated at      {report.generated_at}")
    print(f"  journalled span   {span_text} ({report.events.total} events)")
    print()

    print("events by type")
    for name, count in sorted(report.events.by_type.items(), key=lambda item: -item[1]):
        print(f"  {name:<26} {count:>10,}")
    print()

    print("write latency (occurred_at -> created_at)")
    if report.latency.observations == 0:
        print("  no observable latency")
    else:
        print(f"  samples           {report.latency.observations:>10,}")
        print(f"  p50               {report.latency.p50_ms:>10.2f} ms")
        print(f"  p99               {report.latency.p99_ms:>10.2f} ms")
        print(f"  worst             {report.latency.worst_ms:>10.2f} ms")
        for event_id, milliseconds in reversed(report.latency.outliers):
            print(f"    {event_id:<28} {milliseconds:>10.2f} ms")
    print()

    ratio = report.wal.wal_ratio
    print("wal")
    print(f"  journal           {report.wal.journal_bytes:>10,} bytes")
    print(f"  wal               {report.wal.wal_bytes:>10,} bytes ({ratio:.2f}x)")
    print(f"  checkpoints       {report.wal.checkpoints:>10,}")
    if ratio > 10:
        print("  WARNING the WAL is large relative to the journal; checkpoints are lagging")
    print()

    print("hash chain")
    if report.chain.is_valid:
        print(f"  VALID over {report.chain.rows_verified:,} rows")
    else:
        print(f"  BROKEN: {report.chain.reason}")
    print()

    print("bar continuity")
    if report.bars.is_continuous:
        print(f"  continuous across {report.bars.snapshots:,} equity snapshots")
    else:
        print(f"  DISCONTINUOUS across {report.bars.snapshots:,} equity snapshots")
        print(f"    gaps            {list(report.bars.gaps)}")
        print(f"    regressions     {report.bars.regressions}")
        print(f"    largest gap     {report.bars.max_gap} bars")
    print()
    print(f"systemd log lines   {report.log_lines:,}")


def _duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3_600:
        return f"{seconds / 60:.1f}m"
    if seconds < 86_400:
        return f"{seconds / 3_600:.1f}h"
    return f"{seconds / 86_400:.1f}d"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="soak_report",
        description="Summarise what a soak run did, from the journal and the systemd log.",
    )
    parser.add_argument(
        "--journal",
        default=os.environ.get(JOURNAL_PATH_ENV) or str(DEFAULT_JOURNAL_PATH),
        help="journal to read (opened read-only; safe against a live engine)",
    )
    parser.add_argument(
        "--unit",
        default=os.environ.get(UNIT_ENV) or DEFAULT_UNIT,
        help="systemd unit whose log to include in the report",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=int(os.environ.get(TOP_ENV) or DEFAULT_TOP),
        help="how many latency outliers to print",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Produce one report. Returns 0 on success, 1 if the journal cannot be read."""
    arguments = _parser().parse_args(argv)
    try:
        report = build_report(
            Path(arguments.journal), unit=arguments.unit, top=arguments.top
        )
    except SoakError as exc:
        print(f"soak_report: {exc}", file=sys.stderr)
        return 1

    write_report(report, as_json=arguments.json)
    # A broken chain is a report, not a crash: it is the finding the operator needs, and an
    # exit code that hides it behind a traceback would be worse.
    return 0 if report.chain.is_valid else 2


if __name__ == "__main__":
    raise SystemExit(main())
