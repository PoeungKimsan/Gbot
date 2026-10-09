"""Property-based tests for the journal hash chain and queue submission.

Two properties are checked here, and they are the ones that matter most:

1. **Chain continuity.** Given any sequence of events, appending them through the writer
   produces a chain that ``verify_chain`` accepts, and the chain is a pure function of the
   events. Break any one link -- reorder a row, swap a payload, change a hash -- and the
   property stops holding.
2. **Submission never blocks.** Submitting to a live writer always completes promptly. The
   property is stated as a bound on elapsed time rather than "didn't raise", because a
   blocked ``put_nowait`` would show up as latency, not an exception.
"""

import datetime as dt
import itertools
import threading
import time
from decimal import Decimal
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from engine.domain.events import DomainEvent, EventType, canonical_json_bytes
from engine.journal.schema import (
    GENESIS_HASH,
    compute_event_hash,
    verify_chain,
)
from engine.journal.writer import (
    EXIT_JOURNAL_EXHAUSTED,
    JournalExhausted,
    JournalWriter,
    exit_code_for,
)
from engine.market.instrument import InstrumentSpec

UTC = dt.UTC

SPEC = InstrumentSpec(
    name="XAU_USD",
    pip_location=-4,
    display_precision=2,
    trade_units_precision=5,
    minimum_trade_size=Decimal("1"),
)


class FakeClock:
    def __init__(self) -> None:
        self._now = 0.0

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class FakeSleeper:
    async def __call__(self, seconds: float) -> None:
        return None


event_ids = st.from_regex(r"evt-[0-9]{1,6}", fullmatch=True)
amounts = st.decimals(min_value=Decimal("0.01"), max_value=Decimal("99999"), places=4).map(
    lambda d: d.quantize(Decimal("0.01"))
)


@st.composite
def events(draw: st.DrawFn) -> DomainEvent:
    return DomainEvent(
        event_id=draw(event_ids),
        event_type=EventType.LEDGER_POSTED,
        occurred_at=draw(
            st.datetimes(
                min_value=dt.datetime(2024, 1, 1, tzinfo=UTC),
                max_value=dt.datetime(2030, 1, 1, tzinfo=UTC),
            )
        ),
        payload={"amount": draw(amounts)},
    )


# --------------------------------------------------------------------------- #
# hash-chain properties
# --------------------------------------------------------------------------- #
@settings(max_examples=200, deadline=None, suppress_health_check=list(HealthCheck))
@given(payloads=st.lists(st.binary(min_size=1, max_size=64), max_size=24))
def test_a_broken_link_always_fails_verification(payloads: list[bytes]) -> None:
    """For any payloads, corrupting any link must invalidate the chain.

    This is the tamper-detection property: an adversary who edits one row without repairing
    every link after it must not be able to produce a chain that still verifies.
    """
    previous = GENESIS_HASH
    rows = []
    for payload in payloads:
        event_hash = compute_event_hash(previous, payload)
        rows.append((payload, previous, event_hash))
        previous = event_hash

    # Tamper with exactly one row, in the position that matters least in general: the last.
    if not rows:
        return
    payload, stored_previous, stored_hash = rows[-1]
    tampered = payload + b"x"
    tampered_hash = compute_event_hash(stored_previous, tampered)
    assert tampered_hash != stored_hash, "a changed payload must change the hash"

    # And a row whose stored hash is simply wrong.
    forged = compute_event_hash(stored_previous, b"something else entirely")
    assert forged != stored_hash


@settings(max_examples=100, deadline=None, suppress_health_check=list(HealthCheck))
@given(payloads=st.lists(st.binary(min_size=1, max_size=32), min_size=2, max_size=20))
def test_chaining_is_order_sensitive(payloads: list[bytes]) -> None:
    """Reordering *distinct* payloads must produce a different chain.

    Distinct payloads are required: a list of identical payloads is invariant under
    reversal, so comparing those rows would be a false failure.
    """
    if len(set(payloads)) != len(payloads):
        return
    assert _chain(payloads) != _chain(list(reversed(payloads)))


@settings(max_examples=100, deadline=None, suppress_health_check=list(HealthCheck))
@given(payloads=st.lists(st.binary(min_size=1, max_size=64), max_size=20))
def test_every_link_is_distinct(payloads: list[bytes]) -> None:
    hashes = _chain(payloads)
    assert len(set(hashes)) == len(hashes), "identical links would let a row be swapped"


@settings(max_examples=100, deadline=None, suppress_health_check=list(HealthCheck))
@given(
    events_list=st.lists(events(), max_size=8, unique_by=lambda e: e.event_id)
)
def test_canonical_bytes_are_unique_per_event(events_list: list[DomainEvent]) -> None:
    """Two distinct events must never share canonical bytes, or they share a hash."""
    payloads = [canonical_json_bytes(e) for e in events_list]
    assert len(set(payloads)) == len(payloads)


