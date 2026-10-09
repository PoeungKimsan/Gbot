"""Unit tests for :mod:`engine.feed.oanda_client`.

Everything here is driven by injected fakes: a scripted transport, a fake datetime clock,
and a no-op sleeper. No test opens a socket, waits on a real timer, or reads a real OANDA
stream.

The behaviours under test are exactly the ones the phase mandates: chunk-boundary-safe
parsing, heartbeat/staleness detection, deterministic exponential backoff with jitter,
reconnection, and backfill of the interval missed while the feed was down.
"""

import datetime as dt
import json
from decimal import Decimal

import pytest

from engine.feed.oanda_client import (
    BackfillRequest,
    BackoffPolicy,
    FeedState,
    HeartbeatMonitor,
    OandaCredentials,
    OandaPricingStream,
    OandaStreamError,
    StalenessDetector,
    StreamChunkDecoder,
    elapsed_seconds,
    jittered_backoff,
    parse_heartbeat_message,
    parse_price_message,
)
from engine.market.models import Quote

#: Placeholder account token used by the fixtures below. Never a real credential.
TEST_TOKEN = "test-account-token"  # noqa: S105 - placeholder, not a credential

UTC = dt.UTC

#: One PRICE line, split arbitrarily in the tests below.
PRICE_LINE = (
    b'{"type":"PRICE","time":"2026-01-15T14:30:00.000000000Z",'
    b'"bids":[{"price":"2038.25"}],"asks":[{"price":"2038.27"}]}\n'
)


class FakeTimeClock:
    """Datetime clock that only advances when a test says so."""

    def __init__(self, start: dt.datetime | None = None) -> None:
        self._now = start or dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC)

    def __call__(self) -> dt.datetime:
        return self._now

    def advance(self, duration: dt.timedelta) -> dt.datetime:
        self._now = self._now + duration
        return self._now


class FixedJitter:
    """Deterministic jitter source that records every request."""

    def __init__(self, value: Decimal = Decimal(0)) -> None:
        self.value = value
        self.requests = 0

    def __call__(self) -> Decimal:
        self.requests += 1
        return self.value


class FakeSleeper:
    def __init__(self) -> None:
        self.slept: list[Decimal] = []

    async def __call__(self, duration: dt.timedelta) -> None:
        self.slept.append(_exact_seconds(duration))


def _exact_seconds(duration: dt.timedelta) -> Decimal:
    whole = duration.days * 86_400 + duration.seconds
    return Decimal(whole) + Decimal(duration.microseconds) / Decimal(1_000_000)


class FakeTransport:
    """Scripted OANDA transport.

    ``scripts`` is a list of per-attempt chunk lists. ``advance_before`` is a duration the
    fake clock jumps by when the connection opens, which is how a test simulates a
    connection that goes silent before yielding anything.

    ``__call__`` is an ``async def`` so it satisfies the async-factory transport contract
    the consumer awaits.
    """

    def __init__(
        self,
        scripts: list[list[bytes]],
        *,
        clock: FakeTimeClock | None = None,
        advance_before: dt.timedelta | None = None,
    ) -> None:
        self._scripts = scripts
        self._clock = clock
        self._advance_before = advance_before
        self.calls: list[int] = []

    async def __call__(self):
        session = self._scripts[len(self.calls)] if self.calls else self._scripts[0]
        self.calls.append(len(self.calls))
        if self._clock is not None and self._advance_before is not None:
            self._clock.advance(self._advance_before)
        return _FakeResponse(session)


class _FakeResponse:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk


def _make_stream(
    transport: FakeTransport,
    *,
    sleeper: FakeSleeper | None = None,
    jitter: FixedJitter | None = None,
    policy: BackoffPolicy | None = None,
    clock: FakeTimeClock | None = None,
) -> OandaPricingStream:
    return OandaPricingStream(
        transport=transport,
        credentials=OandaCredentials(
            account_id="acct", token=TEST_TOKEN, environment="practice"
        ),
        policy=policy
        or BackoffPolicy(
            base_delay=dt.timedelta(milliseconds=100),
            max_delay=dt.timedelta(seconds=5),
            multiplier=Decimal(2),
        ),
        sleeper=sleeper or FakeSleeper(),
        jitter=jitter or FixedJitter(),
        clock=clock or FakeTimeClock(),
    )


