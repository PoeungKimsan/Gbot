"""Unit tests for :mod:`engine.market.models`.

The invariants that matter for a fill engine: prices are exact Decimals, timestamps are
always UTC, bid/ask never invert, OHLC bars are internally consistent, and ``mid_ohlc`` is
derived deterministically from bid and ask.
"""

import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from engine.market.models import OHLC, Bar, Quote, Timeframe, mid_of, quantize_to

UTC = dt.UTC
NEW_YORK = ZoneInfo("America/New_York")


def _ny(*args: int) -> dt.datetime:
    return dt.datetime(*args, tzinfo=NEW_YORK)


# --------------------------------------------------------------------------- #
# timeframes
# --------------------------------------------------------------------------- #
def test_timeframe_members() -> None:
    assert {tf.value for tf in Timeframe} == {"M1", "M5"}


def test_timeframe_duration() -> None:
    assert Timeframe.M1.duration == dt.timedelta(minutes=1)
    assert Timeframe.M5.duration == dt.timedelta(minutes=5)


def test_timeframe_from_value() -> None:
    assert Timeframe.from_value("M1") is Timeframe.M1
    assert Timeframe.from_value("m5") is Timeframe.M5
    assert Timeframe.from_value("H1") is None


# --------------------------------------------------------------------------- #
# quantize / mid helpers
# --------------------------------------------------------------------------- #
def test_mid_of_averages_bid_and_ask() -> None:
    assert mid_of(Decimal("2038.25"), Decimal("2038.27")) == Decimal("2038.26")


def test_mid_of_handles_odd_sum_by_halving_even() -> None:
    """A spread of one tick leaves a half-tick mid, which must round half-even."""
    assert mid_of(Decimal("2038.26"), Decimal("2038.27")) == Decimal("2038.26")
    assert mid_of(Decimal("2038.27"), Decimal("2038.28")) == Decimal("2038.28")


def test_mid_of_equal_prices() -> None:
    assert mid_of(Decimal("2038.25"), Decimal("2038.25")) == Decimal("2038.25")


def test_mid_of_rejects_inverted_quotes() -> None:
    with pytest.raises(ValueError, match="ask"):
        mid_of(Decimal("2038.28"), Decimal("2038.27"))


def test_quantize_to_snaps_to_grid() -> None:
    assert quantize_to(Decimal("2038.2537"), Decimal("0.01")) == Decimal("2038.25")
    assert quantize_to(Decimal("2038.00004"), Decimal("0.0001")) == Decimal("2038.0000")


# --------------------------------------------------------------------------- #
# Quote
# --------------------------------------------------------------------------- #
def test_quote_is_immutable() -> None:
    quote = Quote(
        bid=Decimal("2038.25"),
        ask=Decimal("2038.27"),
        timestamp_utc=dt.datetime(2026, 1, 15, tzinfo=UTC),
    )
    with pytest.raises(Exception):  # noqa: B017
        quote.bid = Decimal("1")  # type: ignore[misc]


def test_quote_spread_and_mid() -> None:
    quote = Quote(
        bid=Decimal("2038.25"),
        ask=Decimal("2038.27"),
        timestamp_utc=dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC),
    )
    assert quote.spread == Decimal("0.02")
    assert quote.mid == Decimal("2038.26")


def test_quote_normalizes_non_utc_timestamps_to_utc() -> None:
    quote = Quote(
        bid=Decimal("2038.25"),
        ask=Decimal("2038.27"),
        timestamp_utc=dt.datetime(2026, 1, 15, 9, 30, tzinfo=NEW_YORK),
    )
    assert quote.timestamp_utc == dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC)


def test_quote_rejects_naive_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone"):
        Quote(
            bid=Decimal("2038.25"),
            ask=Decimal("2038.27"),
            timestamp_utc=dt.datetime(2026, 1, 15, 14, 30),
        )


def test_quote_rejects_inverted_prices() -> None:
    with pytest.raises(ValueError, match="ask"):
        Quote(
            bid=Decimal("2038.28"),
            ask=Decimal("2038.27"),
            timestamp_utc=dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC),
        )


