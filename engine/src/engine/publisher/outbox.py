"""The projection publisher: an independent process that reads the journal aloud.

The publisher is a separate process on purpose. A dashboard that reads the trading
journal while the writer is committing is a dashboard that can stop the trader, so the
projection reads ``mode=ro``, never writes to the database, and never opens a second
connection to the same file.

Everything it produces is a *snapshot*: ``state.json`` for the run, ``trades.json`` for
the closed trades, ``metrics.json`` for the report. Each is written through a temp file
and ``os.replace``, so a reader never sees half a document -- it sees the previous whole
one or the next whole one, and nothing between.

What it folds is the journal, and only the journal: an event type it does not know is
ignored rather than fatal, because a new event type is a new phase, and the dashboard
must survive one.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Final

from engine.domain.events import EventType

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

__all__ = [
    "SNAPSHOT_FILES",
    "ProjectionPublisher",
    "ProjectionReport",
    "atomic_write_json",
    "read_snapshot",
]

#: The three documents every projection publishes, in the order they are written.
SNAPSHOT_FILES: Final[tuple[str, ...]] = ("state.json", "trades.json", "metrics.json")


def read_snapshot(path: Path) -> dict[str, object] | None:
    """Read one snapshot, or ``None`` if it is absent or unreadable.

    A corrupt or missing document is reported as absent rather than raised: the
    dashboard's job is to say "no data yet", and a crash there would look like a
    trading failure.
    """
    if not path.is_file():
        return None
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def atomic_write_json(path: Path, payload: object) -> None:
    """Write ``payload`` to ``path`` so a reader never sees a partial document.

    A temp file in the same directory (so the rename is a rename and not a copy),
    then ``os.replace``, which is atomic on POSIX and on Windows alike. The temp name
    carries the process id and a counter, so two publishers writing at once do not
    clobber each other's partial files.
    """
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{_counter()}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        _replace_with_retry(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)


def _replace_with_retry(temporary: Path, path: Path) -> None:
    """Rename into place, retrying while a reader holds the destination open.

    ``os.replace`` is atomic everywhere, but on Windows it fails outright if another
    handle still has the destination open -- which a dashboard reader will, briefly,
    every time it serves a page. Retrying is the standard answer; the rename is still
    atomic from the reader's side, because the reader only ever opens ``path``.
    """
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(0.01 * (attempt + 1))


def _counter() -> int:
    _counter.value += 1
    return _counter.value


_counter.value = 0  # type: ignore[attr-defined]

#: How many times a rename is retried while a reader holds the destination open.
_REPLACE_ATTEMPTS: Final[int] = 10


@dataclass(frozen=True, slots=True)
class ProjectionPublisher:
    """Folds a journal into the three snapshot documents.

    Args:
        journal_path: The SQLite journal, opened read-only.
        projection_path: Where the snapshots are written.
    """

    journal_path: Path
    projection_path: Path

    def publish(self) -> ProjectionReport:
        """Fold everything new and rewrite the snapshots.

        The fold resumes from the last sequence seen, so a poll sees only what has
        been added. A report is returned rather than logged, so a caller (or a test)
        can tell whether anything actually changed.
        """
        state, trades = _ProjectionState(), _ProjectionTrades()
        folded = self._fold(state, trades)
        metrics = _metrics_of(trades)
        report = ProjectionReport(
            events_folded=folded,
            changed=folded > 0 or not (self.projection_path / "state.json").exists(),
        )
        for name, payload in (
            ("state.json", state.payload()),
            ("trades.json", trades.payload()),
            ("metrics.json", metrics),
        ):
            atomic_write_json(self.projection_path / name, payload)
        return report

    def _fold(self, state: _ProjectionState, trades: _ProjectionTrades) -> int:
        """Read every new event and let the two folds have it."""
        connection = sqlite3.connect(f"file:{self.journal_path}?mode=ro", uri=True)
        try:
            connection.row_factory = sqlite3.Row
            since = _resume_point(self.projection_path)
            rows = list(
                connection.execute(
                    "SELECT sequence, event_type, payload FROM journal"
                    " WHERE sequence > ? ORDER BY sequence",
                    (since,),
                )
            )
            for row in rows:
                _apply_event(row, state, trades)
            _mark_progress(self.projection_path, rows[-1]["sequence"] if rows else since)
            return len(rows)
        finally:
            connection.close()


@dataclass(frozen=True, slots=True)
class ProjectionReport:
    """What one publish did, so a caller can decide whether to log it."""

    events_folded: int
    changed: bool
    files_written: tuple[str, ...] = SNAPSHOT_FILES


@dataclass(slots=True)
class _ProjectionState:
    """The run header, the equity mark, and the counts a status page wants."""

    run_id: str = ""
    strategy_version: str = ""
    equity: str = "0"
    bars: int = 0
    open_positions: int = 0
    resting_orders: int = 0

    def payload(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "strategy_version": self.strategy_version,
            "equity": self.equity,
            "bars": self.bars,
            "open_positions": self.open_positions,
            "resting_orders": self.resting_orders,
            "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        }


@dataclass(slots=True)
class _ProjectionTrades:
    """The closed trades, in the order the reporting layer wants them."""

    trades: list[dict[str, object]] = field(default_factory=list)

    def payload(self) -> dict[str, object]:
        return {"count": len(self.trades), "trades": list(self.trades)}


def _apply_event(row: sqlite3.Row, state: _ProjectionState, trades: _ProjectionTrades) -> None:
    """Hand one journal row to the folds that care about it.

    A row's ``payload`` column holds the canonical *event* -- the whole body the writer
    hashed -- so the action-specific fields live one level down, under ``payload``. A
    projection that forgets that reads an empty document and reports a healthy engine
    with no trades, which is the exact failure this fold exists to avoid.
    """
    try:
        envelope = json.loads(row["payload"])
    except (json.JSONDecodeError, TypeError):
        return
    if not isinstance(envelope, dict):
        return
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        return

    event_type = str(row["event_type"])
    if event_type == EventType.RUN_STARTED.value:
        state.run_id = str(payload.get("run_id", ""))
        state.strategy_version = str(payload.get("strategy_version", ""))
        return
    if event_type == EventType.EQUITY_SNAPSHOT.value:
        state.equity = str(payload.get("equity", state.equity))
        state.bars = int(payload.get("bars", state.bars))
        return
    if event_type == EventType.POSITION_OPENED.value:
        state.open_positions += 1
        return
    if event_type == EventType.POSITION_CLOSED.value:
        state.open_positions = max(state.open_positions - 1, 0)
        _close_trade(payload, trades)
        return
    if event_type == EventType.ORDER_SUBMITTED.value:
        state.resting_orders += 1
        return
    if event_type == EventType.ORDER_CANCELLED.value:
        state.resting_orders = max(state.resting_orders - 1, 0)


def _close_trade(payload: dict[str, object], trades: _ProjectionTrades) -> None:
    """Append one closed trade, in the shape the reporting layer consumes.

    The journal writes the exit's reason as ``reason`` -- it is the supervisor's
    vocabulary -- while the snapshot calls it ``exit_reason``, because a dashboard
    reader has no other reason to disambiguate. The mapping is explicit so the two
    cannot silently drift.
    """
    trades.trades.append(
        {
            "trade_id": str(payload.get("position_id", "")),
            "side": str(payload.get("side", "")),
            "quantity": str(payload.get("quantity", "1")),
            "pnl": str(payload.get("amount", "0")),
            "price": str(payload.get("price", "0")),
            "cost": str(payload.get("cost", "0")),
            "r_multiple": str(payload.get("r_multiple", "0")),
            "exit_reason": str(payload.get("reason", "")),
            "reference": str(payload.get("reference", "")),
        }
    )


def _metrics_of(trades: _ProjectionTrades) -> dict[str, object]:
    """The report for the closed trades, from the Phase 3 reporting module."""
    from engine.metrics.performance import TradeResult, compute_performance

    results = [
        TradeResult(
            trade_id=str(trade.get("trade_id", "")),
            pnl=Decimal(str(trade.get("pnl", "0"))),
            r_multiple=Decimal(str(trade.get("r_multiple", "0"))),
        )
        for trade in trades.trades
    ]
    report = compute_performance(results)
    return {
        "status": report.status,
        "trade_count": report.trade_count,
        "realized_pnl": report.realized_pnl,
        "max_drawdown": report.max_drawdown,
        "win_rate": report.win_rate,
        "profit_factor": report.profit_factor,
        "expectancy_r": report.expectancy_r,
        "confidence_interval": (
            None
            if report.confidence_interval is None
            else {
                "lower": report.confidence_interval.lower,
                "upper": report.confidence_interval.upper,
            }
        ),
    }


#: Where the resume point lives, so a poll sees only what has been added. Not a
#: snapshot document, so it is deliberately not named ``*.json``.
_PROGRESS_NAME: Final[str] = ".progress"


def _resume_point(projection_path: Path) -> int:
    progress = read_snapshot(projection_path / _PROGRESS_NAME)
    if progress is None:
        return 0
    try:
        return int(progress.get("sequence", 0))
    except (TypeError, ValueError):
        return 0


def _mark_progress(projection_path: Path, sequence: int) -> None:
    atomic_write_json(projection_path / _PROGRESS_NAME, {"sequence": sequence})