# --------------------------------------------------------------------------- #
# chunk decoding
# --------------------------------------------------------------------------- #
def test_decoder_handles_exact_line_boundaries() -> None:
    decoder = StreamChunkDecoder()

    assert decoder.feed(PRICE_LINE) == [PRICE_LINE.decode().strip()]


def test_decoder_survives_a_chunk_splitting_a_json_line() -> None:
    decoder = StreamChunkDecoder()
    midpoint = len(PRICE_LINE) // 2

    assert decoder.feed(PRICE_LINE[:midpoint]) == []
    messages = decoder.feed(PRICE_LINE[midpoint:])

    assert len(messages) == 1
    assert json.loads(messages[0])["type"] == "PRICE"


def test_decoder_survives_three_way_split() -> None:
    decoder = StreamChunkDecoder()
    third = len(PRICE_LINE) // 3

    decoder.feed(PRICE_LINE[:third])
    decoder.feed(PRICE_LINE[third : 2 * third])
    messages = decoder.feed(PRICE_LINE[2 * third :])

    assert len(messages) == 1
    assert json.loads(messages[0])["asks"][0]["price"] == "2038.27"


def test_decoder_buffers_a_utf8_sequence_split_across_chunks() -> None:
    """A codepoint split mid-sequence must be buffered, not mangled."""
    decoder = StreamChunkDecoder()
    raw = '{"type":"HEARTBEAT","time":"2026-01-15T14:30:00Z","note":"\u00e9"}\n'.encode()
    split = raw.index(b"\n") - 1

    assert decoder.feed(raw[:split]) == []
    messages = decoder.feed(raw[split:])

    assert json.loads(messages[0])["note"] == "\u00e9"


def test_decoder_handles_carriage_return_line_endings() -> None:
    decoder = StreamChunkDecoder()

    assert decoder.feed(b'{"type":"HEARTBEAT","time":"2026-01-15T14:30:00Z"}\r\n') == [
        '{"type":"HEARTBEAT","time":"2026-01-15T14:30:00Z"}'
    ]


def test_decoder_ignores_blank_lines() -> None:
    decoder = StreamChunkDecoder()

    assert decoder.feed(b"\n\n\n") == []


def test_decoder_flushes_a_trailing_line_without_newline() -> None:
    decoder = StreamChunkDecoder()
    line = '{"type":"HEARTBEAT","time":"2026-01-15T14:30:00Z"}'
    decoder.feed(line.encode())

    assert decoder.flush() == [line]


def test_decoder_flush_on_empty_buffer() -> None:
    decoder = StreamChunkDecoder()
    decoder.feed(b"")

    assert decoder.flush() == []


def test_decoder_rejects_text_chunks() -> None:
    decoder = StreamChunkDecoder()

    with pytest.raises(OandaStreamError, match="bytes"):
        decoder.feed('{"type":"HEARTBEAT"}')  # type: ignore[arg-type]


def test_decoder_accumulates_multiple_lines_in_one_chunk() -> None:
    decoder = StreamChunkDecoder()
    body = (
        b'{"type":"HEARTBEAT","time":"2026-01-15T14:30:00Z"}\n'
        b'{"type":"HEARTBEAT","time":"2026-01-15T14:30:05Z"}\n'
    )

    assert len(decoder.feed(body)) == 2


# --------------------------------------------------------------------------- #
# message parsing
# --------------------------------------------------------------------------- #
def test_parse_price_message() -> None:
    payload = json.loads(PRICE_LINE)

    quote = parse_price_message(payload)

    assert quote.bid == Decimal("2038.25")
    assert quote.ask == Decimal("2038.27")
    assert quote.timestamp_utc == dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC)


