"""Economic-release calendar and the blackout windows it implies.

The pipeline is deliberately split into *which day* and *what time*:

* **FRED** (``releases/dates?include_release_dates_with_no_data=true``) answers *which
  day* a release lands on. It is authoritative.
* **The YAML schedule** answers *what time of day*, in America/New_York wall clock. FRED
  does not publish release times, so this is the only source.

**Forex Factory** is advisory: it may corroborate or extend, but it can never create a
blackout on its own, and a rate limit or a malformed payload must log a warning and leave
the refresh intact. That asymmetry is the whole design -- losing the advisory source should
be invisible, losing FRED should be loud.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final
from zoneinfo import ZoneInfo

import yaml

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_SCHEDULE_PATH",
    "NEW_YORK",
    "EconomicEvent",
    "EventKind",
    "ForexFactoryWarning",
    "NewsCalendar",
    "ScheduleLoader",
    "ScheduleRule",
    "blackout_windows_for",
    "load_schedule",
    "release_date_is_usable",
    "resolve_local_instant",
]

#: Pinned New York zone. Never the host's local time.
NEW_YORK: Final[ZoneInfo] = ZoneInfo("America/New_York")

#: Default on-disk schedule, relative to the repository root.
DEFAULT_SCHEDULE_PATH: Final[Path] = Path("config") / "schedule.yaml"

#: Fallback padding when a rule omits its own values.
_DEFAULT_BEFORE: Final[dt.timedelta] = dt.timedelta(minutes=15)
_DEFAULT_AFTER: Final[dt.timedelta] = dt.timedelta(minutes=60)


class ForexFactoryWarning(RuntimeWarning):
    """Forex Factory could not be read. Advisory only; never fatal."""


class EventKind(StrEnum):
    """Where an event came from, which decides whether it can gate trading."""

    RELEASE = "RELEASE"
    ADVISORY = "ADVISORY"


@dataclass(frozen=True, slots=True)
class ScheduleRule:
    """One schedule entry: a release and the local-time rule that dates it."""

    name: str
    release_id: int
    local_time: dt.time | None
    whole_day: bool
    blackout_before: dt.timedelta
    blackout_after: dt.timedelta
    impact: str = "high"

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError(f"rule name must be a non-empty string, got {self.name!r}")
        if isinstance(self.release_id, bool) or not isinstance(self.release_id, int):
            raise ValueError(f"fred_release_id must be an int, got {self.release_id!r}")
        if self.whole_day and self.local_time is not None:
            raise ValueError(
                f"rule {self.name!r} is whole_day and must not also set local_time"
            )
        if not self.whole_day and self.local_time is None:
            raise ValueError(
                f"rule {self.name!r} needs either a local_time or whole_day=true"
            )
        for name, value in (
            ("blackout_before", self.blackout_before),
            ("blackout_after", self.blackout_after),
        ):
            if not isinstance(value, dt.timedelta):
                raise ValueError(f"{name} must be a timedelta, got {type(value).__name__}")
            if value < dt.timedelta(0):
                raise ValueError(f"{name} must not be negative, got {value}")


@dataclass(frozen=True, slots=True)
class EconomicEvent:
    """A release, resolved onto a concrete instant."""

    name: str
    release_id: int
    kind: EventKind
    timestamp_utc: dt.datetime | None
    local_date: dt.date
    impact: str = "high"

    @property
    def is_blocking(self) -> bool:
        """Whether this event may gate signal generation.

        Only FRED-confirmed releases can block. An advisory hit is a hint for a human,
        never a reason for the engine to stop trading.
        """
        return self.kind is EventKind.RELEASE


@dataclass(frozen=True, slots=True)
class BlackoutWindow:
    """A half-open UTC interval during which signals must not be generated."""

    name: str
    start: dt.datetime
    end: dt.datetime

    def contains(self, timestamp_utc: dt.datetime) -> bool:
        return self.start <= timestamp_utc < self.end

    @property
    def duration(self) -> dt.timedelta:
        return self.end - self.start


def resolve_local_instant(
    local_date: dt.date, local_time: dt.time | None, tz: ZoneInfo = NEW_YORK
) -> dt.datetime:
    """Resolve a local ``date`` + ``time`` in ``tz`` to a UTC instant.

    The time is interpreted as wall clock, so the DST offset in effect on that date is
    applied automatically. A time inside a spring-forward gap resolves to the moment the
    clock lands on, which is the visible, debuggable outcome; a misconfigured rule shows
    up as a wrong time an operator can spot rather than a crash.
    """
    if not isinstance(local_date, dt.date):
        raise ValueError(f"local_date must be a date, got {type(local_date).__name__}")
    if local_time is None:
        local_time = dt.time(0, 0)
    if not isinstance(local_time, dt.time):
        raise ValueError(f"local_time must be a time, got {type(local_time).__name__}")
    if not isinstance(tz, ZoneInfo):
        raise ValueError(f"tz must be a ZoneInfo, got {type(tz).__name__}")

    naive = dt.datetime.combine(local_date, local_time)
    aware = naive.replace(tzinfo=tz)
    return aware.astimezone(dt.UTC)


def release_date_is_usable(
    release_date: dt.date,
    *,
    before: dt.date | None = None,
    after: dt.date | None = None,
) -> bool:
    """Whether a FRED release date falls inside an optional window."""
    if not isinstance(release_date, dt.date):
        raise ValueError(f"release_date must be a date, got {type(release_date).__name__}")
    if before is not None and release_date > before:
        return False
    return not (after is not None and release_date < after)


def blackout_windows_for(
    event: EconomicEvent, rule: ScheduleRule
) -> list[BlackoutWindow]:
    """Blackout windows for one event.

    A timed release is padded on both sides by the rule's blackout values. A whole-day
    release covers the New York local day, because no single instant is meaningful.
    """
    if not event.is_blocking:
        return []

    if rule.whole_day:
        start = resolve_local_instant(event.local_date, dt.time(0, 0))
        end = resolve_local_instant(
            event.local_date + dt.timedelta(days=1), dt.time(0, 0)
        )
        return [BlackoutWindow(name=event.name, start=start, end=end)]

    if event.timestamp_utc is None:
        return []

    start = event.timestamp_utc - rule.blackout_before
    end = event.timestamp_utc + rule.blackout_after
    return [BlackoutWindow(name=event.name, start=start, end=end)]


class ScheduleLoader:
    """Reads and validates the YAML schedule."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> tuple[ScheduleRule, ...]:
        if not self._path.is_file():
            raise FileNotFoundError(f"schedule not found at {self._path}")

        try:
            raw = yaml.safe_load(self._path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ValueError(f"schedule {self._path} is not valid YAML") from exc

        if not isinstance(raw, dict):
            raise ValueError(f"schedule must be a mapping, got {type(raw).__name__}")

        entries = raw.get("events", [])
        if not isinstance(entries, list):
            raise ValueError("schedule 'events' must be a list")

        return tuple(self._rule(item) for item in entries)

    def _rule(self, item: object) -> ScheduleRule:
        if not isinstance(item, dict):
            raise ValueError(f"schedule entry must be a mapping, got {type(item).__name__}")

        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("schedule entry needs a 'name'")

        release_id = item.get("fred_release_id")
        if isinstance(release_id, bool) or not isinstance(release_id, int):
            raise ValueError(f"rule {name!r} needs an integer 'fred_release_id'")

        whole_day = bool(item.get("whole_day", False))
        local_time = _parse_local_time(name, item.get("local_time"), whole_day)

        before = _minutes(item.get("blackout_before_minutes"), _DEFAULT_BEFORE, name)
        after = _minutes(item.get("blackout_after_minutes"), _DEFAULT_AFTER, name)

        return ScheduleRule(
            name=name,
            release_id=release_id,
            local_time=local_time,
            whole_day=whole_day,
            blackout_before=before,
            blackout_after=after,
            impact=str(item.get("impact", "high")),
        )


def _parse_local_time(name: str, value: object, whole_day: bool) -> dt.time | None:
    if whole_day:
        if value not in (None, "", "null"):
            raise ValueError(f"rule {name!r} is whole_day and must not set local_time")
        return None
    if value is None:
        raise ValueError(f"rule {name!r} needs a local_time or whole_day=true")
    text = str(value).strip()
    try:
        parsed = dt.time.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"rule {name!r} has an unparseable local_time {value!r}") from exc
    if parsed.second or parsed.microsecond:
        raise ValueError(f"rule {name!r} local_time must be whole minutes, got {value!r}")
    return parsed


