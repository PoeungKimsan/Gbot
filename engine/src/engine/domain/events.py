"""Immutable domain events and their canonical serialization.

The whole journal depends on one property: **an event must serialize to exactly one byte
sequence.** If any two runs of the same event produced different bytes, the SHA-256 hash
chain would break and ``verify_chain`` would scream about tampering that never happened.

That forces three rules, enforced here rather than left to convention:

1. **Never a float.** ``json.dumps`` happily accepts a float and emits ``2038.25``; the
   reader then reconstructs ``2038.2499999999999727``. Floats are rejected outright, so a
   price can only arrive as a ``Decimal`` or as an integer tick count.
2. **Never a naive datetime.** A naive datetime serializes with no offset, and two
   producers in different zones would produce identical bytes for different instants. Every
   timestamp is normalized to UTC first.
3. **Never an unordered map.** Keys are sorted, separators are fixed, and the output is
   ASCII-only, so the bytes are independent of insertion order and host locale.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import types
import typing
from collections.abc import Mapping
from decimal import Decimal
from enum import StrEnum
from typing import Any, TypeVar

__all__ = [
    "CanonicalJSONError",
    "Canonicalizer",
    "DomainEvent",
    "EventType",
    "canonical_json_bytes",
    "canonical_json_text",
    "decode_dataclass",
]

T = TypeVar("T")


class CanonicalJSONError(ValueError):
    """A value cannot be canonicalized, usually because it carries a float."""


class EventType(StrEnum):
    """Every journalled domain action.

    Wire values are the lowercase form and are stored verbatim in the ``journal`` table, so
    a rename is a schema change and must be treated as one.
    """

    RUN_STARTED = "run_started"
    RUN_COMPLETED = "run_completed"
    INSTRUMENT_LOADED = "instrument_loaded"
    ORDER_SUBMITTED = "order_submitted"
    ORDER_ACKNOWLEDGED = "order_acknowledged"
    ORDER_CANCELLED = "order_cancelled"
    FILL_REPORTED = "fill_reported"
    SLIPPAGE_REALIZED = "slippage_realized"
    POSITION_OPENED = "position_opened"
    POSITION_INCREASED = "position_increased"
    POSITION_REDUCED = "position_reduced"
    POSITION_CLOSED = "position_closed"
    LEDGER_POSTED = "ledger_posted"
    EQUITY_SNAPSHOT = "equity_snapshot"
    FEED_STATE_CHANGED = "feed_state_changed"
    RISK_KILLSWITCH = "risk_killswitch"

    @classmethod
    def from_value(cls, value: Any) -> EventType | None:
        """Parse a wire value, returning ``None`` for anything unrecognized."""
        if not isinstance(value, str):
            return None
        try:
            return cls(value.strip().lower())
        except ValueError:
            return None


@dataclasses.dataclass(frozen=True, slots=True)
class DomainEvent:
    """One immutable fact that happened, once.

    ``payload`` carries the action-specific body. It is deliberately an open mapping rather
    than a typed subclass hierarchy: the journal must persist and replay events written by
    an older version of the engine, and a rigid schema would turn a version skew into a
    crash. Validation happens at canonicalization time instead.
    """

    event_id: str
    event_type: EventType
    occurred_at: dt.datetime
    payload: dict[str, Any] = dataclasses.field(default_factory=dict)

    def __hash__(self) -> int:
        """Hash on identity, not on the payload.

        ``payload`` is an open mapping and therefore unhashable, but events are used as dict
        keys during replay. Hashing the identity fields keeps that working; equality still
        distinguishes two events that share an id but differ in body, so a hash collision is
        merely a collision and not a false match.

        Built from the event's own hash-chain digest rather than the builtin ``hash()``,
        which is salted per process and would give a different value on every run.
        """
        digest = hashlib.sha256(self.canonical_bytes()).digest()
        return int.from_bytes(digest[:8], "big")

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, str) or not self.event_id.strip():
            raise ValueError(f"event_id must be a non-empty string, got {self.event_id!r}")
        if not isinstance(self.event_type, EventType):
            raise ValueError(
                f"event_type must be an EventType, got {type(self.event_type).__name__}"
            )
        if not isinstance(self.occurred_at, dt.datetime):
            raise ValueError(
                f"occurred_at must be a datetime, got {type(self.occurred_at).__name__}"
            )
        if self.occurred_at.tzinfo is None:
            raise ValueError(
                f"occurred_at must be timezone-aware, got {self.occurred_at!r}"
            )
        if not isinstance(self.payload, dict):
            raise ValueError(
                f"payload must be a mapping, got {type(self.payload).__name__}"
            )

    @property
    def occurred_at_utc(self) -> dt.datetime:
        """The timestamp in UTC, which is the only form the journal stores."""
        return self.occurred_at.astimezone(dt.UTC)

    def canonical_body(self) -> dict[str, Any]:
        """The content the hash commits over: everything except the hash itself.

        Order is irrelevant here because canonicalization sorts keys; the mapping simply
        names the parts that define the event.
        """
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "occurred_at": self.occurred_at_utc,
            "payload": self.payload,
        }

    def canonical_text(self) -> str:
        """Canonical JSON text of this event's body."""
        return canonical_json_text(self.canonical_body())

    def canonical_bytes(self) -> bytes:
        """Canonical JSON bytes of this event's body. This is what gets hashed."""
        return canonical_json_bytes(self.canonical_body())

    def with_payload(self, payload: Mapping[str, Any]) -> DomainEvent:
        """Return a copy with a different payload, leaving the original untouched."""
        return DomainEvent(
            event_id=self.event_id,
            event_type=self.event_type,
            occurred_at=self.occurred_at,
            payload=dict(payload),
        )

    def with_event_id(self, event_id: str) -> DomainEvent:
        return DomainEvent(
            event_id=event_id,
            event_type=self.event_type,
            occurred_at=self.occurred_at,
            payload=dict(self.payload),
        )


