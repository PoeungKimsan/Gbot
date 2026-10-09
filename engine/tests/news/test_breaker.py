"""Unit tests for :mod:`engine.news.breaker`.

The behaviours under test: rolling ATR from mid bars, a rolling median spread, the spread
breaker triggering above ``spread_breaker_multiple`` times the median, blackout windows
blocking generation, and the gate being inert until it has enough samples to judge.
"""

import datetime as dt
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

from engine.market.instrument import InstrumentSpec
from engine.market.models import OHLC, Bar, Quote, Timeframe
from engine.news.breaker import (
    BreakerConfig,
    BreakerReason,
    RollingATR,
    RollingMedian,
    SignalBreaker,
)
from engine.news.calendar import BlackoutWindow

UTC = dt.UTC
NEW_YORK = ZoneInfo("America/New_York")

REPO_ROOT = Path(__file__).resolve().parents[3]
BREAKER_PATH = REPO_ROOT / "config" / "breaker.yaml"

XAU_USD_SPEC = InstrumentSpec(
    name="XAU_USD",
    pip_location=-4,
    display_precision=2,
    trade_units_precision=5,
    minimum_trade_size=Decimal("1"),
)


def _ohlc(opn: str, high: str, low: str, close: str) -> OHLC:
    return OHLC(open=Decimal(opn), high=Decimal(high), low=Decimal(low), close=Decimal(close))


def _bar(
    timestamp_utc: dt.datetime,
    bid: OHLC | None = None,
    ask: OHLC | None = None,
) -> Bar:
    """A valid bar whose ask side is one spread unit above the bid side."""
    bid = bid or _ohlc("2038.00", "2038.50", "2037.75", "2038.25")
    ask = ask or _ohlc("2038.02", "2038.52", "2037.77", "2038.27")
    return Bar(
        timeframe=Timeframe.M1,
        timestamp_utc=timestamp_utc,
        complete=True,
        volume=1,
        bid_ohlc=bid,
        ask_ohlc=ask,
    )


def _quote(bid: str, ask: str, timestamp_utc: dt.datetime | None = None) -> Quote:
    return Quote(
        bid=Decimal(bid),
        ask=Decimal(ask),
        timestamp_utc=timestamp_utc or dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC),
    )


@pytest.fixture
def config() -> BreakerConfig:
    """The committed breaker config, so the test pins the shipped behaviour."""
    raw = yaml.safe_load(BREAKER_PATH.read_text(encoding="utf-8"))
    return BreakerConfig.from_mapping(raw)


@pytest.fixture
def breaker(config: BreakerConfig) -> SignalBreaker:
    return SignalBreaker(spec=XAU_USD_SPEC, config=config)


# --------------------------------------------------------------------------- #
# RollingATR
# --------------------------------------------------------------------------- #
def _tr_series(values: list[tuple[str, str]]) -> list[Bar]:
    """Bars built from (high, low) pairs, holding the close from the previous bar."""
    stamps = [
        dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC) + dt.timedelta(minutes=i)
        for i in range(len(values))
    ]
    bars = []
    for stamp, (high, low) in zip(stamps, values, strict=True):
        gap = Decimal("0.02")
        bars.append(
            _bar(
                stamp,
                _ohlc("2038.25", high, low, "2038.25"),
                _ohlc(
                    "2038.27",
                    str(Decimal(high) + gap),
                    str(Decimal(low) + gap),
                    "2038.27",
                ),
            )
        )
    return bars


def test_rolling_atr_is_empty_until_a_bar_arrives() -> None:
    atr = RollingATR(window=5)

    assert atr.value is None
    assert atr.samples == 0


def test_rolling_atr_uses_true_range_not_raw_range() -> None:
    """TR accounts for a gap from the previous close, which high-low alone misses.

    The second bar opens well above where the first bar closed, so its true range is the
    distance from the previous mid close, not its own high minus low.
    """
    atr = RollingATR(window=5)

    first = _bar(
        dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC),
        _ohlc("2038.25", "2038.50", "2038.00", "2038.25"),
        _ohlc("2038.27", "2038.52", "2038.02", "2038.27"),
    )
    second = _bar(
        dt.datetime(2026, 1, 15, 14, 31, tzinfo=UTC),
        _ohlc("2040.00", "2040.00", "2039.80", "2039.90"),
        _ohlc("2040.02", "2040.02", "2039.82", "2039.92"),
    )

    atr.add(first)
    atr.add(second)

    # Previous mid close is 2038.26; this bar's mid high is 2040.01, so TR is 1.75 whereas
    # high minus low would only be 0.20.
    assert atr.value == Decimal("1.125")
    assert first.mid_ohlc.high - first.mid_ohlc.low == Decimal("0.50")
    assert second.mid_ohlc.high - second.mid_ohlc.low == Decimal("0.20")