def test_parse_price_message_converts_to_utc() -> None:
    payload = json.loads(PRICE_LINE)
    payload["time"] = "2026-01-15T09:30:00.000000000-05:00"

    assert parse_price_message(payload).timestamp_utc == dt.datetime(
        2026, 1, 15, 14, 30, tzinfo=UTC
    )


def test_parse_price_message_truncates_nanoseconds() -> None:
    payload = json.loads(PRICE_LINE)
    payload["time"] = "2026-01-15T14:30:00.123456789Z"

    quote = parse_price_message(payload)

    assert quote.timestamp_utc.microsecond == 123456


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p.update(type="HEARTBEAT"),
        lambda p: p.pop("bids"),
        lambda p: p.update(bids=[]),
        lambda p: p.update(asks=[{}]),
        lambda p: p.pop("time"),
        lambda p: p.update(time="not-a-timestamp"),
        lambda p: p.update(bids=[{"price": "abc"}]),
        lambda p: p.update(bids=[{"price": "2038.30"}]),
    ],
)
def test_parse_price_message_rejects_broken_payloads(mutation) -> None:
    payload = json.loads(PRICE_LINE)
    mutation(payload)

    with pytest.raises(OandaStreamError):
        parse_price_message(payload)


def test_parse_price_message_uses_top_of_book() -> None:
    payload = json.loads(PRICE_LINE)
    payload["bids"].append({"price": "2038.00"})
    payload["asks"].append({"price": "2038.99"})

    quote = parse_price_message(payload)

    assert quote.bid == Decimal("2038.25")
    assert quote.ask == Decimal("2038.27")


def test_parse_heartbeat_message() -> None:
    assert parse_heartbeat_message(
        json.loads('{"type":"HEARTBEAT","time":"2026-01-15T14:30:00Z"}')
    ) == dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC)


def test_parse_heartbeat_rejects_price() -> None:
    with pytest.raises(OandaStreamError, match="HEARTBEAT"):
        parse_heartbeat_message(json.loads(PRICE_LINE))


# --------------------------------------------------------------------------- #
# elapsed seconds
# --------------------------------------------------------------------------- #
def test_elapsed_seconds_is_a_decimal() -> None:
    first = dt.datetime(2026, 1, 15, 14, 30, 0, tzinfo=UTC)
    second = first.replace(second=3)

    result = elapsed_seconds(second, first)

    assert isinstance(result, Decimal)
    assert result == Decimal(3)


def test_elapsed_seconds_handles_subsecond_precision() -> None:
    first = dt.datetime(2026, 1, 15, 14, 30, 0, 500000, tzinfo=UTC)
    second = dt.datetime(2026, 1, 15, 14, 30, 0, 750000, tzinfo=UTC)

    assert elapsed_seconds(second, first) == Decimal("0.25")


# --------------------------------------------------------------------------- #
# HeartbeatMonitor
# --------------------------------------------------------------------------- #
def test_heartbeat_monitor_starts_quiet() -> None:
    monitor = HeartbeatMonitor()

    assert monitor.last_quote_at is None
    assert monitor.last_heartbeat_at is None


def test_heartbeat_monitor_tracks_quotes() -> None:
    monitor = HeartbeatMonitor()
    stamp = dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC)

    monitor.record_quote(stamp)

    assert monitor.last_quote_at == stamp


def test_heartbeats_alone_keep_the_feed_alive() -> None:
    """Heartbeat-only is the normal idle market, not a stale feed."""
    monitor = HeartbeatMonitor()
    start = dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC)

    monitor.record_heartbeat(start)
    monitor.record_heartbeat(start.replace(second=9))

    assert monitor.is_stale(respect_at=start.replace(second=9)) is False


def test_heartbeat_monitor_staleness_uses_the_newest_signal() -> None:
    monitor = HeartbeatMonitor()
    quote_at = dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC)
    heartbeat_at = quote_at.replace(second=8)

    monitor.record_quote(quote_at)
    monitor.record_heartbeat(heartbeat_at)

    assert monitor.is_stale(respect_at=heartbeat_at.replace(second=17)) is False
    assert monitor.is_stale(respect_at=heartbeat_at.replace(second=18)) is False
    assert monitor.is_stale(respect_at=heartbeat_at.replace(second=19)) is True


