"""The journal schema, the hash chain, and chain verification.

Storage decisions, and why each one is load-bearing:

* **``sequence INTEGER PRIMARY KEY``** -- a rowid alias, so the chain has a total order that
  is cheap to query and cannot be forged by inserting a later row with a smaller sequence.
* **``price_ticks INTEGER``** -- prices live as integer tick counts, never as a float or
  even as a decimal string. A tick is the smallest representable increment, so integer ticks
  are exact and compare correctly under ``<`` and ``>``.
* **``amount TEXT``** -- currency is stored via ``format(d, 'f')`` so no binary rounding
  ever touches it, and so a value read back is bit-identical to the value written.
* **``previous_hash`` / ``event_hash`` BLOB** -- the chain. ``event_hash`` commits over the
  raw bytes of the previous hash plus the canonical JSON payload, so changing any earlier row
  invalidates every hash after it.
* **UPDATE/DELETE triggers** -- the journal is append-only. Raising ``ABORT`` from a trigger
  turns an attempted edit into an ``IntegrityError`` at the database, not just in application
  code.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "GENESIS_HASH",
    "SCHEMA_SQL",
    "ChainError",
    "ChainVerification",
    "JournalHasher",
    "compute_event_hash",
    "connect_writer",
    "verify_chain",
]


class ChainError(ValueError):
    """A hash-chain input or result is malformed."""


#: The chain's starting point. A fixed, published constant rather than a random one, so any
#: two processes verifying the same journal compute the same genesis link.
GENESIS_HASH: Final[bytes] = hashlib.sha256(b"xauusd-lab:journal:genesis").digest()


def compute_event_hash(previous_hash: bytes, payload: bytes) -> bytes:
    """``sha256(previous_hash_bytes + canonical_json_bytes)``.

    Both inputs must be ``bytes``. Accepting a ``str`` here would be catastrophic: UTF-8
    re-encoding is not guaranteed to round-trip, and a silent mismatch would break every
    downstream link while looking like tampering.
    """
    for name, value in (("previous_hash", previous_hash), ("payload", payload)):
        if not isinstance(value, bytes):
            raise ChainError(f"{name} must be bytes, got {type(value).__name__}")
    return hashlib.sha256(previous_hash + payload).digest()


SCHEMA_SQL: Final[str] = """
-- The journal is append-only. Nothing above this line may modify it.

