"""Unit tests for :mod:`engine.market.builder`.

The behaviours under test: M1 aggregation from quotes, M5 aggregation from M1 bars,
session anchoring at the 17:00 America/New_York close, DST correctness (the anchor must
be wall-clock, not a fixed UTC hour), and reconciliation of streamed bars against
historical candles with a 1-pip tolerance.
"""

import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from engine.market.builder import (
    BarAggregator,
    BarBuilder,
    BarReconciler,
    DiscrepancyKind,
    SessionAnchor,
    bucket_start,
)
from engine.market.instrument import InstrumentSpec
from engine.market.models import OHLC, Bar, Quote, Timeframe

UTC = dt.UTC
NEW_YORK = ZoneInfo("America/New_York")

#: The 17:00 New York session close, expressed in UTC. In January EST is UTC-5, so this
#: is a 22:00 UTC instant.
SESSION_OPEN_UTC = dt.datetime(2026, 1, 15, 17, 0, tzinfo=NEW_YORK).astimezone(UTC)

XAU_USD_SPEC = InstrumentSpec(
    name="XAU_USD",
    pip_location=-4,
    display_precision=2,
    trade_units_precision=5,
    minimum_trade_size=Decimal("1"),
)


def _quote(
    bid: str,
    ask: str,
    timestamp_utc: dt.datetime,
) -> Quote:
    return Quote(bid=Decimal(bid), ask=Decimal(ask), timestamp_utc=timestamp_utc)


def _m1_bar(timestamp_utc: dt.datetime, bid: OHLC, ask: OHLC, volume: int = 0) -> Bar:
    return Bar(
        timeframe=Timeframe.M1,
        timestamp_utc=timestamp_utc,
        complete=True,
        volume=volume,
        bid_ohlc=bid,
        ask_ohlc=ask,
    )


def _ohlc(opn: str, high: str, low: str, close: str) -> OHLC:
    return OHLC(open=Decimal(opn), high=Decimal(high), low=Decimal(low), close=Decimal(close))


# --------------------------------------------------------------------------- #
# bucket alignment
# --------------------------------------------------------------------------- #
def test_m1_bucket_floors_to_the_minute() -> None:
    stamp = dt.datetime(2026, 1, 15, 14, 30, 47, tzinfo=UTC)

    assert bucket_start(stamp, Timeframe.M1) == dt.datetime(2026, 1, 15, 14, 30, 0, tzinfo=UTC)


def test_m5_bucket_floors_to_the_five_minute_grid() -> None:
    stamp = dt.datetime(2026, 1, 15, 14, 33, 47, tzinfo=UTC)

    assert bucket_start(stamp, Timeframe.M5) == dt.datetime(2026, 1, 15, 14, 30, 0, tzinfo=UTC)


def test_m5_bucket_is_aligned_on_the_utc_grid() -> None:
    stamp = dt.datetime(2026, 1, 15, 14, 59, 1, tzinfo=UTC)

    assert bucket_start(stamp, Timeframe.M5) == dt.datetime(2026, 1, 15, 14, 55, 0, tzinfo=UTC)