def test_heartbeat_monitor_never_stale_with_no_activity() -> None:
    monitor = HeartbeatMonitor()

    assert monitor.is_stale(respect_at=dt.datetime(2026, 1, 15, 15, 0, tzinfo=UTC)) is False


def test_heartbeat_monitor_reset_clears_state() -> None:
    monitor = HeartbeatMonitor()
    monitor.record_quote(dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC))

    monitor.reset()

    assert monitor.last_quote_at is None


def test_heartbeat_monitor_retention_forgets_old_activity() -> None:
    """Very old activity must not keep a feed looking alive forever."""
    monitor = HeartbeatMonitor(retention=dt.timedelta(seconds=5))
    old = dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC)

    monitor.record_quote(old)

    assert monitor.last_activity(respect_at=old) == old
    assert monitor.last_activity(respect_at=old + dt.timedelta(seconds=6)) is None


def test_heartbeat_monitor_rejects_non_positive_window() -> None:
    with pytest.raises(OandaStreamError, match="quiet_after"):
        HeartbeatMonitor(quiet_after=dt.timedelta(0))


def test_heartbeat_monitor_ignores_out_of_order_timestamps() -> None:
    monitor = HeartbeatMonitor()
    later = dt.datetime(2026, 1, 15, 14, 30, 5, tzinfo=UTC)

    monitor.record_quote(later)
    monitor.record_quote(later.replace(second=1))

    assert monitor.last_quote_at == later


# --------------------------------------------------------------------------- #
# StalenessDetector
# --------------------------------------------------------------------------- #
def test_detector_requires_both_signals_to_be_quiet() -> None:
    detector = StalenessDetector()

    assert detector.is_stale(quote_age=Decimal(10), heartbeat_age=Decimal(10)) is False
    assert detector.is_stale(quote_age=Decimal("10.1"), heartbeat_age=Decimal("10.1")) is True


def test_detector_quotes_only_silence_is_not_stale() -> None:
    detector = StalenessDetector()

    assert detector.is_stale(quote_age=Decimal("10.1"), heartbeat_age=Decimal(1)) is False


def test_detector_heartbeats_only_silence_is_not_stale() -> None:
    detector = StalenessDetector()

    assert detector.is_stale(quote_age=Decimal(1), heartbeat_age=Decimal("10.1")) is False


def test_detector_treats_missing_heartbeat_as_quiet() -> None:
    """Before the first heartbeat, the quote side alone decides."""
    detector = StalenessDetector()

    assert detector.is_stale(quote_age=Decimal(5), heartbeat_age=None) is False
    assert detector.is_stale(quote_age=Decimal("10.1"), heartbeat_age=None) is True


@pytest.mark.parametrize("bad", [dt.timedelta(0), dt.timedelta(seconds=-1)])
def test_detector_rejects_non_positive_windows(bad) -> None:
    with pytest.raises(OandaStreamError, match="timedelta"):
        StalenessDetector(quote_quiet=bad)


# --------------------------------------------------------------------------- #
# backoff
# --------------------------------------------------------------------------- #
def test_jittered_backoff_is_exponential_and_capped() -> None:
    policy = BackoffPolicy(
        base_delay=dt.timedelta(seconds=1),
        max_delay=dt.timedelta(seconds=60),
        multiplier=Decimal(2),
    )

    assert jittered_backoff(policy, attempt=0, jitter=Decimal(0)) == Decimal(0)
    assert jittered_backoff(policy, attempt=1, jitter=Decimal(1)) == Decimal(2)
    assert jittered_backoff(policy, attempt=2, jitter=Decimal(1)) == Decimal(4)
    assert jittered_backoff(policy, attempt=50, jitter=Decimal(1)) == Decimal(60)


