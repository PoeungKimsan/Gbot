"""Shared pytest fixtures.

Everything a test needs that is not under test lives here, as a fixture. This matters: the
suite must be runnable as *any* subset (``uv run pytest engine/tests/feed/test_x.py``) without
requiring ``engine/tests`` to be on ``sys.path``. Module-level imports between test files would
silently break single-file runs.

Fixtures provided here are available to every subdirectory of ``engine/tests``.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterator, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTRUMENT_FIXTURE = REPO_ROOT / "engine" / "tests" / "fixtures" / "instruments" / "XAU_USD.yaml"
SCHEDULE_PATH = REPO_ROOT / "config" / "schedule.yaml"
BREAKER_PATH = REPO_ROOT / "config" / "breaker.yaml"

UTC = dt.UTC
NEW_YORK = ZoneInfo("America/New_York")

#: Canonical instants used across Phase 1 tests, so timestamps in assertions are stable.
BASE_TS = dt.datetime(2026, 1, 15, 14, 30, 0, tzinfo=UTC)
BASE_NY_CLOSE = dt.datetime(2026, 1, 15, 17, 0, 0, tzinfo=NEW_YORK)


class FakeClock:
    """Decimal monotonic clock. Never returns a float and never sleeps."""

    def __init__(self, start: Decimal = Decimal(0)) -> None:
        self._now = Decimal(start)

    def now(self) -> Decimal:
        return self._now

    def advance(self, seconds: Decimal | str | int) -> Decimal:
        self._now += Decimal(seconds)
        return self._now


class NoSleepSleeper:
    """Advances the fake clock by the requested duration and yields to the loop.

    Substituting this for ``asyncio.sleep`` keeps the reconnect/backoff loop fully
    deterministic and lets a test run a full backoff sequence in microseconds.
    """

    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock
        self.requested: list[Decimal] = []

    async def __call__(self, duration: dt.timedelta) -> None:
        whole_seconds = Decimal(duration.days) * Decimal(86400) + Decimal(duration.seconds)
        microseconds = Decimal(duration.microseconds) / Decimal(1_000_000)
        self.requested.append(whole_seconds + microseconds)
        self._clock.advance(whole_seconds + microseconds)
        import asyncio

        await asyncio.sleep(0)


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def utc() -> dt.timezone:
    return UTC


@pytest.fixture
def new_york() -> dt.ZoneInfo:
    return NEW_YORK


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def fake_clock_factory() -> Callable[..., FakeClock]:
    def build(start: Decimal | str | int = Decimal(0)) -> FakeClock:
        return FakeClock(Decimal(start))

    return build


@pytest.fixture
def sleeper(fake_clock: FakeClock) -> NoSleepSleeper:
    return NoSleepSleeper(fake_clock)


@pytest.fixture
def instrument_payload() -> dict[str, Any]:
    """Raw OANDA ``instruments`` payload for XAU_USD."""
    return {
        "name": "XAU_USD",
        "type": "CFD",
        "displayName": "Gold",
        "pipLocation": -4,
        "displayPrecision": 2,
        "tradeUnitsPrecision": 5,
        "minimumTradeSize": "1",
    }


@pytest.fixture
def instrument_spec(instrument_payload: Mapping[str, Any]) -> Any:
    from engine.market.instrument import InstrumentSpec

    return InstrumentSpec.from_oanda_payload(instrument_payload)


@pytest.fixture
def quote_factory(
    instrument_spec: Any,
) -> Callable[..., Any]:
    """Build a :class:`Quote` with Overridable defaults."""
    from engine.market.models import Quote

    def build(
        bid: Decimal | str = Decimal("2038.25"),
        ask: Decimal | str = Decimal("2038.27"),
        timestamp_utc: dt.datetime | None = None,
    ) -> Quote:
        return Quote(
            bid=Decimal(bid),
            ask=Decimal(ask),
            timestamp_utc=timestamp_utc or BASE_TS,
        )

    return build


def _ohlc(
    opn: Decimal | str,
    high: Decimal | str,
    low: Decimal | str,
    close: Decimal | str,
) -> Any:
    from engine.market.models import OHLC

    return OHLC(
        open=Decimal(opn),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
    )


@pytest.fixture
def bar_factory() -> Callable[..., Any]:
    """Build a :class:`Bar` with overridable bid/ask OHLC."""
    from engine.market.models import Bar, Timeframe

    def build(
        timeframe: Any = Timeframe.M1,
        timestamp_utc: dt.datetime | None = None,
        complete: bool = True,
        volume: int = 0,
        bid: Any = None,
        ask: Any = None,
    ) -> Bar:
        return Bar(
            timeframe=timeframe,
            timestamp_utc=timestamp_utc or BASE_TS,
            complete=complete,
            volume=volume,
            bid_ohlc=bid or _ohlc("2038.00", "2038.50", "2037.75", "2038.25"),
            ask_ohlc=ask or _ohlc("2038.02", "2038.52", "2037.77", "2038.27"),
        )

    return build


@pytest.fixture
def breaker_config() -> Any:
    """Breaker configuration read from ``config/breaker.yaml``."""
    from engine.news.breaker import BreakerConfig

    raw = yaml.safe_load(BREAKER_PATH.read_text(encoding="utf-8"))
    return BreakerConfig.from_mapping(raw)


@pytest.fixture
def schedule_rules() -> Any:
    """Parsed schedule rules from ``config/schedule.yaml``."""
    from engine.news.calendar import ScheduleLoader

    return ScheduleLoader(SCHEDULE_PATH).load()


@pytest.fixture
def schedule_path() -> Path:
    return SCHEDULE_PATH


@pytest.fixture
def schedule_raw() -> dict[str, Any]:
    return yaml.safe_load(SCHEDULE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _no_real_io() -> Iterator[None]:
    """Assert that no Phase 1 test performs real network or wall-clock I/O.

    The guard is advisory: it patches ``socket.getaddrinfo`` to refuse connections, so a
    test that forgets to inject a transport fails loudly instead of silently hanging.
    """
    import socket

    original = socket.getaddrinfo

    def guarded(*args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        if args and str(args[0]) in {"127.0.0.1", "localhost", "::1"}:
            return original(*args, **kwargs)
        raise AssertionError(
            f"a Phase 1 test attempted real network resolution for {args!r}; "
            "inject the transport/fetcher instead"
        )

    socket.getaddrinfo = guarded  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.getaddrinfo = original  # type: ignore[assignment]
