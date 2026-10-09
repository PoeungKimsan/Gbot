"""Unit tests for :mod:`engine.domain.events`.

The invariant under test is *canonicalization*: an event must serialize to exactly one
byte sequence, never a float, and never depend on dict ordering. Everything downstream --
the SHA-256 hash chain, the journal row, and replay -- depends on that property holding.
"""

import datetime as dt
import json
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

import pytest

from engine.domain.events import (
    Canonicalizer,
    CanonicalJSONError,
    DomainEvent,
    EventType,
    canonical_json_bytes,
    canonical_json_text,
    decode_dataclass,
)

UTC = dt.UTC


class Payload(StrEnum):
    THING = "thing"


@dataclass(frozen=True, slots=True)
class Sample:
    name: str
    amount: Decimal
    when: dt.datetime
    tags: tuple[str, ...] = ()


def _sample() -> Sample:
    return Sample(
        name="xauusd",
        amount=Decimal("2038.25"),
        when=dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC),
        tags=("a", "b"),
    )


# --------------------------------------------------------------------------- #
# determinism
# --------------------------------------------------------------------------- #
def test_canonical_json_is_deterministic() -> None:
    first = canonical_json_bytes(_sample())
    second = canonical_json_bytes(_sample())

    assert first == second


def test_canonical_json_sorts_mapping_keys() -> None:
    """Two dicts with the same items in different insertion order must serialize alike."""
    forward = {"b": 1, "a": 2}
    reverse = {"a": 2, "b": 1}

    assert canonical_json_bytes(forward) == canonical_json_bytes(reverse)


def test_canonical_json_uses_no_whitespace() -> None:
    text = canonical_json_text({"a": 1, "b": 2})

    assert text == '{"a":1,"b":2}'


def test_canonical_json_is_ascii_only() -> None:
    text = canonical_json_text({"note": "caf\u00e9 \u2014 gold"})

    assert "caf\\u00e9" in text
    assert text.isascii()


# --------------------------------------------------------------------------- #
# type handling
# --------------------------------------------------------------------------- #
def test_decimal_serializes_as_plain_string() -> None:
    """A Decimal must never be widened into a float by the JSON encoder."""
    text = canonical_json_text({"amount": Decimal("2038.2500")})

    assert text == '{"amount":"2038.2500"}'
    assert json.loads(text)["amount"] == "2038.2500"


def test_decimal_preserves_trailing_zeros() -> None:
    """`format(d, 'f')` keeps the scale, which is what makes the hash stable."""
    assert canonical_json_text(Decimal("1.50")) == '"1.50"'
    assert canonical_json_text(Decimal("1.500")) == '"1.500"'


def test_decimal_sort_key_is_the_string_form() -> None:
    """Sorting the string form keeps Decimals with different scales in a stable order."""
    assert canonical_json_text({"a": Decimal(3), "b": Decimal(1)}) == (
        '{"a":"3","b":"1"}'
    )


def test_float_is_rejected() -> None:
    """A float anywhere in the payload must fail loudly rather than serialize lossily."""
    with pytest.raises(CanonicalJSONError, match="float"):
        canonical_json_text({"amount": 2038.25})


def test_nan_and_infinity_decimals_are_rejected() -> None:
    for bad in (Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")):
        with pytest.raises(CanonicalJSONError):
            canonical_json_text({"amount": bad})


def test_aware_datetime_normalises_to_utc_iso() -> None:
    from zoneinfo import ZoneInfo

    new_york = ZoneInfo("America/New_York")
    local = dt.datetime(2026, 1, 15, 9, 30, tzinfo=new_york)

    assert canonical_json_text(local) == '"2026-01-15T14:30:00+00:00"'


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(CanonicalJSONError, match="timezone-aware"):
        canonical_json_text(dt.datetime(2026, 1, 15, 14, 30))


def test_date_serializes_as_iso() -> None:
    assert canonical_json_text(dt.date(2026, 1, 15)) == '"2026-01-15"'


def test_enum_serializes_as_its_value() -> None:
    assert canonical_json_text(Payload.THING) == '"thing"'


def test_tuple_serializes_as_json_array() -> None:
    assert canonical_json_text({"tags": ("a", "b")}) == '{"tags":["a","b"]}'


def test_dataclass_serializes_field_by_field() -> None:
    text = canonical_json_text(_sample())

    parsed = json.loads(text)
    assert parsed == {
        "name": "xauusd",
        "amount": "2038.25",
        "when": "2026-01-15T14:30:00+00:00",
        "tags": ["a", "b"],
    }


def test_bytes_serialize_as_hex() -> None:
    assert canonical_json_text(b"\xde\xad\xbe\xef") == '"deadbeef"'


def test_unsupported_type_is_rejected() -> None:
    with pytest.raises(CanonicalJSONError, match="unsupported"):
        canonical_json_text(object())


def test_unsupported_mapping_key_is_rejected() -> None:
    with pytest.raises(CanonicalJSONError):
        canonical_json_text({1: "value"})


def test_nested_dataclasses_and_decimals() -> None:
    @dataclass(frozen=True, slots=True)
    class Inner:
        price: Decimal

    @dataclass(frozen=True, slots=True)
    class Outer:
        inner: Inner
        label: str

    text = canonical_json_text(Outer(inner=Inner(price=Decimal("0.01")), label="z"))

    assert json.loads(text) == {"inner": {"price": "0.01"}, "label": "z"}


def test_negative_zero_round_trips_deterministically() -> None:
    assert canonical_json_text(Decimal("-0.00")) == '"-0.00"'


# --------------------------------------------------------------------------- #
# DomainEvent
# --------------------------------------------------------------------------- #
def _event(**overrides: object) -> DomainEvent:
    defaults: dict[str, object] = {
        "event_id": "evt-1",
        "event_type": EventType.LEDGER_POSTED,
        "occurred_at": dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC),
        "payload": {"amount": Decimal("100.00"), "account": "CASH"},
    }
    defaults.update(overrides)
    return DomainEvent(**defaults)  # type: ignore[arg-type]


