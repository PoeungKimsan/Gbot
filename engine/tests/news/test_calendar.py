"""Unit tests for :mod:`engine.news.calendar`.

The behaviours under test: schedule loading from YAML, local-time rules resolved through
America/New_York, FRED release dates supplying *which day* a release lands on, blackout
windows derived from those releases, and the Forex Factory adapter being advisory only --
a rate limit or a parse failure must never interrupt a refresh.
"""

import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from engine.news.calendar import (
    EconomicEvent,
    EventKind,
    ForexFactoryWarning,
    NewsCalendar,
    ScheduleLoader,
    ScheduleRule,
    blackout_windows_for,
    load_schedule,
    release_date_is_usable,
    resolve_local_instant,
)

UTC = dt.UTC
NEW_YORK = ZoneInfo("America/New_York")

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEDULE_PATH = REPO_ROOT / "config" / "schedule.yaml"


# --------------------------------------------------------------------------- #
# schedule loading
# --------------------------------------------------------------------------- #
def test_load_schedule_reads_the_committed_config() -> None:
    rules = load_schedule(SCHEDULE_PATH)

    names = {rule.name for rule in rules}
    assert "Nonfarm Payrolls" in names
    assert "Consumer Price Index" in names
    assert "FOMC Statement" in names


def test_schedule_rule_fields() -> None:
    rules = {rule.name: rule for rule in load_schedule(SCHEDULE_PATH)}

    nfp = rules["Nonfarm Payrolls"]
    assert nfp.release_id == 50
    assert nfp.local_time == dt.time(8, 30)
    assert nfp.blackout_before == dt.timedelta(minutes=30)
    assert nfp.blackout_after == dt.timedelta(minutes=90)
    assert nfp.whole_day is False

    fomc = rules["FOMC Statement"]
    assert fomc.whole_day is True
    assert fomc.local_time is None


def test_schedule_rule_is_immutable() -> None:
    rule = ScheduleRule(
        name="Test Release",
        release_id=1,
        local_time=dt.time(8, 30),
        whole_day=False,
        blackout_before=dt.timedelta(minutes=15),
        blackout_after=dt.timedelta(minutes=20),
    )

    with pytest.raises(Exception):  # noqa: B017
        rule.name = "Other"  # type: ignore[misc]


def test_schedule_loader_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        ScheduleLoader(tmp_path / "absent.yaml").load()


def test_schedule_loader_rejects_non_mapping(tmp_path: Path) -> None:
    path = tmp_path / "schedule.yaml"
    path.write_text("- just\n- a\n- list\n", encoding="utf-8")

    with pytest.raises(ValueError, match="mapping"):
        ScheduleLoader(path).load()


@pytest.mark.parametrize(
    "missing",
    ["name", "fred_release_id"],
)
def test_schedule_loader_rejects_missing_fields(tmp_path: Path, missing: str) -> None:
    rule = {
        "name": "Test",
        "fred_release_id": 1,
        "local_time": "08:30",
        "impact": "high",
        "blackout_before_minutes": 10,
        "blackout_after_minutes": 20,
    }
    del rule[missing]
    path = tmp_path / "schedule.yaml"
    path.write_text(f"instrument: XAU_USD\nevents:\n  - {rule}\n", encoding="utf-8")

    with pytest.raises(ValueError):
        ScheduleLoader(path).load()