def test_bucket_start_accepts_any_timezone() -> None:
    ny = dt.datetime(2026, 1, 15, 9, 30, 47, tzinfo=NEW_YORK)

    assert bucket_start(ny, Timeframe.M1) == dt.datetime(2026, 1, 15, 14, 30, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# session anchoring
# --------------------------------------------------------------------------- #
def test_session_anchor_uses_17_00_new_york() -> None:
    anchor = SessionAnchor(close_hour=17, tz=NEW_YORK)

    assert anchor.session_start(SESSION_OPEN_UTC) == SESSION_OPEN_UTC


def test_session_boundaries_span_24_hours_of_wall_clock() -> None:
    anchor = SessionAnchor(close_hour=17, tz=NEW_YORK)
    start = anchor.session_start(SESSION_OPEN_UTC)
    end = anchor.session_end(SESSION_OPEN_UTC)

    assert start == SESSION_OPEN_UTC  # the anchor instant itself, 22:00 UTC
    assert end == dt.datetime(2026, 1, 16, 22, 0, tzinfo=UTC)
    # 25 wall-clock hours from 17:00 EST to 17:00 EST-the-next-day span a 23-hour UTC
    # delta, which is exactly why the anchor must be wall-clock rather than a fixed UTC hour.
    assert end - start == dt.timedelta(hours=24)


def test_session_start_rolls_back_before_the_close() -> None:
    """A timestamp before 17:00 NY belongs to the previous session."""
    anchor = SessionAnchor(close_hour=17, tz=NEW_YORK)
    before_close = dt.datetime(2026, 1, 16, 10, 0, tzinfo=UTC)

    assert anchor.session_start(before_close) == SESSION_OPEN_UTC


@pytest.mark.parametrize(
    ("instant_utc", "expected_start"),
    [
        # 16:59:59 EST on Jan 15 is one second BEFORE the close: still in the session
        # that opened 17:00 EST on Jan 14 (22:00 UTC).
        (
            dt.datetime(2026, 1, 15, 21, 59, 59, tzinfo=UTC),
            dt.datetime(2026, 1, 14, 22, 0, tzinfo=UTC),
        ),
        # Exactly 17:00 EST on Jan 15 (22:00 UTC): the NEXT session opens now.
        (
            dt.datetime(2026, 1, 15, 22, 0, 0, tzinfo=UTC),
            dt.datetime(2026, 1, 15, 22, 0, tzinfo=UTC),
        ),
        (dt.datetime(2026, 1, 16, 12, 0, tzinfo=UTC), dt.datetime(2026, 1, 15, 22, 0, tzinfo=UTC)),
        (
            dt.datetime(2026, 1, 16, 21, 59, 59, tzinfo=UTC),
            dt.datetime(2026, 1, 15, 22, 0, tzinfo=UTC),
        ),
    ],
)
def test_session_start_assigns_the_next_session_after_close(instant_utc, expected_start) -> None:
    anchor = SessionAnchor(close_hour=17, tz=NEW_YORK)

    assert anchor.session_start(instant_utc) == expected_start


def test_session_start_is_idempotent_on_the_boundary() -> None:
    anchor = SessionAnchor(close_hour=17, tz=NEW_YORK)

    assert anchor.session_start(SESSION_OPEN_UTC) == anchor.session_start(SESSION_OPEN_UTC)


def test_session_anchor_tracks_dst_in_summer() -> None:
    """EDT is UTC-4, so 17:00 New York is 21:00 UTC, one hour earlier than EST."""
    anchor = SessionAnchor(close_hour=17, tz=NEW_YORK)
    summer_close = dt.datetime(2026, 7, 15, 17, 0, tzinfo=NEW_YORK).astimezone(UTC)

    start = anchor.session_start(summer_close)

    assert start == dt.datetime(2026, 7, 15, 21, 0, tzinfo=UTC)
    assert start.hour == 21


def test_anchored_session_reports_the_spanning_duration() -> None:
    anchor = SessionAnchor(close_hour=17, tz=NEW_YORK)

    session = anchor.session(SESSION_OPEN_UTC)

    assert session.start == SESSION_OPEN_UTC
    assert session.end == dt.datetime(2026, 1, 16, 22, 0, tzinfo=UTC)


def test_m1_bars_survive_the_spring_forward_gap() -> None:
    """No bar may be emitted for a clock hour that does not exist on 2026-03-08.

    On that date New York jumps from 01:59 EST (06:59 UTC) straight to 03:00 EDT
    (07:00 UTC). Bucketing happens in UTC, so the missing hour simply produces no
    buckets at all: nothing is fabricated and nothing is duplicated.
    """
    builder = BarBuilder()

    before = dt.datetime(2026, 3, 8, 6, 0, tzinfo=UTC)
    after = dt.datetime(2026, 3, 8, 9, 0, tzinfo=UTC)

    cursor = before
    emitted: list[Bar] = []
    while cursor <= after:
        emitted.extend(
            builder.add_quote(
                _quote("2038.25", "2038.27", cursor + dt.timedelta(seconds=30))
            )
        )
        cursor = cursor + dt.timedelta(minutes=1)

    buckets = [bar.timestamp_utc for bar in emitted]
    assert before.replace(minute=0) in buckets
    assert before.replace(minute=59) in buckets

    # The hour New York skipped on 2026-03-08 is 07:00..07:59 UTC. Because bucketing is
    # done in UTC, that hour simply has no buckets at all: nothing was fabricated for the
    # nonexistent 02:00-03:00 local time, and nothing was duplicated for the repeated hour
    # in November. Strictly-advancing UTC minutes produce strictly-increasing buckets.
    assert buckets == sorted(set(buckets))
    assert len(buckets) == len(set(buckets))


def test_session_anchor_can_be_built_from_local_hour() -> None:
    anchor = SessionAnchor(close_hour=17, tz=NEW_YORK)

    assert anchor.close_hour == 17
    assert anchor.tz.key == "America/New_York"


# --------------------------------------------------------------------------- #
# M1 aggregation
# --------------------------------------------------------------------------- #
def _builder() -> BarBuilder:
    return BarBuilder(spec=XAU_USD_SPEC)


def test_builder_emits_nothing_until_the_bucket_changes() -> None:
    builder = _builder()

    quotes = [
        _quote("2038.25", "2038.27", dt.datetime(2026, 1, 15, 14, 30, 5, tzinfo=UTC)),
        _quote("2038.26", "2038.28", dt.datetime(2026, 1, 15, 14, 30, 40, tzinfo=UTC)),
    ]
    emitted = _run(builder, quotes)

    assert emitted == []
    assert builder.pending is not None


def test_builder_completes_a_bar_when_the_bucket_rolls() -> None:
    builder = _builder()

    quotes = [
        _quote("2038.25", "2038.27", dt.datetime(2026, 1, 15, 14, 30, 5, tzinfo=UTC)),
        _quote("2038.26", "2038.28", dt.datetime(2026, 1, 15, 14, 30, 40, tzinfo=UTC)),
        _quote("2038.30", "2038.32", dt.datetime(2026, 1, 15, 14, 31, 5, tzinfo=UTC)),
    ]
    emitted = _run(builder, quotes)

    assert len(emitted) == 1
    bar = emitted[0]
    assert bar.timestamp_utc == dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC)
    assert bar.bid_ohlc == _ohlc("2038.25", "2038.26", "2038.25", "2038.26")
    assert bar.ask_ohlc == _ohlc("2038.27", "2038.28", "2038.27", "2038.28")
    assert bar.complete is True
    assert bar.timeframe == Timeframe.M1