def test_event_is_frozen() -> None:
    event = _event()

    with pytest.raises(Exception):  # noqa: B017
        event.event_id = "other"  # type: ignore[misc]


def test_event_is_hashable() -> None:
    assert hash(_event()) == hash(_event())


def test_event_requires_an_aware_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _event(occurred_at=dt.datetime(2026, 1, 15, 14, 30))


def test_event_rejects_a_blank_event_id() -> None:
    with pytest.raises(ValueError, match="event_id"):
        _event(event_id="   ")


@pytest.mark.parametrize("bad", ["", "   ", "not an event type"])
def test_event_rejects_an_unknown_type(bad: str) -> None:
    with pytest.raises(ValueError, match="event_type"):
        _event(event_type=bad)


def test_event_rejects_a_float_payload() -> None:
    with pytest.raises(CanonicalJSONError, match="float"):
        _event(payload={"amount": 1.5}).canonical_bytes()


def test_event_canonical_bytes_exclude_the_hash_fields() -> None:
    """Adding a hash must not change the canonical bytes it is computed over."""
    event = _event()

    expected = canonical_json_text(
        {
            "event_id": event.event_id,
            "event_type": event.event_type.value,
            "occurred_at": event.occurred_at,
            "payload": event.payload,
        }
    )

    assert event.canonical_text() == expected


def test_event_equality_is_content_based() -> None:
    assert _event() == _event()
    assert _event() != _event(event_id="evt-2")


def test_with_payload_returns_a_new_event() -> None:
    event = _event()
    changed = event.with_payload({"amount": Decimal("5")})

    assert event.payload["amount"] == Decimal("100.00")
    assert changed.payload["amount"] == Decimal("5")
    assert changed.event_id == event.event_id


def test_every_event_type_has_a_wire_value() -> None:
    values = [member.value for member in EventType]

    assert len(values) == len(set(values)), "wire values must be unique"
    assert all(isinstance(v, str) and v for v in values)


def test_event_type_from_value() -> None:
    assert EventType.from_value("ledger_posted") is EventType.LEDGER_POSTED
    assert EventType.from_value("LEDGER_POSTED") is EventType.LEDGER_POSTED
    assert EventType.from_value("nonsense") is None
    assert EventType.from_value(None) is None


def test_event_types_cover_the_domain_actions() -> None:
    """Each phase-2 action needs an event type, or it cannot be journalled."""
    for member in (
        EventType.ORDER_SUBMITTED,
        EventType.ORDER_ACKNOWLEDGED,
        EventType.FILL_REPORTED,
        EventType.POSITION_OPENED,
        EventType.POSITION_CLOSED,
        EventType.LEDGER_POSTED,
        EventType.EQUITY_SNAPSHOT,
        EventType.SLIPPAGE_REALIZED,
        EventType.RUN_STARTED,
        EventType.RUN_COMPLETED,
    ):
        assert isinstance(member, EventType)


# --------------------------------------------------------------------------- #
# Canonicalizer / decode_dataclass
# --------------------------------------------------------------------------- #
def test_canonicalizer_roundtrips_a_dataclass() -> None:
    codec = Canonicalizer(Sample)
    sample = _sample()

    restored = codec.decode(codec.encode(sample))

    assert restored == sample


def test_canonicalizer_encode_decode_is_deterministic() -> None:
    codec = Canonicalizer(Sample)

    first = codec.encode(_sample())
    second = codec.encode(_sample())

    assert first == second
    assert codec.decode(first) == codec.decode(second)


def test_canonicalizer_rejects_a_float_before_encoding() -> None:
    @dataclass(frozen=True, slots=True)
    class WithFloat:
        price: float

    codec = Canonicalizer(WithFloat)

    with pytest.raises(CanonicalJSONError):
        codec.encode(WithFloat(price=1.5))


def test_decode_dataclass_rejects_unknown_fields() -> None:
    @dataclass(frozen=True, slots=True)
    class Exact:
        a: int

    with pytest.raises(CanonicalJSONError, match="unknown field"):
        decode_dataclass(Exact, {"a": 1, "b": 2})


def test_decode_dataclass_rejects_missing_fields() -> None:
    @dataclass(frozen=True, slots=True)
    class Required:
        a: int
        b: str

    with pytest.raises(CanonicalJSONError, match="missing field"):
        decode_dataclass(Required, {"a": 1})


def test_decode_dataclass_rebuilds_decimals() -> None:
    @dataclass(frozen=True, slots=True)
    class Priced:
        amount: Decimal

    restored = decode_dataclass(Priced, {"amount": "2038.25"})

    assert restored.amount == Decimal("2038.25")
    assert isinstance(restored.amount, Decimal)


def test_canonicalizer_preserves_decimal_scale() -> None:
    @dataclass(frozen=True, slots=True)
    class Priced:
        amount: Decimal

    codec = Canonicalizer(Priced)
    encoded = codec.encode(Priced(amount=Decimal("1.500")))

    assert json.loads(encoded)["amount"] == "1.500"
    assert codec.decode(encoded).amount == Decimal("1.500")