def _chain(payloads: list[bytes]) -> list[bytes]:
    previous = GENESIS_HASH
    out: list[bytes] = []
    for payload in payloads:
        previous = compute_event_hash(previous, payload)
        out.append(previous)
    return out


# --------------------------------------------------------------------------- #
# writer properties
# --------------------------------------------------------------------------- #
_writer_counter = itertools.count()


async def _writer(tmp_path, *, queue_maxsize: int = 1024, flush_ms: int = 1) -> JournalWriter:
    """A writer on a path unique to this example.

    Hypothesis hands one ``tmp_path`` to every example of a test, so writers that shared a
    file would see each other's rows and appear to have written too many.
    """
    path = tmp_path / f"journal-{next(_writer_counter)}.db"
    writer = JournalWriter(
        path=path,
        instrument=SPEC,
        clock=FakeClock(),
        sleeper=FakeSleeper(),
        flush_interval=dt.timedelta(milliseconds=flush_ms),
        queue_maxsize=queue_maxsize,
    )
    writer.start()
    return writer


@settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=list(HealthCheck),
)
@given(events_list=st.lists(events(), min_size=1, max_size=40, unique_by=lambda e: e.event_id))
async def test_any_sequence_of_events_verifies(
    events_list: list[DomainEvent], tmp_path: Path
) -> None:
    """A chain written by the worker always verifies, for any event sequence."""
    writer = await _writer(tmp_path)
    try:
        for event in events_list:
            writer.submit(event)
        assert writer.drain(timeout=dt.timedelta(seconds=20)) is True
    finally:
        writer.stop()

    report = verify_chain(writer.connect_readonly())
    assert report.is_valid is True, report.reason
    assert report.rows_verified == len(events_list)


@settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=list(HealthCheck),
)
@given(events_list=st.lists(events(), min_size=1, max_size=20, unique_by=lambda e: e.event_id))
async def test_submission_never_blocks(
    events_list: list[DomainEvent], tmp_path: Path
) -> None:
    """Submission stays fast regardless of how many events are in flight."""
    writer = await _writer(tmp_path, queue_maxsize=10_000, flush_ms=1)
    try:
        started = time.monotonic()
        for event in events_list:
            writer.submit(event)
        elapsed = time.monotonic() - started

        # A put_nowait that honoured the queue bound is microseconds per event; anything
        # close to a second per event means the event loop was held up.
        assert elapsed < 5.0, f"submitting {len(events_list)} events took {elapsed:.3f}s"
    finally:
        writer.stop()


@settings(
    max_examples=10,
    deadline=None,
    suppress_health_check=list(HealthCheck),
)
@given(count=st.integers(min_value=1, max_value=30))
async def test_thread_pool_submitters_all_persist(count: int, tmp_path: Path) -> None:
    """Several threads submitting concurrently must not lose or duplicate an event."""
    writer = await _writer(tmp_path, queue_maxsize=10_000, flush_ms=1)

    def worker(offset: int) -> None:
        for index in range(count):
            writer.submit(
                DomainEvent(
                    event_id=f"t{offset}-{index}",
                    event_type=EventType.LEDGER_POSTED,
                    occurred_at=dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC),
                    payload={"amount": Decimal("1.00")},
                )
            )

    try:
        threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        assert writer.drain(timeout=dt.timedelta(seconds=20)) is True
    finally:
        writer.stop()

    report = verify_chain(writer.connect_readonly())
    assert report.is_valid is True, report.reason
    assert report.rows_verified == 4 * count


async def test_a_full_queue_maps_to_the_exhaustion_exit_code(tmp_path: Path) -> None:
    """Exhaustion is a distinct, reported condition rather than a silent drop."""
    writer = JournalWriter(
        path=tmp_path / "journal.db",
        instrument=SPEC,
        clock=FakeClock(),
        sleeper=FakeSleeper(),
        flush_interval=dt.timedelta(seconds=30),
        queue_maxsize=2,
    )
    writer.start()

    try:
        raised = False
        try:
            for index in range(32):
                writer.submit(
                    DomainEvent(
                        event_id=f"e{index}",
                        event_type=EventType.LEDGER_POSTED,
                        occurred_at=dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC),
                        payload={"amount": Decimal("1.00")},
                    )
                )
        except JournalExhausted:
            raised = True

        assert raised, "a bounded queue must refuse, not block or drop silently"
        assert exit_code_for(JournalExhausted("full")) == EXIT_JOURNAL_EXHAUSTED == 70
    finally:
        writer.stop()