def _minutes(value: object, fallback: dt.timedelta, name: str) -> dt.timedelta:
    if value is None:
        return fallback
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"rule {name!r} blackout minutes must be an int, got {value!r}")
    if value < 0:
        raise ValueError(f"rule {name!r} blackout minutes must not be negative, got {value}")
    return dt.timedelta(minutes=value)


def load_schedule(path: Path | str = DEFAULT_SCHEDULE_PATH) -> tuple[ScheduleRule, ...]:
    return ScheduleLoader(path).load()


#: Callables the calendar depends on. Declared as strings so the runtime does not need
#: ``collections.abc`` imported here.
FredSource = "Callable[[int], Awaitable[Sequence[dt.date]]]"
FactorySource = "Callable[[], Awaitable[Sequence[Mapping[str, Any]]]]"


class NewsCalendar:
    """Builds the set of events and blackout windows for a date range.

    FRED supplies the dates and is authoritative: if it fails, the refresh fails. The
    Forex Factory source is advisory and may only *add* corroborating events, never
    create a blackout on its own.
    """

    def __init__(
        self,
        *,
        rules: Sequence[ScheduleRule],
        fred: FredSource,
        factory: FactorySource | None = None,
        tz: ZoneInfo = NEW_YORK,
    ) -> None:
        self._rules = tuple(rules)
        self._fred = fred
        self._factory = factory
        self._tz = tz

    @property
    def rules(self) -> tuple[ScheduleRule, ...]:
        return self._rules

    async def refresh(self, *, start: dt.date, end: dt.date) -> list[EconomicEvent]:
        """Events whose local date falls in ``[start, end]``.

        Raises:
            ValueError: if the window is inverted.
            Exception: whatever the FRED source raises; it is authoritative.
        """
        if not isinstance(start, dt.date) or not isinstance(end, dt.date):
            raise ValueError("start and end must be dates")
        if end < start:
            raise ValueError(f"end {end} is before start {start}")

        rules_by_id = {rule.release_id: rule for rule in self._rules}
        events: list[EconomicEvent] = []

        for release_id, rule in sorted(rules_by_id.items()):
            dates = await self._fred(release_id)
            for local_date in _usable_dates(dates, start=start, end=end):
                events.append(self._event_for(rule, local_date))

        events.extend(await self._advisory_events(start=start, end=end))
        return sorted(events, key=lambda e: (e.local_date, e.name))

    def _event_for(self, rule: ScheduleRule, local_date: dt.date) -> EconomicEvent:
        if rule.whole_day:
            return EconomicEvent(
                name=rule.name,
                release_id=rule.release_id,
                kind=EventKind.RELEASE,
                timestamp_utc=None,
                local_date=local_date,
                impact=rule.impact,
            )
        if rule.local_time is None:  # pragma: no cover - guarded by ScheduleRule
            raise ValueError(f"rule {rule.name!r} has neither local_time nor whole_day")
        return EconomicEvent(
            name=rule.name,
            release_id=rule.release_id,
            kind=EventKind.RELEASE,
            timestamp_utc=resolve_local_instant(local_date, rule.local_time, self._tz),
            local_date=local_date,
            impact=rule.impact,
        )

    async def _advisory_events(
        self, *, start: dt.date, end: dt.date
    ) -> list[EconomicEvent]:
        """Read Forex Factory. Any failure is a warning and yields nothing."""
        if self._factory is None:
            return []

        try:
            payload = await self._factory()
        except Exception as exc:
            logger.warning("forex factory unavailable, continuing without it: %s", exc)
            return []

        if not isinstance(payload, (list, tuple)):
            logger.warning(
                "forex factory returned %s, expected a list; ignoring",
                type(payload).__name__,
            )
            return []

        return self._digest_advisory(payload, start=start, end=end)

    def _digest_advisory(
        self, payload: Iterable[Mapping[str, Any]], *, start: dt.date, end: dt.date
    ) -> list[EconomicEvent]:
        known = {rule.name.lower() for rule in self._rules}
        events: list[EconomicEvent] = []

        for item in payload:
            if not isinstance(item, dict):
                logger.warning("ignoring non-mapping forex factory entry")
                continue
            title = str(item.get("title", "")).strip()
            if title.lower() not in known:
                continue
            try:
                stamp = _parse_factory_time(item.get("date"))
            except (ValueError, TypeError):
                logger.warning("ignoring forex factory entry with a bad date: %r", item)
                continue
            if stamp is None or not start <= stamp.date() <= end:
                continue
            events.append(
                EconomicEvent(
                    name=title,
                    release_id=0,
                    kind=EventKind.ADVISORY,
                    timestamp_utc=stamp,
                    local_date=stamp.date(),
                )
            )
        return events

    def blackouts(self, events: Iterable[EconomicEvent]) -> list[BlackoutWindow]:
        """Blackout windows for the given events, sorted and non-overlapping per name."""
        rules_by_id = {rule.release_id: rule for rule in self._rules}
        windows: list[BlackoutWindow] = []
        for event in events:
            rule = rules_by_id.get(event.release_id)
            if rule is None:
                continue
            windows.extend(blackout_windows_for(event, rule))
        return sorted(windows, key=lambda w: (w.start, w.name))


def _usable_dates(
    dates: Iterable[dt.date], *, start: dt.date, end: dt.date
) -> list[dt.date]:
    usable: list[dt.date] = []
    for value in dates:
        if not isinstance(value, dt.date):
            logger.warning("fred returned a non-date %r; ignoring", value)
            continue
        if not release_date_is_usable(value, before=end, after=start):
            continue
        usable.append(value)
    return sorted(set(usable))


def _parse_factory_time(value: object) -> dt.datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)