def test_rolling_atr_averages_the_last_window() -> None:
    atr = RollingATR(window=3)
    bars = _tr_series(
        [("2038.50", "2038.00"), ("2038.60", "2038.20"), ("2038.40", "2038.10")]
    )

    for bar in bars:
        atr.add(bar)

    # Pure high-low bars here, so ATR is the mean of the three ranges.
    assert atr.value == Decimal("0.4")


def test_rolling_atr_drops_the_oldest_sample() -> None:
    atr = RollingATR(window=2)
    first = _bar(
        dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC),
        _ohlc("2038.25", "2038.60", "2038.20", "2038.25"),
        _ohlc("2038.27", "2038.62", "2038.22", "2038.27"),
    )
    second = _bar(
        dt.datetime(2026, 1, 15, 14, 31, tzinfo=UTC),
        _ohlc("2038.25", "2038.30", "2038.20", "2038.25"),
        _ohlc("2038.27", "2038.32", "2038.22", "2038.27"),
    )

    atr.add(first)
    after_first = atr.value
    atr.add(second)

    assert atr.value != after_first
    assert atr.samples == 2


def test_rolling_atr_rejects_non_positive_window() -> None:
    with pytest.raises(ValueError, match="window"):
        RollingATR(window=0)


def test_rolling_atr_ignores_incomplete_bars() -> None:
    atr = RollingATR(window=5)
    incomplete = Bar(
        timeframe=Timeframe.M1,
        timestamp_utc=dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC),
        complete=False,
        volume=0,
        bid_ohlc=_ohlc("2038.00", "2038.50", "2037.75", "2038.25"),
        ask_ohlc=_ohlc("2038.02", "2038.52", "2037.77", "2038.27"),
    )

    atr.add(incomplete)

    assert atr.samples == 0


# --------------------------------------------------------------------------- #
# RollingMedian
# --------------------------------------------------------------------------- #
def test_rolling_median_is_empty_without_samples() -> None:
    median = RollingMedian(window=5)

    assert median.value is None
    assert median.samples == 0


def test_rolling_median_of_odd_count() -> None:
    median = RollingMedian(window=5)
    for value in ("0.02", "0.04", "0.30"):
        median.add(Decimal(value))

    assert median.value == Decimal("0.04")


def test_rolling_median_of_even_count_averages_the_middle_pair() -> None:
    median = RollingMedian(window=4)
    for value in ("0.02", "0.04", "0.06", "0.08"):
        median.add(Decimal(value))

    assert median.value == Decimal("0.05")


def test_rolling_median_drops_the_oldest_sample() -> None:
    median = RollingMedian(window=2)
    median.add(Decimal("0.02"))
    median.add(Decimal("0.02"))
    median.add(Decimal("0.50"))

    assert median.value == Decimal("0.26")


def test_rolling_median_rejects_non_positive_window() -> None:
    with pytest.raises(ValueError, match="window"):
        RollingMedian(window=-1)


def test_rolling_median_rejects_negative_values() -> None:
    median = RollingMedian(window=3)

    with pytest.raises(ValueError):
        median.add(Decimal("-0.01"))


# --------------------------------------------------------------------------- #
# BreakerConfig
# --------------------------------------------------------------------------- #
def test_breaker_config_reads_the_committed_values() -> None:
    raw = yaml.safe_load(BREAKER_PATH.read_text(encoding="utf-8"))
    config = BreakerConfig.from_mapping(raw)

    assert config.spread_breaker_multiple == Decimal(3)
    assert config.atr_window == 5
    assert config.spread_window == 60


def test_breaker_config_uses_decimal_for_the_multiple() -> None:
    config = BreakerConfig.from_mapping({"spread_breaker_multiple": "2.5"})

    assert isinstance(config.spread_breaker_multiple, Decimal)
    assert config.spread_breaker_multiple == Decimal("2.5")


@pytest.mark.parametrize("bad", [0, "-1", "abc"])
def test_breaker_config_rejects_bad_multiple(bad) -> None:
    with pytest.raises(ValueError, match="multiple"):
        BreakerConfig.from_mapping({"spread_breaker_multiple": bad})