def test_jittered_backoff_scales_by_jitter() -> None:
    policy = BackoffPolicy(
        base_delay=dt.timedelta(seconds=4),
        max_delay=dt.timedelta(seconds=60),
        multiplier=Decimal(2),
    )

    assert jittered_backoff(policy, attempt=0, jitter=Decimal("0.5")) == Decimal(2)
    assert jittered_backoff(policy, attempt=0, jitter=Decimal(0)) == Decimal(0)


@pytest.mark.parametrize("bad", [Decimal("-0.1"), Decimal("1.1"), Decimal("NaN"), 0.5])
def test_jittered_backoff_rejects_bad_jitter(bad) -> None:
    policy = BackoffPolicy(
        base_delay=dt.timedelta(seconds=1),
        max_delay=dt.timedelta(seconds=10),
        multiplier=Decimal(2),
    )

    with pytest.raises(OandaStreamError):
        jittered_backoff(policy, attempt=0, jitter=bad)


@pytest.mark.parametrize("bad", [-1, True, 1.5])
def test_jittered_backoff_rejects_bad_attempt(bad) -> None:
    policy = BackoffPolicy(
        base_delay=dt.timedelta(seconds=1),
        max_delay=dt.timedelta(seconds=10),
        multiplier=Decimal(2),
    )

    with pytest.raises(OandaStreamError):
        jittered_backoff(policy, attempt=bad, jitter=Decimal(0))


def test_jittered_backoff_returns_decimal_seconds() -> None:
    policy = BackoffPolicy(
        base_delay=dt.timedelta(milliseconds=500),
        max_delay=dt.timedelta(seconds=10),
        multiplier=Decimal(2),
    )

    result = jittered_backoff(policy, attempt=0, jitter=Decimal(1))

    assert isinstance(result, Decimal)
    assert result == Decimal("0.5")


def test_backoff_policy_rejects_bad_multiplier() -> None:
    with pytest.raises(OandaStreamError, match="multiplier"):
        BackoffPolicy(
            base_delay=dt.timedelta(seconds=1),
            max_delay=dt.timedelta(seconds=2),
            multiplier=Decimal("0.5"),
        )


def test_backoff_policy_rejects_bad_delays() -> None:
    with pytest.raises(OandaStreamError, match="max_delay"):
        BackoffPolicy(
            base_delay=dt.timedelta(seconds=5),
            max_delay=dt.timedelta(seconds=1),
            multiplier=Decimal(2),
        )

    with pytest.raises(OandaStreamError, match="base_delay"):
        BackoffPolicy(
            base_delay=dt.timedelta(seconds=0),
            max_delay=dt.timedelta(seconds=5),
            multiplier=Decimal(2),
        )


# --------------------------------------------------------------------------- #
# BackfillRequest
# --------------------------------------------------------------------------- #
def test_backfill_request_covers_the_missed_interval() -> None:
    request = BackfillRequest.for_gap(
        last_seen=dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC),
        resumed=dt.datetime(2026, 1, 15, 14, 40, tzinfo=UTC),
    )

    assert request.start == dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC)
    assert request.end == dt.datetime(2026, 1, 15, 14, 40, tzinfo=UTC)
    assert request.duration == dt.timedelta(minutes=10)


def test_backfill_request_rejects_invalid_interval() -> None:
    with pytest.raises(OandaStreamError, match="before"):
        BackfillRequest(
            start=dt.datetime(2026, 1, 15, 14, 40, tzinfo=UTC),
            end=dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC),
        )


def test_backfill_request_normalizes_non_utc_input() -> None:
    from zoneinfo import ZoneInfo

    request = BackfillRequest.for_gap(
        last_seen=dt.datetime(2026, 1, 15, 9, 30, tzinfo=ZoneInfo("America/New_York")),
        resumed=dt.datetime(2026, 1, 15, 10, 0, tzinfo=ZoneInfo("America/New_York")),
    )

    assert request.start == dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# the stream consumer