def _run(builder: BarBuilder, quotes: list[Quote]) -> list[Bar]:
    emitted: list[Bar] = []
    for quote in quotes:
        emitted.extend(builder.add_quote(quote))
    return emitted


def test_builder_tracks_high_and_low_across_the_bucket() -> None:
    builder = _builder()

    quotes = [
        _quote("2038.25", "2038.27", dt.datetime(2026, 1, 15, 14, 30, 5, tzinfo=UTC)),
        _quote("2038.40", "2038.42", dt.datetime(2026, 1, 15, 14, 30, 20, tzinfo=UTC)),
        _quote("2038.10", "2038.12", dt.datetime(2026, 1, 15, 14, 30, 35, tzinfo=UTC)),
        _quote("2038.30", "2038.32", dt.datetime(2026, 1, 15, 14, 30, 55, tzinfo=UTC)),
        _quote("2038.99", "2039.01", dt.datetime(2026, 1, 15, 14, 31, 1, tzinfo=UTC)),
    ]
    emitted = _run(builder, quotes)

    # The 14:31 quote opens a new bar, so only 14:30's range is reported.
    assert len(emitted) == 1
    assert emitted[0].bid_ohlc.high == Decimal("2038.40")
    assert emitted[0].bid_ohlc.low == Decimal("2038.10")


def test_builder_ignores_late_quotes_outside_the_current_bucket() -> None:
    builder = _builder()

    quotes = [
        _quote("2038.25", "2038.27", dt.datetime(2026, 1, 15, 14, 31, 5, tzinfo=UTC)),
        _quote("2038.99", "2039.01", dt.datetime(2026, 1, 15, 14, 30, 5, tzinfo=UTC)),
        _quote("2038.30", "2038.32", dt.datetime(2026, 1, 15, 14, 31, 40, tzinfo=UTC)),
        _quote("2038.31", "2038.33", dt.datetime(2026, 1, 15, 14, 32, 5, tzinfo=UTC)),
    ]
    emitted = _run(builder, quotes)

    # The out-of-order 14:30 quote must not corrupt the 14:31 bar.
    assert [bar.timestamp_utc for bar in emitted] == [
        dt.datetime(2026, 1, 15, 14, 31, tzinfo=UTC)
    ]
    assert emitted[0].bid_ohlc == _ohlc("2038.25", "2038.30", "2038.25", "2038.30")


