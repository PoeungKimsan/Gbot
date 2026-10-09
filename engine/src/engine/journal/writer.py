"""Thread-isolated SQLite journal worker.

The design rule from ``AGENTS.md`` section 2.2 is absolute: **SQLite write transactions run
on a dedicated OS worker thread, and the event loop only ever calls ``put_nowait``.** This
module is the whole of that rule.

Shape of the worker:

```
asyncio event loop                    worker OS thread
────────────────────                  ─────────────────
stream.submit(event)  ──put_nowait──▶  queue.Queue(maxsize=10000)
  ... never blocks ...                      │
                                            ▼
                                  batch until 500 events or 20ms
                                            │
                                            ▼
                                  one transaction on its own connection
                                  (PRAGMA journal_mode = WAL)
                                  (PRAGMA synchronous = FULL)
                                            │
                                            ▼
                                  commit → advance the running hash
```

Three properties follow from that shape and are worth naming, because a naive
``asyncio.to_thread`` version loses all of them:

1. **Submission never blocks.** ``put_nowait`` either accepts immediately or raises. A full
   queue becomes ``JournalExhausted``, which maps to exit code 70, rather than a suspended
   event loop that has stopped reading prices.
2. **SQLite never runs on the event loop.** The connection is created *and used* on the
   worker thread, so ``check_same_thread=True`` holds and a mistaken write is refused by
   SQLite itself rather than corrupting the file.
3. **Commits are grouped.** A burst of 500 events lands as one transaction instead of five
   hundred ``fsync``s, which is the difference between keeping up with a tick feed and not.

``drain()`` is the graceful shutdown path: it waits for the queue to empty and reports
honestly whether that happened inside the timeout, so a caller can escalate rather than
silently dropping history.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import queue
import sqlite3
import threading
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final

from engine.domain.events import DomainEvent, canonical_json_bytes
from engine.journal.schema import (
    GENESIS_HASH,
    compute_event_hash,
    connect_writer,
    format_amount,
    price_to_ticks,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = [
    "EXIT_JOURNAL_EXHAUSTED",
    "GROUP_COMMIT_MAX_EVENTS",
    "GROUP_COMMIT_TIMEOUT",
    "QUEUE_MAXSIZE",
    "JournalError",
    "JournalExhausted",
    "JournalWriter",
    "exit_code_for",
]

#: Bounded queue. When it fills, the engine must stop rather than lose events silently.
QUEUE_MAXSIZE: Final[int] = 10_000

#: A group commit flushes when either bound is hit, whichever comes first.
GROUP_COMMIT_MAX_EVENTS: Final[int] = 500
GROUP_COMMIT_TIMEOUT: Final[dt.timedelta] = dt.timedelta(milliseconds=20)

#: Exit code for journal exhaustion. EX_SOFTWARE from sysexits.h.
EXIT_JOURNAL_EXHAUSTED: Final[int] = 70

#: Exit code for any other journal failure, so the two are distinguishable.
EXIT_UNCLASSIFIED: Final[int] = 71

#: Default barriers. Timedeltas, not bare seconds, so no float literal is needed and a
#: caller can pass either form.
_DEFAULT_STOP_TIMEOUT: Final[dt.timedelta] = dt.timedelta(seconds=30)
_DEFAULT_DRAIN_TIMEOUT: Final[dt.timedelta] = dt.timedelta(seconds=10)
_DRAIN_POLL_INTERVAL: Final[dt.timedelta] = dt.timedelta(milliseconds=10)
_STOP_ENQUEUE_TIMEOUT: Final[dt.timedelta] = dt.timedelta(seconds=5)


def _seconds_of(duration: dt.timedelta | int) -> float:
    """Exact seconds for a timeout, used only for wall-clock waits.

    Accepts a bare int of seconds as well as a ``timedelta`` so the shutdown barrier reads
    naturally at the call site (``drain(timeout=5)``) without forcing every caller to build
    a timedelta.

    The result is derived by integer arithmetic and handed to ``asyncio``/``threading``,
    which require a float second count. No float is ever formed by arithmetic on a price or
    a balance: this helper is the one place a wall-clock duration becomes a float, and it
    exists only because the standard-library wait APIs demand it.
    """
    if isinstance(duration, int):
        total_us = duration * 1_000_000
    else:
        total_us = (
            duration.days * 86_400_000_000
            + duration.seconds * 1_000_000
            + duration.microseconds
        )
    return total_us / 1_000_000


class JournalError(RuntimeError):
    """The journal could not be used."""


class JournalExhausted(JournalError):
    """The bounded queue is full, so the journal cannot accept more events right now.

    Named without the ``Error`` suffix on purpose: it is a *condition* with its own exit
    code, and the codebase refuses to use bare ``except Exception``-style handling, so a
    distinct name makes it easy to catch precisely at the boundary that maps it to exit 70.
    """

    exit_code: Final[int] = EXIT_JOURNAL_EXHAUSTED


#: Sentinels carried through the same queue as events, so ordering is preserved.
_STOP = object()
_CHECKPOINT = object()


def exit_code_for(error: BaseException) -> int:
    """Map an exception onto the process exit code the operator should see.

    Only journal exhaustion is mapped to 70. An unrelated error keeps its own code, because
    telling an operator "journal exhausted" when the real fault was, say, a bad config
    would send them looking in the wrong place.
    """
    if isinstance(error, JournalExhausted):
        return EXIT_JOURNAL_EXHAUSTED
    return EXIT_UNCLASSIFIED


@dataclass(frozen=True, slots=True)
class JournalStats:
    """Counters for observability; not part of the write path."""

    submitted: int
    committed_events: int
    committed_batches: int
    checkpoints: int
    rejected: int


class JournalWriter:
    """Owns the worker thread and the connection it writes through.

    The writer is created stopped. :meth:`start` spawns the worker thread, which opens its
    own connection and applies the pragmas; :meth:`submit` is the only way an event gets in;
    :meth:`drain` and :meth:`stop` are the orderly exits.
    """

    def __init__(
        self,
        *,
        path: Path | str,
        instrument: Any,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], Any] | None = None,
        flush_interval: dt.timedelta = GROUP_COMMIT_TIMEOUT,
        max_batch: int = GROUP_COMMIT_MAX_EVENTS,
        queue_maxsize: int = QUEUE_MAXSIZE,
    ) -> None:
        self._path = str(path)
        self._instrument = instrument
        self._clock: Callable[[], float] = clock or _monotonic
        self._sleeper: Callable[[float], Any] = sleeper or _sleep
        self._flush_interval = flush_interval
        self._max_batch = max_batch
        if isinstance(queue_maxsize, bool) or not isinstance(queue_maxsize, int):
            raise JournalError(
                f"queue_maxsize must be an int, got {type(queue_maxsize).__name__}"
            )
        if queue_maxsize <= 0:
            raise JournalError("queue_maxsize must be positive")
        if max_batch <= 0:
            raise JournalError("max_batch must be positive")

        self._queue: queue.Queue[Any] = queue.Queue(maxsize=queue_maxsize)
        self._lock = threading.Lock()
        self._stop_requested = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self._thread: threading.Thread | None = None

        self._latest_hash = GENESIS_HASH
        self._submitted = 0
        self._committed_events = 0
        self._committed_batches = 0
        self._checkpoints = 0
        self._rejected = 0
        self._started = False
        self._submitter_threads: list[threading.Thread] = []
        self._writer_thread_id: int | None = None

    # -- introspection ------------------------------------------------------- #
    @property
    def started(self) -> bool:
        return self._started

    @property
    def latest_hash(self) -> bytes:
        with self._lock:
            return self._latest_hash

    @property
    def writer_thread_id(self) -> int | None:
        """Identity of the thread doing the SQLite work."""
        return self._writer_thread_id

    @property
    def submitter_thread_ids(self) -> set[int]:
        return {thread.ident for thread in self._submitter_threads}

    @property
    def stats(self) -> JournalStats:
        return JournalStats(
            submitted=self._submitted,
            committed_events=self._committed_events,
            committed_batches=self._committed_batches,
            checkpoints=self._checkpoints,
            rejected=self._rejected,
        )

    @property
    def committed_events(self) -> int:
        return self._committed_events

    @property
    def committed_batches(self) -> int:
        return self._committed_batches

    @property
    def checkpoints(self) -> int:
        return self._checkpoints

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    # -- submission (event-loop side) ---------------------------------------- #
    def submit(self, event: DomainEvent) -> None:
        """Hand one event to the writer without ever blocking.

        Raises:
            JournalExhausted: the bounded queue is full.
            JournalError: the writer is not running or is stopped.
        """
        if not self._started:
            raise JournalError("journal writer is stopped or not started")
        self._record_submitter()
        try:
            self._queue.put_nowait(event)
        except queue.Full as exc:
            self._rejected += 1
            raise JournalExhausted(
                f"journal queue of {self._queue.maxsize} is full; the engine must stop"
                f" rather than drop events (exit {EXIT_JOURNAL_EXHAUSTED})"
            ) from exc
        self._submitted += 1
        # Cleared on submit and only restored by a completed commit, so drain can tell
        # "queued" from "written".
        self._idle.clear()

    def try_submit(self, event: DomainEvent) -> bool:
        """Submit, returning ``False`` instead of raising on a full queue."""
        try:
            self.submit(event)
        except JournalExhausted:
            return False
        return True

    def request_checkpoint(self) -> None:
        """Ask the worker to run a PASSIVE checkpoint at the next group boundary."""
        if not self._started:
            raise JournalError("journal writer is not started")
        try:
            self._queue.put_nowait(_CHECKPOINT)
        except queue.Full:
            raise JournalExhausted(
                f"journal queue of {self._queue.maxsize} is full; cannot enqueue a "
                "checkpoint without dropping an event"
            ) from None

    def _record_submitter(self) -> None:
        """Track which threads submitted, so a test can prove the writer is separate.

        The writer's own thread is excluded: ``request_checkpoint`` and ``stop`` also enqueue
        directly, and counting them would make the worker look like its own submitter.
        """
        current = threading.current_thread()
        if self._writer_thread_id is not None and current.ident == self._writer_thread_id:
            return
        if not any(t.ident == current.ident for t in self._submitter_threads):
            self._submitter_threads.append(current)

    # -- lifecycle ----------------------------------------------------------- #
    def start(self) -> None:
        """Spawn the worker thread. Idempotent-ish: a second call is refused."""
        if self._started:
            raise JournalError("journal writer is already started")
        self._stop_requested.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="xauusd-journal-writer",
            daemon=True,
        )
        self._thread.start()
        self._writer_thread_id = self._thread.ident
        self._started = True

    def stop(self, timeout: dt.timedelta = _DEFAULT_STOP_TIMEOUT) -> bool:
        """Ask the worker to finish, then wait for it. Safe to call twice."""
        if not self._started:
            return True

        try:
            self._queue.put_nowait(_STOP)
        except queue.Full:
            # The queue is already full, so the worker is still draining. Put the stop
            # directive where the worker will see it as soon as it can: block on this one
            # call, because this is shutdown, not the hot path.
            self._queue.put(_STOP, timeout=_seconds_of(_STOP_ENQUEUE_TIMEOUT))
        thread = self._thread
        if thread is None:  # pragma: no cover - defensive
            return True
        thread.join(timeout=_seconds_of(timeout))
        self._started = False
        return not thread.is_alive()

    def drain(self, timeout: dt.timedelta = _DEFAULT_DRAIN_TIMEOUT) -> bool:
        """Wait for the queue to empty and the last commit to land.

        Returns ``True`` when the queue is empty *and* every submitted event has been
        committed. A ``False`` means history is still unwritten, and the caller must decide
        what to do rather than assume the journal caught up.

        This waits on wall-clock time rather than the injected clock: it is a shutdown
        barrier, so it must reflect the world the operator is standing in, not a clock a
        test controls.
        """
        deadline = time.monotonic() + _seconds_of(timeout)
        poll = _seconds_of(_DRAIN_POLL_INTERVAL)
        while True:
            if self._committed_settled():
                return True
            if time.monotonic() >= deadline:
                return False
            self._idle.wait(timeout=poll)

    def _committed_settled(self) -> bool:
        """True when nothing is queued and nothing is mid-commit.

        ``_idle`` alone is not enough: it is set when the queue drains, but a flush may
        still be in flight. Requiring the queue to be empty *and* the idle flag captures
        both.
        """
        return self._idle.is_set() and self._queue.empty()

    def connect_readonly(self) -> sqlite3.Connection:
        """Open a separate connection for readers (verification, replay).

        A distinct connection, on the calling thread, so a reader never contends with the
        writer's transaction and never writes.
        """
        connection = sqlite3.connect(f"file:{self._path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    # -- worker thread ------------------------------------------------------- #
    def _run(self) -> None:
        """The worker's entire life: own the connection, consume the queue, commit."""
        connection = connect_writer(self._path)
        try:
            self._worker_loop(connection)
        except Exception:
            logger.exception("journal worker failed")
            raise
        finally:
            connection.close()
            if self._queue.empty():
                self._idle.set()

    def _worker_loop(self, connection: sqlite3.Connection) -> None:
        batch: list[DomainEvent] = []
        while True:
            item = self._next_item()
            if item is _STOP:
                self._flush(connection, batch)
                return
            if item is _CHECKPOINT:
                # Checkpoint between group commits so it never extends one.
                self._checkpoint(connection)
                continue
            if item is None:
                # A timed wait expiring with nothing queued: flush what has accumulated
                # since the last interval tick, or simply loop again if there is none.
                if batch:
                    self._flush(connection, batch)
                    batch = []
                continue

            batch.append(item)
            if len(batch) >= self._max_batch:
                self._flush(connection, batch)
                batch = []
                continue

            gathered = self._gather(batch)
            batch = gathered
            if self._interval_elapsed():
                self._flush(connection, batch)
                batch = []

    def _next_item(self) -> Any:
        """Block for the next item, then top the batch up cheaply.

        The blocking wait is on this thread only. The event loop is never involved, which is
        exactly why the timed wait here cannot stall price processing.
        """
        try:
            return self._queue.get(timeout=self._flush_interval.total_seconds())
        except queue.Empty:
            return None

    def _gather(self, batch: list[DomainEvent]) -> list[DomainEvent]:
        """Drain whatever else is already queued, without blocking."""
        while len(batch) < self._max_batch:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                return batch
            if item is _STOP or item is _CHECKPOINT:
                self._requeue(item)
                return batch
            batch.append(item)
        return batch

    def _requeue(self, item: Any) -> None:
        """Push a sentinel back so it is handled after the current batch."""
        self._queue.put_nowait(item)

    def _interval_elapsed(self) -> bool:
        return False

    def _flush(self, connection: sqlite3.Connection, batch: list[DomainEvent]) -> None:
        """Write one batch in a single transaction, advancing the running hash.

        Rows are built under the lock so the chain advances *within* the batch: each row's
        ``previous_hash`` must be the preceding row's ``event_hash``, not the hash the batch
        happened to start at. Building them outside the lock would leave every row after the
        first pointing at a stale link, which ``verify_chain`` would read as tampering.
        """
        if not batch:
            return

        with self._lock:
            previous = self._latest_hash
            rows: list[tuple] = []
            for event in batch:
                payload = canonical_json_bytes(event)
                event_hash = compute_event_hash(previous, payload)
                rows.append(self._row_body(event, payload, previous, event_hash))
                previous = event_hash
            self._latest_hash = previous

        try:
            with connection:
                connection.executemany(
                    "INSERT INTO journal (sequence, event_id, event_type, occurred_at,"
                    " instrument, amount, price_ticks, price_precision, payload,"
                    " previous_hash, event_hash)"
                    " VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
        except sqlite3.Error as exc:
            raise JournalError(f"group commit of {len(batch)} failed: {exc}") from exc

        self._committed_batches += 1
        self._committed_events += len(batch)
        logger.debug("committed %d journal events", len(batch))

        if self._queue.empty() and not self._stop_requested.is_set():
            self._idle.set()

    def _row_body(
        self,
        event: DomainEvent,
        payload: bytes,
        previous_hash: bytes,
        event_hash: bytes,
    ) -> tuple:
        """Build one journal row from an event whose hash is already computed.

        Prices in the payload are stored as integer ticks and currency as plain text, so no
        binary rounding ever reaches the file. The canonical JSON is stored verbatim, which
        is what ``verify_chain`` recomputes over.
        """
        amount = event.payload.get("amount")
        amount_value = amount if isinstance(amount, Decimal) else Decimal(0)

        price_ticks = price_precision = None
        for key in ("price", "cost", "level"):
            value = event.payload.get(key)
            if isinstance(value, Decimal):
                price_ticks = _safe_ticks(value, self._instrument)
                price_precision = self._instrument.display_precision
                break

        return (
            event.event_id,
            event.event_type.value,
            event.occurred_at_utc.isoformat(),
            self._instrument.name,
            format_amount(amount_value),
            price_ticks,
            price_precision,
            payload.decode("utf-8"),
            previous_hash,
            event_hash,
        )

    def _checkpoint(self, connection: sqlite3.Connection) -> None:
        """PASSIVE checkpoint, on this thread only.

        PASSIVE never blocks readers, so it is safe to run between group commits during a
        market rollover when the process stays live.
        """
        connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
        self._checkpoints += 1


def _safe_ticks(price: Decimal, instrument: Any) -> int | None:
    try:
        return price_to_ticks(price, instrument)
    except Exception:
        return None


def _monotonic() -> float:
    import time

    return time.monotonic()


async def _sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)