# --------------------------------------------------------------------------- #
def test_stream_starts_idle() -> None:
    stream = _make_stream(FakeTransport([[b""]]))

    assert stream.state is FeedState.IDLE


async def test_stream_processes_prices() -> None:
    transport = FakeTransport([[PRICE_LINE, PRICE_LINE.replace(b"2038.27", b"2038.31")]])
    stream = _make_stream(transport)

    quotes: list[Quote] = []
    await stream.run(quotes.append, max_attempts=1)

    assert [q.bid for q in quotes] == [Decimal("2038.25"), Decimal("2038.25")]
    assert [q.ask for q in quotes] == [Decimal("2038.27"), Decimal("2038.31")]
    assert stream.state is FeedState.STREAMING


async def test_stream_reassembles_a_message_split_across_chunks() -> None:
    raw = PRICE_LINE
    transport = FakeTransport([[raw[:20], raw[20:45], raw[45:]]])
    stream = _make_stream(transport)

    quotes: list[Quote] = []
    await stream.run(quotes.append, max_attempts=1)

    assert len(quotes) == 1
    assert quotes[0].ask == Decimal("2038.27")


async def test_stream_marks_stale_after_silence_and_reconnects() -> None:
    clock = FakeTimeClock()
    transport = FakeTransport(
        [[b""], [b""]],
        clock=clock,
        advance_before=dt.timedelta(seconds=30),
    )
    sleeper = FakeSleeper()
    stream = _make_stream(transport, sleeper=sleeper, clock=clock)

    transitions: list[FeedState] = []
    stream.on_state_change = transitions.append

    await stream.run(lambda _q: None, max_attempts=2)

    assert FeedState.STALE_FEED in transitions
    assert sleeper.slept, "a stale feed must back off before reconnecting"


async def test_stream_stays_healthy_within_the_staleness_window() -> None:
    """Six seconds of silence, with a heartbeat, must not trip the 10s window."""
    clock = FakeTimeClock()
    heartbeat = b'{"type":"HEARTBEAT","time":"2026-01-15T14:30:00.000000000Z"}\n'
    transport = FakeTransport(
        [[heartbeat], [heartbeat]],
        clock=clock,
        advance_before=dt.timedelta(seconds=6),
    )
    stream = _make_stream(transport, clock=clock)

    transitions: list[FeedState] = []
    stream.on_state_change = transitions.append

    await stream.run(lambda _q: None, max_attempts=2)

    assert FeedState.STALE_FEED not in transitions
    assert stream.state is FeedState.STREAMING


async def test_stream_reconnects_with_exponential_backoff() -> None:
    transport = FakeTransport([[b""]] * 4)
    sleeper = FakeSleeper()
    stream = _make_stream(transport, sleeper=sleeper, jitter=FixedJitter(Decimal(1)))

    await stream.run(lambda _q: None, max_attempts=4)

    assert sleeper.slept == [Decimal("0.1"), Decimal("0.2"), Decimal("0.4")]


async def test_stream_stops_after_max_attempts() -> None:
    transport = FakeTransport([[b""]] * 10)
    stream = _make_stream(transport)

    await stream.run(lambda _q: None, max_attempts=3)

    assert stream.state is FeedState.FAILED
    assert len(transport.calls) == 3


async def test_stream_requests_backfill_for_a_large_gap() -> None:
    second = PRICE_LINE.replace(b"14:30:00", b"14:40:00")
    transport = FakeTransport([[PRICE_LINE], [second]])
    stream = _make_stream(transport)

    await stream.run(lambda _q: None, max_attempts=2)

    assert len(stream.backfill_requests) == 1
    request = stream.backfill_requests[0]
    assert request.start == dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC)
    assert request.end == dt.datetime(2026, 1, 15, 14, 40, tzinfo=UTC)
    assert request.duration == dt.timedelta(minutes=10)


async def test_stream_ignores_a_negligible_gap() -> None:
    second = PRICE_LINE.replace(b"14:30:00", b"14:30:02")
    transport = FakeTransport([[PRICE_LINE], [second]])
    stream = _make_stream(transport)

    await stream.run(lambda _q: None, max_attempts=2)

    assert stream.backfill_requests == []


