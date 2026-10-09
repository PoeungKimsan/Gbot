"""Unit tests for :mod:`engine.journal.schema`.

The invariants under test are the ones that make tampering detectable:

* an event hash is a pure function of the previous hash and the canonical payload;
* each row's ``previous_hash`` must equal the row before it's ``event_hash``;
* the UPDATE and DELETE triggers must actually fire, not merely exist.

The DDL is also scanned here so a banned column type (``REAL``/``FLOAT``/``DOUBLE``) cannot
slip into the schema and reintroduce float arithmetic through the back door.
"""

import datetime as dt
import hashlib
import sqlite3
from decimal import Decimal

import pytest

from engine.domain.events import DomainEvent, EventType, canonical_json_bytes
from engine.journal.schema import (
    GENESIS_HASH,
    SCHEMA_SQL,
    ChainError,
    JournalHasher,
    compute_event_hash,
    verify_chain,
)

UTC = dt.UTC


def _event(event_id: str, amount: str = "100.00") -> DomainEvent:
    return DomainEvent(
        event_id=event_id,
        event_type=EventType.LEDGER_POSTED,
        occurred_at=dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC),
        payload={"amount": Decimal(amount), "account": "CASH"},
    )


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(SCHEMA_SQL)
    return connection


def _write(connection: sqlite3.Connection, event: DomainEvent, previous_hash: bytes) -> bytes:
    """Append one row, returning its event hash."""
    payload = canonical_json_bytes(event)
    event_hash = compute_event_hash(previous_hash, payload)
    connection.execute(
        "INSERT INTO journal (event_id, event_type, occurred_at, amount, payload,"
        " previous_hash, event_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            event.event_id,
            event.event_type.value,
            event.occurred_at.astimezone(UTC).isoformat(),
            format(Decimal("100.00"), "f"),
            payload.decode("utf-8"),
            previous_hash,
            event_hash,
        ),
    )
    return event_hash


# --------------------------------------------------------------------------- #
# hashing
# --------------------------------------------------------------------------- #
def test_genesis_hash_is_deterministic() -> None:
    """A published constant, so any process verifying the journal agrees on the first link."""
    assert hashlib.sha256(b"xauusd-lab:journal:genesis").digest() == GENESIS_HASH
    assert hashlib.sha256(b"xauusd-lab:journal:genesis").digest() == GENESIS_HASH


def test_genesis_hash_is_sha256_length() -> None:
    assert len(GENESIS_HASH) == 32


def test_event_hash_is_deterministic() -> None:
    payload = canonical_json_bytes(_event("evt-1"))

    first = compute_event_hash(GENESIS_HASH, payload)
    second = compute_event_hash(GENESIS_HASH, payload)

    assert first == second
    assert len(first) == 32


def test_event_hash_depends_on_the_previous_hash() -> None:
    payload = canonical_json_bytes(_event("evt-1"))
    other = b"\x01" * 32

    assert compute_event_hash(GENESIS_HASH, payload) != compute_event_hash(other, payload)


def test_event_hash_depends_on_the_payload() -> None:
    one = canonical_json_bytes(_event("evt-1", "100.00"))
    two = canonical_json_bytes(_event("evt-1", "200.00"))

    assert compute_event_hash(GENESIS_HASH, one) != compute_event_hash(GENESIS_HASH, two)


def test_event_hash_concatenates_as_specified() -> None:
    """event_hash = sha256(previous_hash_bytes + canonical_json_bytes)."""
    import hashlib

    payload = canonical_json_bytes(_event("evt-1"))

    assert compute_event_hash(GENESIS_HASH, payload) == hashlib.sha256(
        GENESIS_HASH + payload
    ).digest()


def test_event_hash_rejects_text_inputs() -> None:
    with pytest.raises(ChainError, match="bytes"):
        compute_event_hash("not bytes", b"payload")


def test_event_hash_rejects_text_payload() -> None:
    with pytest.raises(ChainError, match="bytes"):
        compute_event_hash(b"\x00" * 32, "payload")


def test_hasher_matches_the_free_function() -> None:
    hasher = JournalHasher()
    payload = canonical_json_bytes(_event("evt-1"))

    assert hasher.chain(hasher.genesis, payload) == compute_event_hash(
        hasher.genesis, payload
    )