# --------------------------------------------------------------------------- #
# canonicalization
# --------------------------------------------------------------------------- #
def _normalize(value: Any) -> Any:
    """Convert ``value`` into a JSON-safe structure with exactly one representation."""
    # bool before int: bool is a subclass of int, and True must not become 1.
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal):
        # format(d, 'f') keeps the scale exactly, including trailing zeros. str(d) would
        # emit scientific notation for small exponents, which changes the bytes.
        if not value.is_finite():
            raise CanonicalJSONError(f"non-finite Decimal cannot be canonicalized: {value!r}")
        return format(value, "f")
    if isinstance(value, float):
        raise CanonicalJSONError(
            f"float values are prohibited in domain events, got {value!r}; "
            "use Decimal or an integer tick count"
        )
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            raise CanonicalJSONError(
                f"datetime must be timezone-aware, got {value!r}"
            )
        return value.astimezone(dt.UTC).isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return _normalize(value.value)
    if isinstance(value, bytes):
        return value.hex()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _normalize(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, Mapping):
        return {_mapping_key(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    raise CanonicalJSONError(
        f"unsupported type {type(value).__name__} in a canonical payload"
    )


def _mapping_key(key: Any) -> str:
    """Normalize a mapping key to a string, rejecting non-string keys.

    An int key is rejected rather than stringified: JSON turns object keys into strings, so
    ``{1: "a"}`` and ``{"1": "a"}`` would canonicalize identically and two different events
    could share a hash.
    """
    if isinstance(key, str):
        return key
    if isinstance(key, StrEnum):
        return str(key.value)
    raise CanonicalJSONError(
        f"mapping keys must be strings, got {type(key).__name__}"
    )


def canonical_json_bytes(value: Any) -> bytes:
    """Canonical JSON bytes for ``value``.

    Sorted keys, no whitespace, ASCII escaping, and NaN/Infinity rejected, so the result is
    a pure function of ``value``.
    """
    return canonical_json_text(value).encode("utf-8")


def canonical_json_text(value: Any) -> str:
    """Canonical JSON text for ``value``. See :func:`canonical_json_bytes`."""
    try:
        return json.dumps(
            _normalize(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except CanonicalJSONError:
        raise
    except (TypeError, ValueError) as exc:
        raise CanonicalJSONError(f"value cannot be canonicalized: {exc}") from exc


@dataclasses.dataclass(frozen=True, slots=True)
class Canonicalizer:
    """Encode/decode round-trip for a dataclass, used by replay and tests.

    Enums and Decimals survive the trip; floats never get in at all. The field set is fixed
    by the dataclass, so an unknown field is a schema change rather than silently ignored
    data.
    """

    model: type

    def encode(self, value: Any) -> bytes:
        if not isinstance(value, self.model):
            raise CanonicalJSONError(
                f"expected {self.model.__name__}, got {type(value).__name__}"
            )
        return canonical_json_bytes(value)

    def decode(self, data: bytes | str) -> Any:
        text = data.decode("utf-8") if isinstance(data, bytes) else data
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise CanonicalJSONError(f"payload is not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise CanonicalJSONError(f"payload must be an object, got {type(parsed).__name__}")
        return decode_dataclass(self.model, parsed)


def decode_dataclass(model: type, parsed: Mapping[str, Any]) -> Any:
    """Rebuild ``model`` from a canonical mapping, validating the field set.

    Decimal fields are reconstructed from their string form; every other type is restored
    from its JSON scalar. This is the only place a stored event becomes a Python object
    again, so it is also where a schema drift is caught.
    """
    if not isinstance(parsed, Mapping):
        raise CanonicalJSONError(
            f"expected a mapping to build {model.__name__}, got {type(parsed).__name__}"
        )

    expected = {f.name: f for f in dataclasses.fields(model)}
    unknown = set(parsed) - set(expected)
    if unknown:
        raise CanonicalJSONError(f"unknown field(s) for {model.__name__}: {sorted(unknown)}")

    missing = set(expected) - set(parsed)
    if missing:
        raise CanonicalJSONError(f"missing field(s) for {model.__name__}: {sorted(missing)}")

    resolved = _resolved_hints(model)
    kwargs: dict[str, Any] = {
        name: _restore(parsed[name], resolved.get(name, f.type))
        for name, f in expected.items()
    }

    return model(**kwargs)


def _resolved_hints(model: type) -> dict[str, Any]:
    """Best-effort resolution of a model's annotations to real types.

    ``from __future__ import annotations`` stores annotations as strings, so a ``Decimal``
    field would otherwise be indistinguishable from a plain string field and would decode
    into a ``str``. ``get_type_hints`` resolves them; when it fails, the raw string
    annotations are used and ``_restore`` falls back to its string handling.
    """
    hints = getattr(model, "__annotations__", {})
    try:
        return dict(typing.get_type_hints(model))
    except Exception:
        return dict(hints)


def _restore(value: Any, annotation: Any) -> Any:
    """Coerce a parsed JSON value back to the type the field declared.

    Only the types that survive canonicalization are handled: ``Decimal``, ``datetime``,
    ``date``, tuples, and enums. Anything else is already the right JSON scalar (``str``,
    ``int``, ``bool``, ``None``) and is returned unchanged.
    """
    if annotation is None or value is None:
        return value

    # A string annotation is a name, not a type; only Decimal is unambiguous enough to
    # resolve by name. Everything else falls through as the parsed scalar.
    if isinstance(annotation, str):
        if annotation in {"Decimal", "decimal.Decimal"}:
            return _parse_decimal(value)
        return value

    origin = typing.get_origin(annotation)

    if origin is typing.Union or origin is types.UnionType:
        for member in typing.get_args(annotation):
            if member is type(None):
                if value is None:
                    return None
                continue
            return _restore(value, member)
        return value

    if annotation is Decimal:
        return _parse_decimal(value)
    if annotation is dt.datetime:
        return _parse_datetime(value)
    if annotation is dt.date:
        return _parse_date(value)
    if annotation is dt.timedelta:
        return _parse_timedelta(value)
    if isinstance(annotation, type) and issubclass(annotation, StrEnum):
        return annotation(value)
    if origin in (tuple, list):
        args = typing.get_args(annotation)
        if not args:
            return tuple(value)
        return tuple(_restore(item, args[0]) for item in value)

    return value


def _parse_timedelta(value: Any) -> dt.timedelta:
    if isinstance(value, dt.timedelta):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return dt.timedelta(seconds=value)
    raise CanonicalJSONError(
        f"timedelta field must be an int of seconds, got {type(value).__name__}"
    )


def _parse_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, str):
        try:
            return Decimal(value)
        except Exception as exc:
            raise CanonicalJSONError(f"not a Decimal: {value!r}") from exc
    if isinstance(value, int) and not isinstance(value, bool):
        return Decimal(value)
    raise CanonicalJSONError(
        f"Decimal field must be a string, got {type(value).__name__}"
    )


def _parse_datetime(value: Any) -> dt.datetime:
    if not isinstance(value, str):
        raise CanonicalJSONError(
            f"datetime field must be a string, got {type(value).__name__}"
        )
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError as exc:
        raise CanonicalJSONError(f"not a datetime: {value!r}") from exc
    if parsed.tzinfo is None:
        raise CanonicalJSONError(f"datetime field has no offset: {value!r}")
    return parsed.astimezone(dt.UTC)


def _parse_date(value: Any) -> dt.date:
    if not isinstance(value, str):
        raise CanonicalJSONError(
            f"date field must be a string, got {type(value).__name__}"
        )
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise CanonicalJSONError(f"not a date: {value!r}") from exc