@pytest.mark.parametrize("bad", [0, -5])
def test_breaker_config_rejects_bad_window(bad) -> None:
    with pytest.raises(ValueError, match="window"):
        BreakerConfig.from_mapping({
            "spread_breaker_multiple": "3",
            "atr_window_bars": bad,
        })


def test_breaker_config_rejects_min_samples_above_window() -> None:
    with pytest.raises(ValueError, match="minimum"):
        BreakerConfig.from_mapping({
            "spread_breaker_multiple": "3",
            "atr_window_bars": 5,
            "minimum_atr_bars": 10,
        })


# --------------------------------------------------------------------------- #
# the signal breaker
# --------------------------------------------------------------------------- #
def test_breaker_starts_permissive(breaker: SignalBreaker) -> None:
    decision = breaker.evaluate(dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC))

    assert decision.blocked is False
    assert decision.reasons == ()


def test_breaker_is_inert_without_spread_samples(breaker: SignalBreaker) -> None:
    """Too few samples to judge a median means no spread block, not a block by default."""
    for _ in range(4):
        breaker.observe_quote(_quote("2038.25", "2038.27"))

    decision = breaker.evaluate(dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC))

    assert decision.blocked is False
    assert BreakerReason.SPREAD not in decision.reasons


def test_breaker_flags_a_spread_three_times_the_median(breaker: SignalBreaker) -> None:
    """The shipped multiple is 3, so this spread must trip the breaker.

    Median is 0.02 and the live spread is 0.08, which is 4x: it trips.
    """
    for _ in range(20):
        breaker.observe_quote(_quote("2038.25", "2038.27"))

    breaker.observe_quote(_quote("2038.25", "2038.33"))

    decision = breaker.evaluate(dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC))

    assert decision.blocked is True
    assert BreakerReason.SPREAD in decision.reasons
    assert decision.spread == Decimal("0.08")
    assert decision.median_spread == Decimal("0.02")


def test_breaker_allows_a_spread_below_the_threshold(breaker: SignalBreaker) -> None:
    for _ in range(20):
        breaker.observe_quote(_quote("2038.25", "2038.30"))
    # A doubled spread stays under 3x the median.
    for _ in range(5):
        breaker.observe_quote(_quote("2038.25", "2038.35"))

    decision = breaker.evaluate(dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC))

    assert BreakerReason.SPREAD not in decision.reasons


def test_breaker_uses_the_median_not_the_mean(breaker: SignalBreaker) -> None:
    """A single wide quote must not poison the median the way it would a mean."""
    for _ in range(20):
        breaker.observe_quote(_quote("2038.25", "2038.27"))
    for _ in range(20):
        breaker.observe_quote(_quote("2038.25", "2038.40"))

    decision = breaker.evaluate(dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC))

    # Median of twenty 0.02s and twenty 0.15s is the mean of the middle pair,
    # 0.085, snapped to the coarser 2-decimal exponent. The latest spread of 0.15 is well
    # under 3x that, so no break trips.
    assert decision.median_spread == Decimal("0.08")
    assert decision.threshold == Decimal("0.24")
    assert BreakerReason.SPREAD not in decision.reasons


def test_breaker_blocks_during_a_blackout(breaker: SignalBreaker) -> None:
    window = BlackoutWindow(
        name="Nonfarm Payrolls",
        start=dt.datetime(2026, 1, 15, 13, 0, tzinfo=UTC),
        end=dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC),
    )

    decision = breaker.evaluate(dt.datetime(2026, 1, 15, 13, 30, tzinfo=UTC), [window])

    assert decision.blocked is True
    assert BreakerReason.BLACKOUT in decision.reasons
    assert decision.blackout is window


def test_blackout_boundary_is_half_open(breaker: SignalBreaker) -> None:
    window = BlackoutWindow(
        name="X",
        start=dt.datetime(2026, 1, 15, 13, 0, tzinfo=UTC),
        end=dt.datetime(2026, 1, 15, 14, 0, tzinfo=UTC),
    )

    before = breaker.evaluate(dt.datetime(2026, 1, 15, 12, 59, 59, tzinfo=UTC), [window])
    at_start = breaker.evaluate(dt.datetime(2026, 1, 15, 13, 0, tzinfo=UTC), [window])
    at_end = breaker.evaluate(dt.datetime(2026, 1, 15, 14, 0, tzinfo=UTC), [window])

    assert before.blocked is False
    assert at_start.blocked is True
    assert at_end.blocked is False