def test_builder_flushes_the_open_bar() -> None:
    builder = _builder()
    _run(builder, [_quote("2038.25", "2038.27", dt.datetime(2026, 1, 15, 14, 30, 5, tzinfo=UTC))])
    assert builder.pending is not None

    flushed = builder.flush()

    assert flushed is not None
    assert flushed.bid_ohlc.open == Decimal("2038.25")
    assert flushed.bid_ohlc.close == Decimal("2038.25")
    assert builder.pending is None


def test_builder_mid_bars_are_derived_not_stored() -> None:
    builder = _builder()
    quotes = [
        _quote("2038.25", "2038.27", dt.datetime(2026, 1, 15, 14, 30, 5, tzinfo=UTC)),
        _quote("2038.30", "2038.32", dt.datetime(2026, 1, 15, 14, 31, 5, tzinfo=UTC)),
    ]
    emitted = _run(builder, quotes)

    assert emitted[0].mid_ohlc.open == Decimal("2038.26")


def test_builder_tracks_volume_from_quote_count() -> None:
    builder = _builder()
    quotes = [
        _quote("2038.25", "2038.27", dt.datetime(2026, 1, 15, 14, 30, 5, tzinfo=UTC)),
        _quote("2038.26", "2038.28", dt.datetime(2026, 1, 15, 14, 30, 40, tzinfo=UTC)),
        _quote("2038.30", "2038.32", dt.datetime(2026, 1, 15, 14, 31, 5, tzinfo=UTC)),
    ]
    emitted = _run(builder, quotes)

    assert emitted[0].volume == 2


# --------------------------------------------------------------------------- #
# M5 aggregation
# --------------------------------------------------------------------------- #
def test_m5_aggregation_from_m1_bars() -> None:
    aggregator = BarAggregator(spec=XAU_USD_SPEC)

    emitted = aggregator.add(
        _m1_bar(
            dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC),
            _ohlc("2038.00", "2038.50", "2037.75", "2038.25"),
            _ohlc("2038.02", "2038.52", "2037.77", "2038.27"),
        )
    )
    emitted += aggregator.add(
        _m1_bar(
            dt.datetime(2026, 1, 15, 14, 31, tzinfo=UTC),
            _ohlc("2038.25", "2038.60", "2038.20", "2038.55"),
            _ohlc("2038.27", "2038.62", "2038.22", "2038.57"),
        )
    )
    emitted += aggregator.add(
        _m1_bar(
            dt.datetime(2026, 1, 15, 14, 32, tzinfo=UTC),
            _ohlc("2038.50", "2038.70", "2038.40", "2038.65"),
            _ohlc("2038.52", "2038.72", "2038.42", "2038.67"),
        )
    )

    # Bar is not complete until the bucket rolls.
    assert emitted == []

    emitted += aggregator.add(
        _m1_bar(
            dt.datetime(2026, 1, 15, 14, 35, tzinfo=UTC),
            _ohlc("2039.00", "2039.10", "2038.90", "2039.05"),
            _ohlc("2039.02", "2039.12", "2038.92", "2039.07"),
        )
    )

    assert len(emitted) == 1
    bar = emitted[0]
    assert bar.timestamp_utc == dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC)
    assert bar.timeframe == Timeframe.M5
    assert bar.bid_ohlc == _ohlc("2038.00", "2038.70", "2037.75", "2038.65")
    assert bar.mid_ohlc.high == Decimal("2038.71")


def test_m5_aggregation_sums_volume() -> None:
    aggregator = BarAggregator(spec=XAU_USD_SPEC)
    for minute, volume in ((30, 10), (31, 20), (32, 30)):
        aggregator.add(
            _m1_bar(
                dt.datetime(2026, 1, 15, 14, minute, tzinfo=UTC),
                _ohlc("2038.00", "2038.50", "2037.75", "2038.25"),
                _ohlc("2038.02", "2038.52", "2037.77", "2038.27"),
                volume=volume,
            )
        )

    emitted = aggregator.add(
        _m1_bar(
            dt.datetime(2026, 1, 15, 14, 35, tzinfo=UTC),
            _ohlc("2039.00", "2039.10", "2038.90", "2039.05"),
            _ohlc("2039.02", "2039.12", "2038.92", "2039.07"),
        )
    )

    assert emitted[0].volume == 60