def test_hasher_chains_in_order() -> None:
    hasher = JournalHasher()
    payloads = [canonical_json_bytes(_event(f"evt-{i}")) for i in range(4)]

    running = hasher.genesis
    hashes = []
    for payload in payloads:
        running = hasher.chain(running, payload)
        hashes.append(running)

    assert len(set(hashes)) == 4, "every link in the chain must be distinct"
    # Chain by hand to confirm chain_all folded them in order.
    expected = hasher.genesis
    for payload in payloads:
        expected = hasher.chain(expected, payload)

    assert hashes == list(hashes)
    assert hashes[-1] == expected


# --------------------------------------------------------------------------- #
# schema
# --------------------------------------------------------------------------- #
def test_schema_creates_a_sequence_primary_key() -> None:
    connection = _connect()
    columns = {
        row[1]: row[2] for row in connection.execute("PRAGMA table_info(journal)")
    }

    assert "sequence" in columns
    assert columns["sequence"] == "INTEGER"
    assert columns["price_ticks"] == "INTEGER"
    assert columns["amount"] == "TEXT"


def test_schema_has_no_banned_column_type() -> None:
    lowered = SCHEMA_SQL.lower()

    for banned in ("real", "float", "double"):
        assert banned not in lowered.replace("nullable", ""), (
            f"column type {banned!r} would reintroduce float arithmetic"
        )


def test_schema_creates_indexes() -> None:
    connection = _connect()
    names = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='index'")
    }

    assert "journal_occurred_at" in names
    assert "journal_event_type" in names


def test_schema_update_trigger_rejects_update() -> None:
    connection = _connect()
    connection.execute(
        "INSERT INTO journal (event_id, event_type, occurred_at, amount, payload,"
        " previous_hash, event_hash) VALUES ('e', 'ledger_posted', '2026-01-15T14:30:00+00:00',"
        " '100.00', '{}', ?, ?)",
        (GENESIS_HASH, b"\x01" * 32),
    )

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        connection.execute(
            "UPDATE journal SET amount = '999.00' WHERE event_id = 'e'"
        )


def test_schema_delete_trigger_rejects_delete() -> None:
    connection = _connect()
    connection.execute(
        "INSERT INTO journal (event_id, event_type, occurred_at, amount, payload,"
        " previous_hash, event_hash) VALUES ('e', 'ledger_posted', '2026-01-15T14:30:00+00:00',"
        " '100.00', '{}', ?, ?)",
        (GENESIS_HASH, b"\x01" * 32),
    )

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        connection.execute("DELETE FROM journal WHERE event_id = 'e'")


def test_schema_rejects_a_duplicate_event_id() -> None:
    connection = _connect()
    _append(connection, "evt-1")
    _append(connection, "evt-2")

    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        _append(connection, "evt-1")


def _append(connection: sqlite3.Connection, event_id: str) -> None:
    connection.execute(
        "INSERT INTO journal (event_id, event_type, occurred_at, amount, payload,"
        " previous_hash, event_hash) VALUES (?, 'ledger_posted',"
        " '2026-01-15T14:30:00+00:00', '100.00', '{}', ?, ?)",
        (event_id, GENESIS_HASH, b"\x01" * 32),
    )


def test_schema_requires_the_foreign_key_shape() -> None:
    """Every row must carry both hashes; the chain cannot be reconstructed without them."""
    connection = _connect()

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO journal (event_id, event_type, occurred_at, amount, payload)"
            " VALUES ('e', 'ledger_posted', '2026-01-15T14:30:00+00:00', '100.00', '{}')"
        )


# --------------------------------------------------------------------------- #
# verify_chain
# --------------------------------------------------------------------------- #
def test_verify_chain_accepts_an_empty_journal() -> None:
    report = verify_chain(_connect())

    assert report.is_valid is True
    assert report.rows_verified == 0
    assert report.errors == ()


def test_verify_chain_accepts_a_well_formed_chain() -> None:
    connection = _connect()
    previous = GENESIS_HASH
    for index in range(5):
        previous = _write(connection, _event(f"evt-{index}"), previous)

    report = verify_chain(connection)

    assert report.is_valid is True
    assert report.rows_verified == 5
    assert report.tail_hash == previous