CREATE TABLE IF NOT EXISTS journal (
    sequence        INTEGER PRIMARY KEY,
    event_id        TEXT    NOT NULL UNIQUE,
    event_type      TEXT    NOT NULL,
    occurred_at     TEXT    NOT NULL,
    instrument      TEXT,
    amount          TEXT    NOT NULL,
    price_ticks     INTEGER,
    price_precision INTEGER,
    payload         TEXT    NOT NULL,
    previous_hash   BLOB    NOT NULL,
    event_hash      BLOB    NOT NULL,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS journal_occurred_at ON journal (occurred_at);
CREATE INDEX IF NOT EXISTS journal_event_type ON journal (event_type, sequence);
CREATE INDEX IF NOT EXISTS journal_instrument ON journal (instrument, sequence);

-- Immutable history: an UPDATE or a DELETE aborts the statement. The message is part of
-- the contract, because callers match on it to distinguish an edit attempt from a bug.
CREATE TRIGGER IF NOT EXISTS journal_no_update
BEFORE UPDATE ON journal
BEGIN
    SELECT RAISE(ABORT, 'journal rows are immutable');
END;

CREATE TRIGGER IF NOT EXISTS journal_no_delete
BEFORE DELETE ON journal
BEGIN
    SELECT RAISE(ABORT, 'journal rows are immutable');
END;
"""


def connect_writer(path: str | None = None) -> sqlite3.Connection:
    """Open a connection for the journal, applying the pragmas the writer requires.

    ``journal_mode = WAL`` lets readers run while the writer commits, and
    ``synchronous = FULL`` makes a committed row durable rather than merely written. The
    caller owns the returned connection; nothing here starts a thread.
    """
    connection = sqlite3.connect(path or ":memory:", check_same_thread=True)
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = FULL")
    connection.executescript(SCHEMA_SQL)
    return connection


@dataclass(frozen=True, slots=True)
class JournalHasher:
    """Chains event hashes, carrying the running link.

    Holds the genesis so a caller cannot accidentally start a chain from zero bytes or from
    the wrong constant.
    """

    genesis: bytes = GENESIS_HASH

    def chain(self, previous_hash: bytes, payload: bytes) -> bytes:
        return compute_event_hash(previous_hash, payload)

    def chain_all(self, payloads: Iterable[bytes]) -> list[bytes]:
        """Chain every payload in order, returning each link's hash."""
        running = self.genesis
        hashes: list[bytes] = []
        for payload in payloads:
            running = self.chain(running, payload)
            hashes.append(running)
        return hashes


@dataclass(frozen=True, slots=True)
class ChainVerification:
    """The outcome of walking a journal."""

    is_valid: bool
    rows_verified: int
    tail_hash: bytes | None
    errors: tuple[ChainError, ...]

    @property
    def reason(self) -> str:
        """The first failure, or ``""`` when the chain is intact."""
        return str(self.errors[0]) if self.errors else ""


def _row_payload(row: sqlite3.Row) -> bytes:
    """The canonical JSON bytes stored in a row, exactly as the writer wrote them."""
    value = row["payload"]
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    raise ChainError(f"payload is neither text nor blob: {type(value).__name__}")


def _preceding_hash(connection: sqlite3.Connection, start_sequence: int) -> bytes:
    """The event_hash of the row immediately before ``start_sequence``.

    Returns the genesis link when there is no such row, which is the case for a fresh
    journal or a start of 1.
    """
    row = connection.execute(
        "SELECT event_hash FROM journal WHERE sequence < ? ORDER BY sequence DESC LIMIT 1",
        (start_sequence,),
    ).fetchone()
    if row is None:
        return GENESIS_HASH
    value = row[0]
    if not isinstance(value, bytes):
        raise ChainError(f"stored event_hash is not a blob: {type(value).__name__}")
    return value


def verify_chain(
    connection: sqlite3.Connection,
    *,
    start_sequence: int = 1,
    expected_tail: bytes | None = None,
) -> ChainVerification:
    """Audit the journal's hash chain, in ``sequence`` order.

    Checks, for every row: that ``previous_hash`` equals the preceding row's ``event_hash``
    (the genesis for the first), and that ``event_hash`` recomputes from that ``previous_hash``
    plus the stored canonical payload. Any mismatch is reported rather than raised, so one
    corrupt row yields the whole list of problems instead of just the first.

    Args:
        connection: A connection to the journalling database.
        start_sequence: First sequence to verify, for resuming after a checkpoint.
        expected_tail: If given, the chain must end on exactly this hash.

    Returns:
        A :class:`ChainVerification` report.
    """
    if not isinstance(connection, sqlite3.Connection):
        raise ChainError("verify_chain requires an sqlite3.Connection")

    connection.row_factory = sqlite3.Row
    rows: list[sqlite3.Row] = list(
        connection.execute(
            "SELECT sequence, event_id, payload, previous_hash, event_hash"
            " FROM journal WHERE sequence >= ? ORDER BY sequence",
            (start_sequence,),
        )
    )

    errors: list[ChainError] = []
    # Resuming past a checkpoint must resume from the row the chain was actually at, not
    # from the genesis link. Otherwise every resumed verification would fail at its first row.
    previous = _preceding_hash(connection, start_sequence)

    for row in rows:
        sequence = int(row["sequence"])
        try:
            stored_previous = bytes(row["previous_hash"])
            stored_hash = bytes(row["event_hash"])
        except (TypeError, ValueError) as exc:
            errors.append(ChainError(f"row {sequence}: unreadable hashes ({exc})"))
            break

        if stored_previous != previous:
            errors.append(
                ChainError(
                    f"row {sequence} ({row['event_id']}): previous_hash does not match "
                    f"the preceding event_hash"
                )
            )
            break

        try:
            recomputed = compute_event_hash(previous, _row_payload(row))
        except ChainError as exc:
            errors.append(ChainError(f"row {sequence}: {exc}"))
            break

        if recomputed != stored_hash:
            errors.append(
                ChainError(
                    f"row {sequence} ({row['event_id']}): event_hash does not match "
                    "a recomputation over the stored payload"
                )
            )
            break

        previous = stored_hash

    if expected_tail is not None and expected_tail != previous:
        errors.append(
            ChainError(f"tail hash is {previous.hex()}, expected {expected_tail.hex()}")
        )

    return ChainVerification(
        is_valid=not errors,
        rows_verified=len(rows),
        tail_hash=previous,
        errors=tuple(errors),
    )


#: Formatting helper for callers that must persist a currency amount.
def format_amount(value: Decimal) -> str:
    """Serialize a Decimal for TEXT storage, exactly and without an exponent."""
    if not isinstance(value, Decimal):
        raise ChainError(f"amount must be a Decimal, got {type(value).__name__}")
    if not value.is_finite():
        raise ChainError(f"amount must be finite, got {value!r}")
    return format(value, "f")


#: Formatting helper for callers that must persist a UTC timestamp.
def format_timestamp(value: dt.datetime) -> str:
    """Serialize an aware datetime as an ISO-8601 UTC string."""
    if not isinstance(value, dt.datetime) or value.tzinfo is None:
        raise ChainError(f"timestamp must be timezone-aware, got {value!r}")
    return value.astimezone(dt.UTC).isoformat()


def price_to_ticks(price: Decimal, spec: Any) -> int:
    """Convert a price to an integer tick count using an ``InstrumentSpec``.

    The spec owns the tick size; this function never assumes one. A price that does not land
    exactly on a tick is rejected rather than rounded, because a silently rounded price in
    the journal would be indistinguishable from the real one.
    """
    tick = spec.tick
    ratio = price / tick
    if ratio != ratio.to_integral_value():
        raise ChainError(
            f"price {price!r} is not a whole number of {tick} ticks for {spec.name}"
        )
    return int(ratio)


def ticks_to_price(ticks: int, precision: int) -> Decimal:
    """Rebuild a price from its tick count and the precision it was stored at."""
    if isinstance(ticks, bool) or not isinstance(ticks, int):
        raise ChainError(f"ticks must be an int, got {type(ticks).__name__}")
    if ticks < 0:
        raise ChainError(f"ticks must not be negative, got {ticks}")
    if isinstance(precision, bool) or not isinstance(precision, int) or precision < 0:
        raise ChainError(f"precision must be a non-negative int, got {precision!r}")
    return Decimal(ticks) * (Decimal(10) ** -precision)