def test_quote_rejects_equal_prices() -> None:
    """A zero spread is a crossed or frozen book, not a quotable spread."""
    with pytest.raises(ValueError):
        Quote(
            bid=Decimal("2038.25"),
            ask=Decimal("2038.25"),
            timestamp_utc=dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC),
        )


def test_quote_rejects_non_positive_prices() -> None:
    for bad_bid, bad_ask in (("0", "1"), ("-1", "2")):
        with pytest.raises(ValueError):
            Quote(
                bid=Decimal(bad_bid),
                ask=Decimal(bad_ask),
                timestamp_utc=dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC),
            )


def test_quote_rejects_float_prices() -> None:
    with pytest.raises(ValueError, match="Decimal"):
        Quote(
            bid=2038.25,  # type: ignore[arg-type]
            ask=2038.27,  # type: ignore[arg-type]
            timestamp_utc=dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC),
        )


def test_quote_rejects_non_finite_prices() -> None:
    with pytest.raises(ValueError):
        Quote(
            bid=Decimal("NaN"),
            ask=Decimal("Infinity"),
            timestamp_utc=dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC),
        )


def test_quote_accepts_string_prices_as_exact_decimals() -> None:
    """OANDA sends prices as JSON strings; they must not pass through a float."""
    quote = Quote.from_bid_ask_strings(
        "2038.25", "2038.27", dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC)
    )
    assert quote.bid == Decimal("2038.25")


# --------------------------------------------------------------------------- #
# OHLC
# --------------------------------------------------------------------------- #
def _ohlc(opn: str, high: str, low: str, close: str) -> OHLC:
    return OHLC(
        open=Decimal(opn),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
    )


def test_ohlc_is_immutable() -> None:
    ohlc = _ohlc("2038.00", "2038.50", "2037.75", "2038.25")
    with pytest.raises(Exception):  # noqa: B017
        ohlc.high = Decimal("2039")  # type: ignore[misc]


def _valid_ohld() -> list[OHLC]:
    return [
        _ohlc("2038.00", "2038.50", "2037.75", "2038.25"),
        _ohlc("2038.00", "2038.50", "2038.00", "2038.00"),
        _ohlc("2038.00", "2038.00", "2037.50", "2037.50"),
        _ohlc("5", "5", "5", "5"),
    ]


@pytest.mark.parametrize("ohlc", _valid_ohld())
def test_ohlc_accepts_consistent_bars(ohlc: OHLC) -> None:
    assert ohlc.high >= ohlc.low
    assert ohlc.high >= ohlc.open
    assert ohlc.high >= ohlc.close
    assert ohlc.low <= ohlc.open
    assert ohlc.low <= ohlc.close


@pytest.mark.parametrize(
    ("opn", "high", "low", "close"),
    [
        ("2038.60", "2038.50", "2037.75", "2038.25"),  # open above high
        ("2037.50", "2038.50", "2037.75", "2038.25"),  # open below low
        ("2038.00", "2038.50", "2037.75", "2038.90"),  # close above high
        ("2038.00", "2038.50", "2037.75", "2037.00"),  # close below low
        ("2038.00", "2037.50", "2038.00", "2038.25"),  # high below low
    ],
)
def test_ohlc_rejects_impossible_bars(opn: str, high: str, low: str, close: str) -> None:
    with pytest.raises(ValueError):
        _ohlc(opn, high, low, close)


def test_ohlc_rejects_float_components() -> None:
    with pytest.raises(ValueError, match="Decimal"):
        OHLC(
            open=2038.0,  # type: ignore[arg-type]
            high=Decimal("2038.50"),
            low=Decimal("2037.75"),
            close=Decimal("2038.25"),
        )


def test_ohlc_rejects_non_finite_components() -> None:
    with pytest.raises(ValueError):
        OHLC(
            open=Decimal("NaN"),
            high=Decimal("2038.50"),
            low=Decimal("2037.75"),
            close=Decimal("2038.25"),
        )