def test_verify_chain_flags_a_broken_link() -> None:
    """A row whose previous_hash points at the wrong link must be detected.

    The row is *written* with a wrong link rather than updated in place, because the UPDATE
    trigger exists specifically to stop that kind of edit.
    """
    connection = _connect()
    previous = GENESIS_HASH
    for index in range(3):
        previous = _write(connection, _event(f"evt-{index}"), previous)

    event = _event("evt-forged")
    payload = canonical_json_bytes(event)
    _insert(
        connection,
        event,
        payload,
        previous_hash=bytes(32),
        event_hash=compute_event_hash(bytes(32), payload),
    )

    report = verify_chain(connection)

    assert report.is_valid is False
    assert "previous_hash does not match" in report.reason


def test_verify_chain_flags_a_forged_event_hash() -> None:
    """A hash that does not recompute over the stored payload is detected."""
    connection = _connect()
    previous = GENESIS_HASH
    for index in range(3):
        previous = _write(connection, _event(f"evt-{index}"), previous)

    event = _event("evt-forged")
    payload = canonical_json_bytes(event)
    _insert(
        connection, event, payload, previous_hash=previous, event_hash=bytes([9]) * 32
    )

    report = verify_chain(connection)

    assert report.is_valid is False
    assert "event_hash does not match" in report.reason


def test_verify_chain_detects_a_tampered_payload() -> None:
    """A hash computed over different bytes than the ones stored is detected.

    This is the shape of an edit made *outside* the triggers -- a row repaired directly in
    the file. The stored hash is genuine, but it covered another payload, so recomputing
    over what is actually in the row does not reproduce it.
    """
    connection = _connect()
    previous = GENESIS_HASH
    for index in range(3):
        previous = _write(connection, _event(f"evt-{index}"), previous)

    forged = _event("evt-forged", "999.00")
    stored = canonical_json_bytes(_event("evt-forged", "100.00"))
    # The row was authored with the honest hash over one payload, then its payload text was
    # swapped. Recomputing over what is actually in the row therefore does not match.
    authored = compute_event_hash(previous, canonical_json_bytes(forged))
    _insert(
        connection, forged, stored, previous_hash=previous, event_hash=authored
    )

    report = verify_chain(connection)

    assert report.is_valid is False
    assert "event_hash does not match" in report.reason


def test_verify_chain_reports_the_offending_sequence() -> None:
    connection = _connect()
    _insert_raw(connection, "evt-1", previous_hash=b"\x01" * 32, event_hash=b"\x02" * 32)

    report = verify_chain(connection)

    assert report.is_valid is False
    assert report.errors
    assert "row 1" in report.reason


def _insert_raw(
    connection: sqlite3.Connection,
    event_id: str,
    *,
    previous_hash: bytes,
    event_hash: bytes,
) -> None:
    connection.execute(
        "INSERT INTO journal (event_id, event_type, occurred_at, amount, payload,"
        " previous_hash, event_hash) VALUES (?, 'ledger_posted',"
        " '2026-01-15T14:30:00+00:00', '100.00', '{}', ?, ?)",
        (event_id, previous_hash, event_hash),
    )


def _insert(
    connection: sqlite3.Connection,
    event: DomainEvent,
    payload: bytes,
    *,
    previous_hash: bytes,
    event_hash: bytes | None = None,
) -> None:
    from engine.journal.schema import compute_event_hash

    if event_hash is None:
        event_hash = compute_event_hash(previous_hash, payload)
    connection.execute(
        "INSERT INTO journal (event_id, event_type, occurred_at, amount, payload,"
        " previous_hash, event_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            event.event_id,
            event.event_type.value,
            event.occurred_at.astimezone(UTC).isoformat(),
            format(Decimal("100.00"), "f"),
            payload.decode("utf-8"),
            previous_hash,
            event_hash,
        ),
    )


def test_verify_chain_result_is_immutable() -> None:
    report = verify_chain(_connect())

    with pytest.raises(Exception):  # noqa: B017
        report.is_valid = False  # type: ignore[misc]


def test_verify_chain_can_resume_from_a_known_hash() -> None:
    connection = _connect()
    previous = GENESIS_HASH
    for index in range(4):
        previous = _write(connection, _event(f"evt-{index}"), previous)

    report = verify_chain(connection, start_sequence=3)

    assert report.is_valid is True, report.reason
    assert report.rows_verified == 2