def test_m5_aggregation_flushes_partial() -> None:
    aggregator = BarAggregator(spec=XAU_USD_SPEC)
    aggregator.add(
        _m1_bar(
            dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC),
            _ohlc("2038.00", "2038.50", "2037.75", "2038.25"),
            _ohlc("2038.02", "2038.52", "2037.77", "2038.27"),
        )
    )

    flushed = aggregator.flush()

    assert flushed is not None
    assert flushed.bid_ohlc.close == Decimal("2038.25")


def test_m5_buckets_line_up_with_the_m1_grid() -> None:
    aggregator = BarAggregator(spec=XAU_USD_SPEC)

    emitted = aggregator.add(
        _m1_bar(
            dt.datetime(2026, 1, 15, 14, 34, tzinfo=UTC),
            _ohlc("2038.00", "2038.50", "2037.75", "2038.25"),
            _ohlc("2038.02", "2038.52", "2037.77", "2038.27"),
        )
    )
    emitted += aggregator.add(
        _m1_bar(
            dt.datetime(2026, 1, 15, 14, 35, tzinfo=UTC),
            _ohlc("2039.00", "2039.10", "2038.90", "2039.05"),
            _ohlc("2039.02", "2039.12", "2038.92", "2039.07"),
        )
    )

    assert emitted[0].timestamp_utc == dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC)



def _aligned_bar(
    timestamp_utc: dt.datetime,
    *,
    open_: str = "2038.00",
    high: str | None = None,
    low: str = "2037.75",
    close: str = "2038.25",
    spread: str = "0.02",
) -> Bar:
    """Build a valid M1 bar where every ask field is ``spread`` above its bid field.

    The bar model rejects a crossed book, so a test that wants exactly one bid field to
    differ must move the matching ask field too. Overriding ``close`` lifts the high to
    fit it and moves both sides, leaving mid as the only other field that changes.
    """
    gap = Decimal(spread)
    if high is None:
        high = max(str(Decimal("2038.50")), close)
    bid = _ohlc(open_, high, low, close)
    ask = _ohlc(
        str(Decimal(open_) + gap),
        str(Decimal(high) + gap),
        str(Decimal(low) + gap),
        str(Decimal(close) + gap),
    )
    return _m1_bar(timestamp_utc, bid, ask)


# --------------------------------------------------------------------------- #
# reconciliation
# --------------------------------------------------------------------------- #
def _historical_bar(timestamp_utc: dt.datetime) -> Bar:
    return _m1_bar(
        timestamp_utc,
        _ohlc("2038.00", "2038.50", "2037.75", "2038.25"),
        _ohlc("2038.02", "2038.52", "2037.77", "2038.27"),
    )


def test_reconciler_accepts_matching_bars() -> None:
    reconciler = BarReconciler(spec=XAU_USD_SPEC)
    streamed = [_historical_bar(dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC))]
    historical = [_historical_bar(dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC))]

    assert reconciler.reconcile(streamed, historical) == []


def test_reconciler_reports_a_missing_bar() -> None:
    reconciler = BarReconciler(spec=XAU_USD_SPEC)
    streamed = []
    historical = [_historical_bar(dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC))]

    discrepancies = reconciler.reconcile(streamed, historical)

    assert len(discrepancies) == 1
    assert discrepancies[0].kind is DiscrepancyKind.MISSING
    assert discrepancies[0].bucket_start == dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC)


def test_reconciler_reports_an_unexpected_bar() -> None:
    reconciler = BarReconciler(spec=XAU_USD_SPEC)
    streamed = [_historical_bar(dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC))]
    historical = []

    discrepancies = reconciler.reconcile(streamed, historical)

    assert len(discrepancies) == 1
    assert discrepancies[0].kind is DiscrepancyKind.UNEXPECTED


