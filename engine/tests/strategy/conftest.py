"""Shared fixtures for the Phase 4 strategy suite.

Every fixture here exists so a test can name the price it built. The strategy reads
**closed mid bars and nothing else**, so a bar is built from a mid-side OHLC and the
bid/ask sides are derived around it with a two-cent spread: a bar built that way has
``bar.mid_ohlc == (o, h, l, c)`` exactly, so an assertion on a detector's arithmetic
is an assertion on a number the test wrote down.

Local times in the fixtures are naive ``America/New_York`` wall clock. They are
converted through the pinned ``tzdata`` zone, never through the host's offset, so a
fixture that builds the London window at 03:00 local produces the UTC instants the
engine will actually see on either side of a DST transition.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from engine.market.instrument import InstrumentSpec
from engine.market.models import OHLC, Bar, Quote
from engine.strategy.config import load_strategy_config

REPO_ROOT = Path(__file__).resolve().parents[3]

UTC = dt.UTC
NEW_YORK = ZoneInfo("America/New_York")

#: The spread the fixtures quote around a mid. Two cents, so mid is exact to the
#: cent and the bid/ask sides never cross.
SPREAD: Decimal = Decimal("0.02")
HALF_SPREAD: Decimal = SPREAD / Decimal(2)

#: One M5 step.
M5: dt.timedelta = dt.timedelta(minutes=5)

#: Canonical instants around the two DST changeover weeks of 2026.
#: Spring forward: Sunday 2026-03-08 (02:00 local -> 03:00).
#: Fall back: Sunday 2026-11-01 (02:00 local -> 01:00).
SPRING_WEEK_FRIDAY = dt.date(2026, 3, 6)
SPRING_TRANSITION_SUNDAY = dt.date(2026, 3, 8)
FIRST_EDT_MONDAY = dt.date(2026, 3, 9)
FALL_WEEK_FRIDAY = dt.date(2026, 10, 30)
FALL_TRANSITION_SUNDAY = dt.date(2026, 11, 1)
FIRST_EST_MONDAY = dt.date(2026, 11, 2)


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def strategy_config() -> Any:
    """The committed ``config/strategy.yaml``, loaded and validated."""
    return load_strategy_config(REPO_ROOT / "config" / "strategy.yaml")


@pytest.fixture
def strategy_config_path() -> Path:
    return REPO_ROOT / "config" / "strategy.yaml"


@pytest.fixture
def spec() -> InstrumentSpec:
    """The XAU_USD geometry: one cent ticks, one unit is one troy ounce."""
    return InstrumentSpec(
        name="XAU_USD",
        pip_location=-4,
        display_precision=2,
        trade_units_precision=5,
        minimum_trade_size=Decimal("1"),
    )


@pytest.fixture
def tick() -> Decimal:
    return Decimal("0.01")


def at(day: dt.date, clock: dt.time) -> dt.datetime:
    """A naive ``America/New_York`` instant, the way the strategy sees wall clock."""
    return dt.datetime.combine(day, clock)


def utc_of(naive_local: dt.datetime) -> dt.datetime:
    """Resolve a naive New York instant through the pinned zone."""
    return naive_local.replace(tzinfo=NEW_YORK).astimezone(UTC)


def build_bar(
    naive_local: dt.datetime,
    mid_open: Decimal | str,
    mid_high: Decimal | str,
    mid_low: Decimal | str,
    mid_close: Decimal | str,
    *,
    complete: bool = True,
    timeframe: str = "M5",
    volume: int = 1,
) -> Bar:
    """Build one bar from a mid-side OHLC, deriving a symmetric two-cent spread."""
    open_ = Decimal(mid_open)
    high = Decimal(mid_high)
    low = Decimal(mid_low)
    close = Decimal(mid_close)
    return Bar(
        timeframe=timeframe,
        timestamp_utc=utc_of(naive_local),
        complete=complete,
        volume=volume,
        bid_ohlc=OHLC(
            open=open_ - HALF_SPREAD,
            high=high - HALF_SPREAD,
            low=low - HALF_SPREAD,
            close=close - HALF_SPREAD,
        ),
        ask_ohlc=OHLC(
            open=open_ + HALF_SPREAD,
            high=high + HALF_SPREAD,
            low=low + HALF_SPREAD,
            close=close + HALF_SPREAD,
        ),
    )


def flat_bar(naive_local: dt.datetime, price: Decimal | str, **kwargs: Any) -> Bar:
    """A bar that prints and closes at ``price``: no body, no range."""
    level = Decimal(price)
    return build_bar(naive_local, level, level, level, level, **kwargs)


def walk(start: dt.datetime, count: int) -> list[dt.datetime]:
    """``count`` M5 grid instants starting at ``start`` (naive local)."""
    return [start + (index * M5) for index in range(count)]


@pytest.fixture
def bar_factory() -> Callable[..., Bar]:
    return build_bar


@pytest.fixture
def flat_factory() -> Callable[..., Bar]:
    return flat_bar


def quotes_for(bar: Bar, *, ticks_per_bar: int = 5) -> list[Quote]:
    """Split a closed bar into a small deterministic quote stream inside its range.

    The first quote carries the bar's open, the last its close, and both extremes are
    present, so a bar rebuilt from this stream has exactly the OHLC of the bar it came
    from. That is what makes the tick-stream/batch comparison a real one: both paths
    see the same prices, only assembled differently.
    """
    if ticks_per_bar < 4:
        raise ValueError("ticks_per_bar must be at least 4 to reach open, close and both sides")

    spread = bar.ask_ohlc.close - bar.bid_ohlc.close
    span = bar.bid_ohlc.high - bar.bid_ohlc.low

    prices = [bar.bid_ohlc.open, bar.bid_ohlc.low, bar.bid_ohlc.high]
    interior = ticks_per_bar - 4
    for step in range(interior):
        fraction = Decimal(step + 1) / Decimal(interior + 1)
        prices.append(bar.bid_ohlc.low + (span * fraction))
    prices.append(bar.bid_ohlc.close)
    # The open leads and the close trails, and the walk stays inside [low, high],
    # which is what a rebuilt bar's high and low depend on.
    ordered = [prices[0], *sorted(prices[1:-1]), prices[-1]]

    quotes: list[Quote] = []
    seconds = (5 * 60) // ticks_per_bar
    for index, bid in enumerate(ordered):
        moment = bar.timestamp_utc + dt.timedelta(seconds=index * seconds)
        quotes.append(Quote(bid=bid, ask=bid + spread, timestamp_utc=moment))
    return quotes


@pytest.fixture
def quote_factory() -> Callable[..., list[Quote]]:
    return quotes_for


@pytest.fixture
def day_factory() -> Callable[..., list[Bar]]:
    """Build a bar list from an iterable of ``(naive_local, o, h, l, c)`` tuples."""
    return build_day


def build_day(entries: list[tuple[dt.datetime, Any, Any, Any, Any]]) -> list[Bar]:
    return [
        build_bar(moment, open_, high, low, close) for moment, open_, high, low, close in entries
    ]


# --------------------------------------------------------------------------- #
# canonical silver-bullet days
# --------------------------------------------------------------------------- #
#: The prices every canonical day prints. Named so a test can assert against a
#: level instead of a magic number.
ASIAN_HIGH = Decimal("2010.00")
ASIAN_LOW = Decimal("2005.00")
PREVIOUS_DAY_HIGH = Decimal("2012.00")
PREVIOUS_DAY_LOW = Decimal("1996.00")
LOCAL_HIGH = Decimal("2008.50")
SWEEP_LOW = Decimal("2004.70")
MSS_HIGH = Decimal("2009.20")
FVG_TOP = Decimal("2006.90")
FVG_BOTTOM = Decimal("2005.80")

ONE_DAY: dt.timedelta = dt.timedelta(days=1)


def _monotonic(bars: list[Bar]) -> list[Bar]:
    """Drop any bar whose resolved UTC instant is not after the one before it.

    The spring-forward hour has no wall clock at all: 02:15 New York does not exist on
    2026-03-08, and ``replace(tzinfo=...)`` maps it onto the same instant as 03:15.
    Publishing it would put two bars at one instant and then walk *backwards*, which
    is the same reason the Phase 1 session iterator refuses to fill the gap -- no wall
    clock minute exists there, so inventing one would fabricate data.
    """
    kept: list[Bar] = []
    for bar in bars:
        if kept and bar.timestamp_utc <= kept[-1].timestamp_utc:
            continue
        kept.append(bar)
    return kept


def _quiet(moment: dt.datetime, price: Decimal, *, wiggle: Decimal = Decimal("0.10")) -> Bar:
    """A bar that opens and closes at ``price`` with a small, honest range."""
    return build_bar(moment, price, price + wiggle, price - wiggle, price)


def _run(
    start: dt.datetime,
    count: int,
    price: Decimal,
    *,
    overrides: dict[dt.datetime, tuple[Decimal, Decimal, Decimal, Decimal]] | None = None,
) -> list[Bar]:
    """A run of M5 bars at ``price``, with explicit OHLC replacing named instants."""
    bars: list[Bar] = []
    for moment in walk(start, count):
        override = (overrides or {}).get(moment)
        bars.append(_quiet(moment, price) if override is None else build_bar(moment, *override))
    return _monotonic(bars)


def _asian_session(
    day: dt.date, *, low: Decimal, high: Decimal, base: Decimal, top: Decimal | None = None
) -> list[Bar]:
    """The 20:00->00:00 Asian session, which is the only range the London window may read.

    ``base`` is the quiet level and ``high`` the extreme. ``top`` turns on a monotonic
    climb, which matters because a session that only drifts would print no fractal and
    a session that chops would print one nobody asked for.
    """
    previous = day - ONE_DAY
    bars: list[Bar] = _run(at(previous, dt.time(20, 0)), 13, base)
    bars.append(build_bar(at(previous, dt.time(21, 5)), base, base, low, base - Decimal("0.05")))
    close = base - Decimal("0.05")
    for index in range(19):
        moment = at(previous, dt.time(21, 10)) + (index * M5)
        if top is not None:
            next_close = close + ((top - (base - Decimal("0.05"))) / Decimal(19))
        else:
            next_close = base
        bars.append(
            build_bar(
                moment,
                close,
                max(close, next_close) + Decimal("0.10"),
                min(close, next_close) - Decimal("0.10"),
                next_close,
            )
        )
        close = next_close
    bars.extend(_run(at(previous, dt.time(22, 45)), 12, close))
    spike = build_bar(at(previous, dt.time(23, 45)), close, high, close, close + Decimal("0.10"))
    bars.append(spike)
    bars.extend(_run(at(previous, dt.time(23, 50)), 2, close + Decimal("0.10")))
    return bars


def _previous_day(day: dt.date) -> list[Bar]:
    """The calendar day before ``day``: only its high and low matter, as PDH/PDL."""
    previous = day - ONE_DAY
    return _run(
        at(previous, dt.time(0, 0)),
        240,
        Decimal("2008.00"),
        overrides={
            at(previous, dt.time(9, 5)): (
                Decimal("2008.00"),
                PREVIOUS_DAY_HIGH,
                Decimal("2008.00"),
                Decimal("2008.20"),
            ),
            at(previous, dt.time(14, 5)): (
                Decimal("2008.00"),
                Decimal("2008.00"),
                PREVIOUS_DAY_LOW,
                Decimal("2007.80"),
            ),
        },
    )


def canonical_day(
    day: dt.date,
    *,
    reaches_target: bool = True,
    local_high: Decimal = LOCAL_HIGH,
) -> list[Bar]:
    """The reference day: the Asian low is swept in the 03:00 London window.

    The whole setup is legible from the prices alone: a sweep of the Asian low at
    03:05, an MSS over the 02:30 fractal high at 03:20, a bullish imbalance on the
    same bar, a buy limit at the imbalance midpoint filled at 03:25, and a target
    at 03:45. With ``reaches_target=False`` the rally never comes back, so the
    position is still open when the 16:45 dynamic close flattens it. ``local_high``
    moves the fractal the MSS has to break, which is how a test turns the setup off.
    """
    bars = _previous_day(day)
    bars += _asian_session(
        day, low=ASIAN_LOW, high=ASIAN_HIGH, base=Decimal("2006.00"), top=Decimal("2009.80")
    )

    london: dict[dt.datetime, tuple[Decimal, Decimal, Decimal, Decimal]] = {
        at(day, dt.time(2, 20)): (_d("2008.00"), _d("2008.30"), _d("2007.90"), _d("2008.20")),
        at(day, dt.time(2, 25)): (_d("2008.20"), _d("2008.40"), _d("2008.10"), _d("2008.35")),
        at(day, dt.time(2, 30)): (_d("2008.35"), local_high, _d("2008.20"), _d("2008.40")),
        at(day, dt.time(2, 35)): (_d("2008.40"), _d("2008.40"), _d("2008.20"), _d("2008.25")),
        at(day, dt.time(2, 40)): (_d("2008.10"), _d("2008.20"), _d("2008.05"), _d("2008.10")),
        at(day, dt.time(3, 0)): (_d("2007.60"), _d("2007.70"), _d("2007.55"), _d("2007.60")),
        at(day, dt.time(3, 5)): (_d("2007.60"), _d("2007.65"), SWEEP_LOW, _d("2005.30")),
        at(day, dt.time(3, 10)): (_d("2005.30"), FVG_BOTTOM, _d("2005.20"), _d("2005.60")),
        at(day, dt.time(3, 15)): (_d("2005.60"), _d("2007.20"), _d("2005.50"), _d("2007.00")),
        at(day, dt.time(3, 20)): (_d("2007.00"), MSS_HIGH, FVG_TOP, _d("2008.90")),
        at(day, dt.time(3, 25)): (_d("2006.90"), _d("2007.40"), _d("2006.20"), _d("2006.60")),
    }
    if reaches_target:
        rally: dict[dt.datetime, tuple[Decimal, Decimal, Decimal, Decimal]] = {
            at(day, dt.time(3, 30)): (_d("2006.60"), _d("2008.00"), _d("2006.50"), _d("2008.00")),
            at(day, dt.time(3, 35)): (_d("2008.00"), _d("2008.60"), _d("2007.90"), _d("2008.60")),
            at(day, dt.time(3, 40)): (_d("2008.60"), _d("2009.20"), _d("2008.50"), _d("2009.20")),
            at(day, dt.time(3, 45)): (_d("2009.20"), _d("2009.80"), _d("2009.10"), _d("2009.60")),
            at(day, dt.time(3, 50)): (_d("2009.60"), _d("2009.70"), _d("2009.40"), _d("2009.50")),
            at(day, dt.time(3, 55)): (_d("2009.50"), _d("2009.60"), _d("2009.30"), _d("2009.40")),
        }
        london.update(rally)
    bars += _run(at(day, dt.time(0, 0)), 204, _d("2008.50"), overrides=london)
    return bars


def ny_am_day(day: dt.date) -> list[Bar]:
    """A day whose only setup is the 10:00 window sweeping the London range high.

    The London window is deliberately quiet: nothing it is allowed to reference is
    breached between 03:00 and 04:00, so the one trade of the day is the bearish
    NY AM setup at 10:00-10:20.
    """
    bars = _previous_day(day)
    bars += _asian_session(day, low=_d("2007.90"), high=_d("2008.30"), base=_d("2008.00"))
    london_range_high = _d("2008.60")
    london: dict[dt.datetime, tuple[Decimal, Decimal, Decimal, Decimal]] = {
        at(day, dt.time(4, 30)): (
            _d("2008.20"),
            london_range_high,
            _d("2008.10"),
            _d("2008.30"),
        ),
    }
    bars += _run(at(day, dt.time(0, 0)), 60, _d("2008.05"), overrides=london)

    consolidation: dict[dt.datetime, tuple[Decimal, Decimal, Decimal, Decimal]] = {
        at(day, dt.time(9, 20)): (_d("2008.10"), _d("2008.20"), _d("2008.05"), _d("2008.10")),
        at(day, dt.time(9, 25)): (_d("2008.10"), _d("2008.15"), _d("2008.02"), _d("2008.08")),
        at(day, dt.time(9, 30)): (_d("2008.08"), _d("2008.10"), _d("2007.95"), _d("2008.02")),
        at(day, dt.time(9, 35)): (_d("2008.02"), _d("2008.15"), _d("2008.00"), _d("2008.10")),
        at(day, dt.time(9, 40)): (_d("2008.10"), _d("2008.20"), _d("2008.05"), _d("2008.12")),
    }
    bars += _run(at(day, dt.time(5, 0)), 60, _d("2008.10"), overrides=consolidation)

    ny_am: dict[dt.datetime, tuple[Decimal, Decimal, Decimal, Decimal]] = {
        at(day, dt.time(10, 5)): (_d("2008.20"), _d("2008.70"), _d("2008.15"), _d("2008.30")),
        at(day, dt.time(10, 10)): (_d("2008.05"), _d("2008.08"), _d("2007.70"), _d("2007.75")),
        at(day, dt.time(10, 15)): (_d("2007.75"), _d("2007.90"), _d("2007.55"), _d("2007.60")),
        at(day, dt.time(10, 20)): (_d("2007.60"), _d("2008.05"), _d("2007.50"), _d("2007.90")),
        at(day, dt.time(10, 25)): (_d("2007.90"), _d("2008.00"), _d("2007.20"), _d("2007.30")),
        at(day, dt.time(10, 30)): (_d("2007.30"), _d("2007.40"), _d("2006.90"), _d("2007.00")),
        at(day, dt.time(10, 35)): (_d("2007.00"), _d("2007.10"), _d("2006.50"), _d("2006.60")),
        at(day, dt.time(10, 40)): (_d("2006.60"), _d("2006.70"), _d("2006.30"), _d("2006.50")),
    }
    bars += _run(at(day, dt.time(10, 0)), 84, _d("2008.10"), overrides=ny_am)
    return bars


def bars_to_quotes(bars: list[Bar]) -> list[Quote]:
    """Flatten bars into one quote stream, in time order."""
    return [quote for bar in bars for quote in quotes_for(bar)]


def _d(value: str) -> Decimal:
    """A price, from the digits a test wrote down."""
    return Decimal(value)


@pytest.fixture
def canonical_day_factory() -> Callable[..., list[Bar]]:
    return canonical_day


@pytest.fixture
def ny_am_day_factory() -> Callable[..., list[Bar]]:
    return ny_am_day
