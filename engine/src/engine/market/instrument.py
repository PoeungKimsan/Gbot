"""Instrument specification: the single source of tick and pip sizes.

Tick and pip sizes are **never** hardcoded in engine logic. They are derived from the two
integers OANDA publishes for each instrument:

* ``pip_location`` -- decimal exponent of a pip (``-4`` for XAU_USD, so a pip is 0.0001)
* ``display_precision`` -- decimal digits shown to traders (2 for XAU_USD, so the tick is 0.01)

Every price in the engine is a :class:`~decimal.Decimal` quantized against these values, so a
mistake here would silently corrupt every downstream calculation. The sizes are therefore
loaded from the OANDA API, cached on disk, and verified for exactness on construction.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import yaml

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "CURRENT_INSTRUMENT",
    "InstrumentSpec",
    "InstrumentSpecCache",
    "InstrumentSpecError",
    "InstrumentSpecLoader",
]

#: The instrument this engine trades. Everything else in the codebase resolves sizes
#: through an :class:`InstrumentSpec` rather than through this constant.
CURRENT_INSTRUMENT: Final[str] = "XAU_USD"

#: Highest decimal precision an exchange realistically publishes. Keeps the exact-power
#: assertion in :meth:`InstrumentSpec._validate_derived_sizes` inside sane bounds.
_MAX_PRECISION: Final[int] = 8

#: Cache schema version. Bump when the on-disk shape changes; old files are rejected
#: rather than misread, so a stale cache can never feed wrong sizes into a live run.
_CACHE_SCHEMA: Final[int] = 1

#: Wire-shape field names written by :meth:`InstrumentSpec.to_oanda_payload`.
_OANDA_FIELDS: Final[tuple[str, ...]] = (
    "name",
    "pipLocation",
    "displayPrecision",
    "tradeUnitsPrecision",
    "minimumTradeSize",
)

#: Complete set of keys a valid cache entry may contain. Anything else is a foreign file.
_CACHE_FIELDS: Final[tuple[str, ...]] = (*_OANDA_FIELDS, "fetched_at_utc", "schema")


class InstrumentSpecError(ValueError):
    """An instrument spec is malformed, undeliverable, or cached unreadably."""


@dataclass(frozen=True, slots=True)
class InstrumentSpec:
    """Tick geometry for one tradable instrument.

    All size fields arrive from the OANDA ``/v3/instruments`` endpoint. Nothing in this
    class may be substituted with a literal ``Decimal("0.01")`` anywhere downstream.
    """

    name: str
    pip_location: int
    display_precision: int
    trade_units_precision: int
    minimum_trade_size: Decimal

    def __post_init__(self) -> None:
        self._validate_inputs()
        self._validate_derived_sizes()

    # -- validation ---------------------------------------------------------- #
    def _validate_inputs(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise InstrumentSpecError(
                f"instrument name must be a non-empty string, got {self.name!r}"
            )

        for field in ("pip_location", "display_precision", "trade_units_precision"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int):
                raise InstrumentSpecError(f"{field} must be an int, got {type(value).__name__}")

        if not 0 <= self.display_precision <= _MAX_PRECISION:
            raise InstrumentSpecError(
                f"display_precision must be between 0 and {_MAX_PRECISION}, "
                f"got {self.display_precision}"
            )
        if not 0 <= self.trade_units_precision <= _MAX_PRECISION:
            raise InstrumentSpecError(
                f"trade_units_precision must be between 0 and {_MAX_PRECISION}, "
                f"got {self.trade_units_precision}"
            )
        if self.pip_location > 0:
            raise InstrumentSpecError(f"pip_location must be <= 0, got {self.pip_location}")
        if not isinstance(self.minimum_trade_size, Decimal):
            raise InstrumentSpecError(
                f"minimum_trade_size must be Decimal, got {type(self.minimum_trade_size).__name__}"
            )
        if not self.minimum_trade_size.is_finite() or self.minimum_trade_size <= 0:
            raise InstrumentSpecError(
                f"minimum_trade_size must be a positive finite Decimal, "
                f"got {self.minimum_trade_size}"
            )

    def _validate_derived_sizes(self) -> None:
        """Fail loudly rather than let a rounded power reach price math.

        ``Decimal(10) ** -n`` is only exact while ``n`` stays under the context precision.
        If that ever changes, the engine must stop here instead of quantizing millions of
        prices against a silently rounded tick.

        Note that no ordering between tick and pip is asserted: the relationship is
        venue-specific. XAU_USD quotes at 0.01 with a 0.0001 pip, while a typical FX pair
        does the opposite. Assuming either direction would be wrong for half the book.
        """
        tick = self.tick
        pip = self.pip
        if tick.as_tuple().exponent != -self.display_precision:
            raise InstrumentSpecError(
                f"tick {tick} for display_precision {self.display_precision} is not exact"
            )
        if pip.as_tuple().exponent != self.pip_location:
            raise InstrumentSpecError(
                f"pip {pip} for pip_location {self.pip_location} is not exact"
            )

    # -- derived sizes ------------------------------------------------------- #
    @property
    def tick(self) -> Decimal:
        """Smallest quoteable price increment."""
        return Decimal(10) ** -self.display_precision

    @property
    def pip(self) -> Decimal:
        """Size of one pip, from the venue-published ``pip_location``."""
        return Decimal(10) ** self.pip_location

    # -- OANDA payload ------------------------------------------------------- #
    @classmethod
    def from_oanda_payload(cls, payload: dict[str, Any]) -> InstrumentSpec:
        """Parse one entry of the OANDA ``/v3/accounts/{id}/instruments`` response."""
        if not isinstance(payload, dict):
            raise InstrumentSpecError(
                f"instrument payload must be a mapping, got {type(payload).__name__}"
            )

        raw_name = payload.get("name")
        if raw_name is None:
            raise InstrumentSpecError("instrument payload is missing 'name'")

        return cls(
            name=str(raw_name),
            pip_location=_require_int(payload, "pipLocation"),
            display_precision=_require_int(payload, "displayPrecision"),
            trade_units_precision=_require_int(payload, "tradeUnitsPrecision"),
            minimum_trade_size=_require_decimal(payload, "minimumTradeSize"),
        )

    def to_oanda_payload(self) -> dict[str, Any]:
        """Serialize back to the OANDA wire shape, for cache round-trips and tests."""
        return {
            "name": self.name,
            "pipLocation": self.pip_location,
            "displayPrecision": self.display_precision,
            "tradeUnitsPrecision": self.trade_units_precision,
            "minimumTradeSize": str(self.minimum_trade_size),
        }

    def to_cache_payload(self, fetched_at_utc: dt.datetime) -> dict[str, Any]:
        """Serialize to the on-disk cache shape. Sizes stay as exact strings."""
        return {
            **self.to_oanda_payload(),
            "fetched_at_utc": fetched_at_utc.isoformat(),
            "schema": _CACHE_SCHEMA,
        }

    @classmethod
    def from_cache_payload(cls, payload: dict[str, Any]) -> InstrumentSpec:
        """Parse a cache entry, rejecting unknown schemas and unknown fields."""
        if not isinstance(payload, dict):
            raise InstrumentSpecError(
                f"cached instrument spec must be a mapping, got {type(payload).__name__}"
            )

        schema = payload.get("schema")
        if schema != _CACHE_SCHEMA:
            raise InstrumentSpecError(
                f"unsupported instrument cache schema {schema!r}, expected {_CACHE_SCHEMA}"
            )

        unknown = set(payload) - set(_CACHE_FIELDS)
        if unknown:
            raise InstrumentSpecError(f"unknown instrument cache fields: {sorted(unknown)}")

        absent = set(_OANDA_FIELDS) - set(payload)
        if absent:
            raise InstrumentSpecError(f"cached instrument spec is missing {sorted(absent)}")

        spec = cls.from_oanda_payload(payload)

        fetched_at = payload.get("fetched_at_utc")
        if not isinstance(fetched_at, str):
            raise InstrumentSpecError("cached instrument spec is missing 'fetched_at_utc'")
        try:
            dt.datetime.fromisoformat(fetched_at)
        except ValueError as exc:
            raise InstrumentSpecError(f"unparseable fetched_at_utc {fetched_at!r}") from exc

        return spec


def _require_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if value is None:
        raise InstrumentSpecError(f"instrument payload is missing {key!r}")
    if isinstance(value, bool):
        raise InstrumentSpecError(f"instrument field {key!r} must be an integer, got a bool")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError as exc:
            raise InstrumentSpecError(
                f"instrument field {key!r} is not an integer: {value!r}"
            ) from exc
    if isinstance(value, float):
        # OANDA does not send float precisions; a float here means the payload is not what
        # we think it is, and silently truncating it would be catastrophic.
        raise InstrumentSpecError(
            f"instrument field {key!r} must be an integer, got float {value!r}"
        )
    raise InstrumentSpecError(
        f"instrument field {key!r} must be an integer, got {type(value).__name__}"
    )


def _require_decimal(payload: dict[str, Any], key: str) -> Decimal:
    value = payload.get(key)
    if value is None:
        raise InstrumentSpecError(f"instrument payload is missing {key!r}")
    try:
        parsed = Decimal(str(value).strip())
    except Exception as exc:
        raise InstrumentSpecError(
            f"instrument field {key!r} is not a decimal: {value!r}"
        ) from exc
    if not parsed.is_finite():
        raise InstrumentSpecError(f"instrument field {key!r} must be finite, got {value!r}")
    return parsed


# --------------------------------------------------------------------------- #
# disk cache
# --------------------------------------------------------------------------- #
class InstrumentSpecCache:
    """Reads and writes the instrument spec cache at ``config/instrument.cache.yaml``.

    The cache is a performance guard, never a source of truth: it is TTL-bounded,
    schema-versioned, and validated on read. A corrupt, stale or foreign file is treated
    as a miss so the loader falls back to the API (and then the fixture).
    """

    def __init__(
        self,
        path: Path | str,
        *,
        ttl: dt.timedelta = dt.timedelta(hours=24),
        clock: Callable[[], dt.datetime] | None = None,
    ) -> None:
        self._path = Path(path)
        self._ttl = ttl
        self._clock = clock or _utc_now

    @property
    def path(self) -> Path:
        return self._path

    def read(self) -> InstrumentSpec | None:
        """Return the cached spec, or ``None`` on any cache miss or read failure."""
        if not self._path.is_file():
            return None
        try:
            raw = yaml.safe_load(self._path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise InstrumentSpecError(f"instrument cache {self._path} is not valid YAML") from exc
        if raw is None:
            return None
        return InstrumentSpec.from_cache_payload(raw)

    def write(self, spec: InstrumentSpec, *, fetched_at: dt.datetime | None = None) -> None:
        """Persist ``spec`` atomically so a crash mid-write cannot truncate the cache."""
        stamp = fetched_at or _utc_now()
        payload = spec.to_cache_payload(stamp)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a sibling temp file, then atomically replace: a reader either sees the
        # old cache in full or the new one in full, never a half-written file.
        temporary = self._path.with_suffix(f".tmp{id(payload):x}")
        text = yaml.safe_dump(payload, default_flow_style=False, sort_keys=True)
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(self._path)

    def is_stale(self, *, now: dt.datetime | None = None) -> bool:
        """True when the cache is absent or older than the TTL."""
        if not self._path.is_file():
            return True
        try:
            spec = self.read()
        except InstrumentSpecError:
            return True
        if spec is None:
            return True
        try:
            raw = yaml.safe_load(self._path.read_text(encoding="utf-8"))
            fetched_at = dt.datetime.fromisoformat(raw["fetched_at_utc"])
        except (yaml.YAMLError, KeyError, TypeError, ValueError):
            return True
        return (now or self._clock()) - fetched_at >= self._ttl


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


# --------------------------------------------------------------------------- #
# loader
# --------------------------------------------------------------------------- #
#: Callable returning the parsed OANDA ``instruments`` payload for one instrument.
#: Declared as a string so the runtime never needs ``collections.abc`` imported here.
Fetcher = "Callable[[], Awaitable[dict[str, Any]]]"


class InstrumentSpecLoader:
    """Resolves the instrument spec, preferring cache, then API, then a fallback.

    Precedence, in order, *unless* ``force_refresh`` is set:

    1. a fresh disk cache
    2. a live fetch, written back to the cache
    3. a fallback spec (the committed test fixture), when fetching fails

    The fallback exists so tests and offline development never reach the network. It is
    never used in a live run unless the API is unreachable, which is a degraded mode that
    should be logged loudly by the caller.
    """

    def __init__(
        self,
        *,
        fetcher: Fetcher,
        cache: InstrumentSpecCache,
        fallback: InstrumentSpec | None = None,
        clock: Callable[[], dt.datetime] | None = None,
    ) -> None:
        self._fetcher = fetcher
        self._cache = cache
        self._fallback = fallback
        self._clock = clock or _utc_now

    @property
    def cache(self) -> InstrumentSpecCache:
        return self._cache

    async def load(self, *, force_refresh: bool = False) -> InstrumentSpec:
        """Return the instrument spec, refreshing from the API when required."""
        if force_refresh:
            pass
        elif not self._cache.is_stale(now=self._clock()):
            cached = self._cache.read()
            if cached is not None:
                return cached

        try:
            spec = await self._fetch()
        except InstrumentSpecError:
            if self._fallback is not None:
                return self._fallback
            raise
        except Exception as exc:
            if self._fallback is not None:
                return self._fallback
            raise InstrumentSpecError(f"could not load instrument spec: {exc}") from exc

        self._cache.write(spec, fetched_at=self._clock())
        return spec

    async def _fetch(self) -> InstrumentSpec:
        payload = await self._fetcher()
        if not isinstance(payload, dict):
            raise InstrumentSpecError(
                f"instrument fetch returned {type(payload).__name__}, expected a mapping"
            )
        return InstrumentSpec.from_oanda_payload(payload)