async def test_stream_heartbeats_do_not_trigger_backfill() -> None:
    heartbeats = b'{"type":"HEARTBEAT","time":"2026-01-15T14:30:00.000000000Z"}\n'
    later = b'{"type":"HEARTBEAT","time":"2026-01-15T14:35:00.000000000Z"}\n'
    transport = FakeTransport([[heartbeats], [later]])
    stream = _make_stream(transport)

    await stream.run(lambda _q: None, max_attempts=2)

    assert stream.backfill_requests == []


async def test_stream_state_passes_through_stale_before_reconnecting() -> None:
    clock = FakeTimeClock()
    transport = FakeTransport(
        [[b""], [b""], [b""]],
        clock=clock,
        advance_before=dt.timedelta(seconds=30),
    )
    stream = _make_stream(transport, clock=clock)

    transitions: list[FeedState] = []
    stream.on_state_change = transitions.append

    await stream.run(lambda _q: None, max_attempts=2)

    assert transitions[0] is FeedState.CONNECTING
    assert transitions.index(FeedState.STALE_FEED) < transitions.index(FeedState.RECONNECTING)


async def test_stream_backoff_resets_after_a_healthy_session() -> None:
    """A healthy session must earn back the full backoff range.

    First a failure backs off by the base delay, then a healthy session resets the step,
    so the next failure waits the base delay again instead of the doubled one.
    """
    transport = FakeTransport([[b""], [PRICE_LINE], [b""], [b""]])
    sleeper = FakeSleeper()
    stream = _make_stream(transport, sleeper=sleeper, jitter=FixedJitter(Decimal(1)))

    await stream.run(lambda _q: None, max_attempts=4)

    assert sleeper.slept == [Decimal("0.1"), Decimal("0.1")]


async def test_stream_propagates_transport_errors() -> None:
    class BoomTransport(FakeTransport):
        async def __call__(self):
            raise RuntimeError("connection reset")

    stream = _make_stream(BoomTransport([[b""]]))

    with pytest.raises(RuntimeError, match="connection reset"):
        await stream.run(lambda _q: None, max_attempts=1)


async def test_stream_can_be_stopped_between_reconnects() -> None:
    transport = FakeTransport([[b""]] * 5)
    sleeper = FakeSleeper()
    stream = _make_stream(transport, sleeper=sleeper, jitter=FixedJitter(Decimal(1)))

    def should_stop() -> bool:
        return len(sleeper.slept) >= 2

    await stream.run(lambda _q: None, max_attempts=10, should_stop=should_stop)

    assert stream.state is FeedState.STOPPED
    assert len(transport.calls) == 2
    assert sleeper.slept == [Decimal("0.1"), Decimal("0.2")]


async def test_stream_skips_unparseable_lines_without_aborting() -> None:
    transport = FakeTransport([[b'{"type":"PRICE","time"\n' + PRICE_LINE]])
    stream = _make_stream(transport)

    quotes: list[Quote] = []
    await stream.run(quotes.append, max_attempts=1)

    assert len(quotes) == 1


def test_credentials_reject_missing_token() -> None:
    with pytest.raises(OandaStreamError, match="token"):
        OandaCredentials(account_id="acct", token="", environment="practice")


def test_credentials_reject_unknown_environment() -> None:
    with pytest.raises(OandaStreamError, match="environment"):
        OandaCredentials(account_id="acct", token=TEST_TOKEN, environment="demo")


def test_credentials_build_oanda_endpoints() -> None:
    practice = OandaCredentials(account_id="101", token=TEST_TOKEN, environment="practice")
    live = OandaCredentials(account_id="101", token=TEST_TOKEN, environment="live")

    assert practice.stream_url.endswith("/v3/accounts/101/pricing/stream")
    assert "fxpractice" in practice.stream_url
    assert "fxtrade" in live.stream_url
    assert live.rest_url.startswith("https://api-fxtrade.oanda.com")