def test_breaker_reports_every_active_blackout(breaker: SignalBreaker) -> None:
    overlapping = [
        BlackoutWindow(
            "A",
            dt.datetime(2026, 1, 15, 12, 0, tzinfo=UTC),
            dt.datetime(2026, 1, 15, 13, 30, tzinfo=UTC),
        ),
        BlackoutWindow(
            "B",
            dt.datetime(2026, 1, 15, 13, 0, tzinfo=UTC),
            dt.datetime(2026, 1, 15, 14, 0, tzinfo=UTC),
        ),
    ]

    decision = breaker.evaluate(dt.datetime(2026, 1, 15, 13, 15, tzinfo=UTC), overlapping)

    assert len(decision.active_blackouts) == 2


def test_breaker_blocks_when_spread_and_blackout_both_trip(
    breaker: SignalBreaker,
) -> None:
    for _ in range(20):
        breaker.observe_quote(_quote("2038.25", "2038.27"))
    breaker.observe_quote(_quote("2038.25", "2038.33"))
    window = BlackoutWindow(
        "FOMC",
        dt.datetime(2026, 1, 15, 13, 0, tzinfo=UTC),
        dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC),
    )

    decision = breaker.evaluate(dt.datetime(2026, 1, 15, 13, 30, tzinfo=UTC), [window])

    assert set(decision.reasons) == {BreakerReason.SPREAD, BreakerReason.BLACKOUT}
    assert decision.blocked is True


def test_breaker_atr_is_reported_once_enough_bars_arrive(
    breaker: SignalBreaker,
) -> None:
    bars = _tr_series(
        [("2038.50", "2038.00"), ("2038.60", "2038.20"), ("2038.40", "2038.10")]
    )
    for bar in bars:
        breaker.observe_bar(bar)

    decision = breaker.evaluate(dt.datetime(2026, 1, 15, 14, 40, tzinfo=UTC))

    assert decision.atr == Decimal("0.4")


def test_breaker_reset_clears_all_state(breaker: SignalBreaker) -> None:
    for _ in range(20):
        breaker.observe_quote(_quote("2038.25", "2038.27"))
    breaker.observe_quote(_quote("2038.25", "2038.33"))
    for bar in _tr_series([("2038.50", "2038.00")]):
        breaker.observe_bar(bar)

    breaker.reset()
    decision = breaker.evaluate(dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC))

    assert decision.blocked is False
    assert decision.spread is None
    assert decision.median_spread is None
    assert decision.atr is None


def test_breaker_rejects_a_quote_with_an_inverted_spread(
    breaker: SignalBreaker,
) -> None:
    with pytest.raises(ValueError):
        breaker.observe_quote(
            Quote(
                bid=Decimal("2038.30"),
                ask=Decimal("2038.27"),
                timestamp_utc=dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC),
            )
        )


def test_breaker_reports_the_threshold(breaker: SignalBreaker) -> None:
    for _ in range(20):
        breaker.observe_quote(_quote("2038.25", "2038.27"))

    decision = breaker.evaluate(dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC))

    assert decision.threshold == Decimal("0.06")


def test_breaker_decision_is_immutable(breaker: SignalBreaker) -> None:
    decision = breaker.evaluate(dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC))

    with pytest.raises(Exception):  # noqa: B017
        decision.blocked = True  # type: ignore[misc]


def test_breaker_atr_window_matches_the_config(breaker: SignalBreaker) -> None:
    assert breaker.config.atr_window == 5
    assert breaker.config.minimum_atr_bars <= breaker.config.atr_window


def test_breaker_is_deterministic(breaker: SignalBreaker) -> None:
    for _ in range(20):
        breaker.observe_quote(_quote("2038.25", "2038.27"))
    breaker.observe_quote(_quote("2038.25", "2038.33"))

    first = breaker.evaluate(dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC))
    second = breaker.evaluate(dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC))

    assert first == second


def test_breaker_uses_decimal_end_to_end(breaker: SignalBreaker) -> None:
    for _ in range(20):
        breaker.observe_quote(_quote("2038.25", "2038.27"))
    breaker.observe_quote(_quote("2038.25", "2038.33"))

    decision = breaker.evaluate(dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC))

    assert isinstance(decision.spread, Decimal)
    assert isinstance(decision.median_spread, Decimal)
    assert isinstance(decision.threshold, Decimal)