def test_reconciler_reports_a_price_difference_beyond_tolerance() -> None:
    """Only the bid close differs, and by far more than one pip."""
    reconciler = BarReconciler(spec=XAU_USD_SPEC)
    streamed = [_aligned_bar(dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC), close="2039.99")]
    historical = [_historical_bar(dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC))]

    discrepancies = reconciler.reconcile(streamed, historical)

    by_field = {d.detail["field"]: d.difference for d in discrepancies}
    # Raising close above the old high moves the high too, and because the spread is held
    # constant the ask side follows, so exactly these fields disagree. Fields are reported
    # in the reconciler's fixed comparison order.
    assert set(by_field) == {
        "bid_high", "ask_high", "bid_close", "ask_close", "mid_close", "mid_high",
    }
    assert by_field["bid_close"] == Decimal("1.74")
    assert by_field["ask_close"] == Decimal("1.74")
    # Mid tracks both sides moving together, so it shifts by the full amount.
    assert by_field["mid_close"] == Decimal("1.74")
    # The high also moved to fit the new close: 2039.99 + 0.02 minus the historical 2038.52.
    assert by_field["ask_high"] == Decimal("1.49")
    assert by_field["bid_high"] == Decimal("1.49")
    assert by_field["mid_high"] == Decimal("1.49")
    assert all(d.kind is DiscrepancyKind.PRICE for d in discrepancies)


def test_reconciler_tolerates_exactly_one_pip() -> None:
    """A difference of exactly one pip is within tolerance, not above it.

    Only the high differs, and by exactly one pip (0.0001 for XAU_USD).
    """
    reconciler = BarReconciler(spec=XAU_USD_SPEC)
    streamed = [_aligned_bar(dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC), high="2038.5001")]
    historical = [_historical_bar(dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC))]

    assert reconciler.reconcile(streamed, historical) == []


def test_reconciler_flags_a_difference_one_tick_above_tolerance() -> None:
    """One pip plus one tick (0.0002 above 0.5000) exceeds the one-pip tolerance."""
    reconciler = BarReconciler(spec=XAU_USD_SPEC)
    streamed = [_aligned_bar(dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC), high="2038.5002")]
    historical = [_historical_bar(dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC))]

    discrepancies = reconciler.reconcile(streamed, historical)

    assert {d.detail["field"] for d in discrepancies} == {
        "bid_high",
        "ask_high",
        "mid_high",
    }
    bid_high = next(d for d in discrepancies if d.detail["field"] == "bid_high")
    assert bid_high.kind is DiscrepancyKind.PRICE
    assert bid_high.difference == Decimal("0.0002")


def test_reconciler_ignores_incomplete_streamed_bars() -> None:
    reconciler = BarReconciler(spec=XAU_USD_SPEC)
    completed = _historical_bar(dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC))
    still_open = Bar(
        timeframe=Timeframe.M1,
        timestamp_utc=completed.timestamp_utc,
        complete=False,
        volume=completed.volume,
        bid_ohlc=completed.bid_ohlc,
        ask_ohlc=completed.ask_ohlc,
    )
    historical = [_historical_bar(dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC))]

    assert reconciler.reconcile([still_open], historical) == []


def test_reconciler_compares_mid_and_both_sides() -> None:
    reconciler = BarReconciler(spec=XAU_USD_SPEC)
    streamed = [
        _m1_bar(
            dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC),
            _ohlc("2038.00", "2038.50", "2037.75", "2038.25"),
            _ohlc("2038.10", "2038.52", "2037.77", "2038.27"),
        )
    ]
    historical = [_historical_bar(dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC))]

    discrepancies = reconciler.reconcile(streamed, historical)

    assert {d.detail["field"] for d in discrepancies} == {"ask_open", "mid_open"}
    ask_discrepancy = next(d for d in discrepancies if d.detail["field"] == "ask_open")
    assert ask_discrepancy.difference == Decimal("0.08")


def test_reconciler_handles_many_bars_and_orders_them() -> None:
    reconciler = BarReconciler(spec=XAU_USD_SPEC)
    stamps = [dt.datetime(2026, 1, 15, 14, m, tzinfo=UTC) for m in (30, 31, 32, 33)]
    streamed = [_historical_bar(s) for s in stamps]
    historical = [_historical_bar(s) for s in stamps]

    assert reconciler.reconcile(streamed, historical) == []
    assert reconciler.reconcile(list(reversed(historical)), streamed) == []


def test_reconciler_rejects_mismatched_timeframes() -> None:
    reconciler = BarReconciler(spec=XAU_USD_SPEC)
    m5 = Bar(
        timeframe=Timeframe.M5,
        timestamp_utc=dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC),
        complete=True,
        volume=0,
        bid_ohlc=_ohlc("2038.00", "2038.50", "2037.75", "2038.25"),
        ask_ohlc=_ohlc("2038.02", "2038.52", "2037.77", "2038.27"),
    )

    with pytest.raises(ValueError, match="configured for"):
        reconciler.reconcile([m5], [m5])