def test_schedule_loader_rejects_bad_local_time(tmp_path: Path) -> None:
    path = tmp_path / "schedule.yaml"
    path.write_text(
        "instrument: XAU_USD\nevents:\n  - name: X\n    fred_release_id: 1\n"
        '    local_time: "25:00"\n    impact: high\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="local_time"):
        ScheduleLoader(path).load()


def test_schedule_loader_rejects_negative_blackout(tmp_path: Path) -> None:
    path = tmp_path / "schedule.yaml"
    path.write_text(
        "instrument: XAU_USD\nevents:\n  - name: X\n    fred_release_id: 1\n"
        '    local_time: "08:30"\n    impact: high\n    blackout_before_minutes: -5\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="blackout"):
        ScheduleLoader(path).load()


def test_schedule_whole_day_rule_must_not_set_a_local_time(tmp_path: Path) -> None:
    path = tmp_path / "schedule.yaml"
    path.write_text(
        "instrument: XAU_USD\nevents:\n  - name: X\n    fred_release_id: 1\n"
        '    local_time: "08:30"\n    whole_day: true\n    impact: high\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="whole_day"):
        ScheduleLoader(path).load()


# --------------------------------------------------------------------------- #
# local time resolution
# --------------------------------------------------------------------------- #
def test_resolve_local_instant_uses_new_york_wall_clock() -> None:
    """08:30 New York in January is 13:30 UTC, not 08:30 UTC."""
    instant = resolve_local_instant(dt.date(2026, 1, 15), dt.time(8, 30), NEW_YORK)

    assert instant == dt.datetime(2026, 1, 15, 13, 30, tzinfo=UTC)


def test_resolve_local_instant_tracks_dst() -> None:
    """08:30 New York in July is 12:30 UTC because EDT is UTC-4."""
    instant = resolve_local_instant(dt.date(2026, 7, 15), dt.time(8, 30), NEW_YORK)

    assert instant == dt.datetime(2026, 7, 15, 12, 30, tzinfo=UTC)


def test_resolve_local_instant_on_spring_forward_day() -> None:
    """08:30 is safely after the 02:00-to-03:00 jump, so it resolves normally."""
    instant = resolve_local_instant(dt.date(2026, 3, 8), dt.time(8, 30), NEW_YORK)

    assert instant.astimezone(NEW_YORK).hour == 8


def test_resolve_local_instant_maps_into_a_nonexistent_hour() -> None:
    """02:30 does not exist on spring-forward day; the shift must be visible.

    A release is never scheduled in that hour, but the resolution must be total rather
    than raise, because a misconfigured rule must surface as a wrong time the operator can
    see, not a crash that stops the pipeline.
    """
    instant = resolve_local_instant(dt.date(2026, 3, 8), dt.time(2, 30), NEW_YORK)

    local = instant.astimezone(NEW_YORK)
    assert local.hour == 3  # 02:30 EST is read as 03:30 EDT, one hour later in UTC terms


@pytest.mark.parametrize(
    "bad_time",
    ["08:30", 830, dt.timedelta(minutes=5)],
)
def test_resolve_local_instant_rejects_non_time(bad_time) -> None:
    with pytest.raises(ValueError):
        resolve_local_instant(dt.date(2026, 1, 15), bad_time, NEW_YORK)


def test_resolve_local_instant_rejects_non_date() -> None:
    with pytest.raises(ValueError):
        resolve_local_instant("2026-01-15", dt.time(8, 30), NEW_YORK)


def test_resolve_local_instant_defaults_to_midnight() -> None:
    """A whole-day rule passes no time; midnight New York is the documented default."""
    instant = resolve_local_instant(dt.date(2026, 1, 15), None, NEW_YORK)

    assert instant == dt.datetime(2026, 1, 15, 5, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# FRED release-date gating
# --------------------------------------------------------------------------- #
def test_release_date_is_usable() -> None:
    """Bounds are inclusive: a date exactly on a bound is still usable."""
    assert release_date_is_usable(dt.date(2026, 1, 15)) is True
    assert release_date_is_usable(dt.date(2026, 1, 15), before=dt.date(2026, 1, 15)) is True
    assert release_date_is_usable(dt.date(2026, 1, 16), before=dt.date(2026, 1, 15)) is False
    assert release_date_is_usable(dt.date(2026, 1, 14), after=dt.date(2026, 1, 15)) is False


# --------------------------------------------------------------------------- #
# events and blackout windows
# --------------------------------------------------------------------------- #
def _rule(**overrides: object) -> ScheduleRule:
    defaults: dict[str, object] = {
        "name": "Test Release",
        "release_id": 1,
        "local_time": dt.time(8, 30),
        "whole_day": False,
        "blackout_before": dt.timedelta(minutes=30),
        "blackout_after": dt.timedelta(minutes=60),
        "impact": "high",
    }
    defaults.update(overrides)
    return ScheduleRule(**defaults)  # type: ignore[arg-type]


def test_blackout_window_wraps_the_release_on_both_sides() -> None:
    event = EconomicEvent(
        name="Test Release",
        release_id=1,
        kind=EventKind.RELEASE,
        timestamp_utc=dt.datetime(2026, 1, 15, 13, 30, tzinfo=UTC),
        local_date=dt.date(2026, 1, 15),
    )

    windows = blackout_windows_for(event, _rule())

    assert len(windows) == 1
    window = windows[0]
    assert window.start == dt.datetime(2026, 1, 15, 13, 0, tzinfo=UTC)
    assert window.end == dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC)
    assert window.contains(dt.datetime(2026, 1, 15, 13, 45, tzinfo=UTC))
    assert not window.contains(dt.datetime(2026, 1, 15, 15, 0, tzinfo=UTC))


def test_blackout_window_for_whole_day_release_spans_the_local_day() -> None:
    rule = _rule(whole_day=True, local_time=None)
    event = EconomicEvent(
        name="FOMC",
        release_id=101,
        kind=EventKind.RELEASE,
        timestamp_utc=dt.datetime(2026, 1, 15, 0, 0, tzinfo=UTC),
        local_date=dt.date(2026, 1, 15),
    )

    window = blackout_windows_for(event, rule)[0]

    # A whole-day release is treated as covering the New York local day, which starts at
    # midnight EST (05:00 UTC) in January.
    assert window.start == dt.datetime(2026, 1, 15, 5, 0, tzinfo=UTC)
    assert window.end == dt.datetime(2026, 1, 16, 5, 0, tzinfo=UTC)
    assert window.duration == dt.timedelta(hours=24)


def test_blackout_window_is_dst_aware() -> None:
    """A summer release must produce a window one UTC hour earlier than in winter."""
    rule = _rule()
    winter = EconomicEvent(
        name="Test",
        release_id=1,
        kind=EventKind.RELEASE,
        timestamp_utc=dt.datetime(2026, 1, 15, 13, 30, tzinfo=UTC),
        local_date=dt.date(2026, 1, 15),
    )
    summer = EconomicEvent(
        name="Test",
        release_id=1,
        kind=EventKind.RELEASE,
        timestamp_utc=dt.datetime(2026, 7, 15, 12, 30, tzinfo=UTC),
        local_date=dt.date(2026, 7, 15),
    )

    winter_window = blackout_windows_for(winter, rule)[0]
    summer_window = blackout_windows_for(summer, rule)[0]

    assert winter_window.start.hour == 13
    assert summer_window.start.hour == 12


def test_blackout_window_boundaries_are_half_open() -> None:
    event = EconomicEvent(
        name="Test",
        release_id=1,
        kind=EventKind.RELEASE,
        timestamp_utc=dt.datetime(2026, 1, 15, 13, 30, tzinfo=UTC),
        local_date=dt.date(2026, 1, 15),
    )
    window = blackout_windows_for(event, _rule())[0]

    assert window.contains(window.start) is True
    assert window.contains(window.end) is False


# --------------------------------------------------------------------------- #
# calendar refresh
# --------------------------------------------------------------------------- #
class _FakeFred:
    """Returns a fixed date list per release id."""

    def __init__(self, dates: dict[int, list[dt.date]]) -> None:
        self._dates = dates
        self.calls: list[int] = []

    async def __call__(self, release_id: int) -> list[dt.date]:
        self.calls.append(release_id)
        return self._dates.get(release_id, [])


async def test_calendar_builds_events_from_fred_dates() -> None:
    fred = _FakeFred({50: [dt.date(2026, 1, 15), dt.date(2026, 2, 13)]})
    calendar = NewsCalendar(rules=load_schedule(SCHEDULE_PATH), fred=fred)

    events = await calendar.refresh(start=dt.date(2026, 1, 1), end=dt.date(2026, 2, 28))

    assert {e.local_date for e in events} == {dt.date(2026, 1, 15), dt.date(2026, 2, 13)}
    assert all(e.timestamp_utc is not None for e in events)


async def test_calendar_ignores_fred_dates_outside_the_window() -> None:
    fred = _FakeFred({50: [dt.date(2026, 1, 15), dt.date(2025, 1, 15)]})
    calendar = NewsCalendar(rules=load_schedule(SCHEDULE_PATH), fred=fred)

    events = await calendar.refresh(start=dt.date(2026, 1, 1), end=dt.date(2026, 1, 31))

    assert [e.local_date for e in events] == [dt.date(2026, 1, 15)]


async def test_calendar_whole_day_event_still_has_a_local_date() -> None:
    fred = _FakeFred({101: [dt.date(2026, 1, 28)]})
    calendar = NewsCalendar(rules=load_schedule(SCHEDULE_PATH), fred=fred)

    events = await calendar.refresh(start=dt.date(2026, 1, 1), end=dt.date(2026, 1, 31))
    fomc = [e for e in events if e.release_id == 101]

    assert len(fomc) == 1
    assert fomc[0].local_date == dt.date(2026, 1, 28)


async def test_calendar_rejects_an_inverted_window() -> None:
    calendar = NewsCalendar(rules=load_schedule(SCHEDULE_PATH), fred=_FakeFred({}))

    with pytest.raises(ValueError, match="end"):
        await calendar.refresh(start=dt.date(2026, 2, 1), end=dt.date(2026, 1, 1))


async def test_calendar_propagates_a_fred_failure() -> None:
    """FRED is authoritative: an unreachable API must fail the refresh, not be ignored."""

    class _Boom:
        async def __call__(self, release_id: int) -> list[dt.date]:
            raise RuntimeError("fred unavailable")

    calendar = NewsCalendar(rules=load_schedule(SCHEDULE_PATH), fred=_Boom())

    with pytest.raises(RuntimeError, match="fred unavailable"):
        await calendar.refresh(start=dt.date(2026, 1, 1), end=dt.date(2026, 1, 31))


# --------------------------------------------------------------------------- #
# Forex Factory: strictly advisory
# --------------------------------------------------------------------------- #
async def test_forex_factory_events_are_added_alongside_fred() -> None:
    fred = _FakeFred({50: [dt.date(2026, 1, 15)]})

    class _Factory:
        async def __call__(self) -> list[dict]:
            return [
                {
                    "title": "Nonfarm Payrolls",
                    "country": "USD",
                    "date": "2026-01-15T08:30:00-05:00",
                    "impact": "High",
                }
            ]

    calendar = NewsCalendar(
        rules=load_schedule(SCHEDULE_PATH), fred=fred, factory=_Factory()
    )

    events = await calendar.refresh(start=dt.date(2026, 1, 1), end=dt.date(2026, 1, 31))

    kinds = {e.kind for e in events}
    assert EventKind.RELEASE in kinds
    assert EventKind.ADVISORY in kinds


async def test_forex_factory_rate_limit_is_a_warning_not_a_failure(caplog) -> None:
    fred = _FakeFred({50: [dt.date(2026, 1, 15)]})

    class _RateLimited:
        async def __call__(self) -> list[dict]:
            raise ForexFactoryWarning("429 Too Many Requests")

    calendar = NewsCalendar(
        rules=load_schedule(SCHEDULE_PATH), fred=fred, factory=_RateLimited()
    )

    with caplog.at_level("WARNING"):
        events = await calendar.refresh(
            start=dt.date(2026, 1, 1), end=dt.date(2026, 1, 31)
        )

    assert events, "the refresh must still produce the FRED-derived events"
    assert "429" in caplog.text


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        [{"title": "Something Else"}],
        [{"title": "Nonfarm Payrolls", "date": "not-a-date"}],
        ["not", "a", "mapping"],
    ],
)
async def test_forex_factory_malformed_payloads_are_ignored(payload) -> None:
    fred = _FakeFred({50: [dt.date(2026, 1, 15)]})

    class _Broken:
        async def __call__(self) -> list[dict]:
            return payload

    calendar = NewsCalendar(
        rules=load_schedule(SCHEDULE_PATH), fred=fred, factory=_Broken()
    )

    events = await calendar.refresh(start=dt.date(2026, 1, 1), end=dt.date(2026, 1, 31))

    # FRED events survive; the advisory source simply contributed nothing.
    assert [e.local_date for e in events if e.kind is EventKind.RELEASE] == [
        dt.date(2026, 1, 15)
    ]


async def test_forex_factory_advisory_never_creates_a_blackout_alone() -> None:
    """An advisory event must not block trading unless FRED agrees it is happening."""

    class _Factory:
        async def __call__(self) -> list[dict]:
            return [
                {
                    "title": "Made Up Release",
                    "country": "USD",
                    "date": "2026-01-15T08:30:00-05:00",
                    "impact": "High",
                }
            ]

    calendar = NewsCalendar(
        rules=load_schedule(SCHEDULE_PATH), fred=_FakeFred({}), factory=_Factory()
    )

    events = await calendar.refresh(start=dt.date(2026, 1, 1), end=dt.date(2026, 1, 31))

    assert events == []


def test_forex_factory_warning_is_an_exception_type() -> None:
    assert issubclass(ForexFactoryWarning, Exception)