# --------------------------------------------------------------------------- #
# Bar
# --------------------------------------------------------------------------- #
def _bar(
    timeframe: Timeframe = Timeframe.M1,
    timestamp: dt.datetime = dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC),
    complete: bool = True,
    volume: int = 0,
    bid: OHLC | None = None,
    ask: OHLC | None = None,
) -> Bar:
    return Bar(
        timeframe=timeframe,
        timestamp_utc=timestamp,
        complete=complete,
        volume=volume,
        bid_ohlc=bid or _ohlc("2038.00", "2038.50", "2037.75", "2038.25"),
        ask_ohlc=ask or _ohlc("2038.02", "2038.52", "2037.77", "2038.27"),
    )


def test_bar_is_immutable() -> None:
    bar = _bar()
    with pytest.raises(Exception):  # noqa: B017
        bar.complete = False  # type: ignore[misc]


def test_bar_mid_ohlc_derives_from_bid_and_ask() -> None:
    bar = _bar(
        bid=_ohlc("2038.00", "2038.50", "2037.75", "2038.25"),
        ask=_ohlc("2038.02", "2038.52", "2037.77", "2038.27"),
    )

    mid = bar.mid_ohlc
    assert mid.open == Decimal("2038.01")
    assert mid.high == Decimal("2038.51")
    assert mid.low == Decimal("2037.76")
    assert mid.close == Decimal("2038.26")


def test_bar_mid_ohlc_is_a_valid_ohlc() -> None:
    mid = _bar().mid_ohlc
    assert mid.high >= mid.open
    assert mid.high >= mid.close
    assert mid.low <= mid.open
    assert mid.low <= mid.close


def test_bar_mid_ohlc_is_recomputed_not_stored() -> None:
    """Two bars with identical bid/ask must agree, and mid must not affect equality."""
    first = _bar()
    second = _bar()
    assert first.mid_ohlc == second.mid_ohlc
    assert first == second


def test_bar_spread_ohlc_reports_per_side_spread() -> None:
    bar = _bar(
        bid=_ohlc("2038.00", "2038.50", "2037.75", "2038.25"),
        ask=_ohlc("2038.05", "2038.55", "2037.80", "2038.30"),
    )
    spread = bar.spread_ohlc
    assert spread.open == Decimal("0.05")
    assert spread.high == Decimal("0.05")
    assert spread.low == Decimal("0.05")
    assert spread.close == Decimal("0.05")


def test_bar_rejects_ask_below_bid() -> None:
    with pytest.raises(ValueError, match="ask"):
        _bar(
            bid=_ohlc("2038.00", "2038.50", "2037.75", "2038.25"),
            ask=_ohlc("2037.90", "2038.40", "2037.70", "2038.15"),
        )


def test_bar_rejects_inverted_close() -> None:
    """A mid below the bid low is impossible: ask >= bid everywhere."""
    with pytest.raises(ValueError):
        _bar(
            bid=_ohlc("2038.00", "2038.50", "2037.75", "2038.25"),
            ask=_ohlc("2038.02", "2038.49", "2037.77", "2038.15"),
        )


def test_bar_rejects_naive_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone"):
        _bar(timestamp=dt.datetime(2026, 1, 15, 14, 30))


def test_bar_normalizes_non_utc_timestamp() -> None:
    bar = _bar(timestamp=_ny(2026, 1, 15, 9, 30))
    assert bar.timestamp_utc == dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC)


def test_bar_rejects_negative_volume() -> None:
    with pytest.raises(ValueError):
        _bar(volume=-1)


def test_bar_rejects_float_volume() -> None:
    with pytest.raises(ValueError, match="volume"):
        _bar(volume=1.5)  # type: ignore[arg-type]


def test_bar_accepts_complete_and_incomplete() -> None:
    assert _bar(complete=True).complete is True
    assert _bar(complete=False).complete is False


def test_bar_is_hashable() -> None:
    assert hash(_bar()) == hash(_bar())


def test_bar_ordering_by_timestamp() -> None:
    early = _bar(timestamp=dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC))
    late = _bar(timestamp=dt.datetime(2026, 1, 15, 14, 31, tzinfo=UTC))
    assert early < late
