"""Async chunked consumer for the OANDA v20 pricing stream.

The stream is a long-lived HTTP response carrying newline-delimited JSON. That shape is
fragile in exactly the ways that matter for a trading feed: a JSON object can be split
across TCP chunks, a chunk can land mid-UTF-8 codepoint, and the connection can go silent
without closing.

Behaviour contract:

* **Chunk-boundary safety.** Bytes are incrementally decoded and buffered; only complete
  newline-terminated lines reach the parser, and a partial line is never parsed.
* **Staleness.** OANDA sends a heartbeat every 5s, so a quiet *market* is not a dead
  connection. The feed is stale only when quotes **and** heartbeats have both been quiet
  for more than the window (10s by default, strictly greater).
* **Reconnect.** Exponential backoff with jitter, capped at ``max_delay``. The backoff step
  resets after a healthy session, so a long-lived connection does not ramp its delay forever.
* **Backfill.** On seeing a quote far ahead of the previous one, the skipped interval is
  reported as a :class:`BackfillRequest` so the caller can recover it from REST.

Every side effect is injected -- transport, clock, sleeper, jitter source -- so the whole
lifecycle is deterministic under test.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol

from engine.market.models import Quote

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

logger = logging.getLogger(__name__)

__all__ = [
    "HEARTBEAT_INTERVAL",
    "STALE_AFTER",
    "BackfillRequest",
    "BackoffPolicy",
    "FeedState",
    "HeartbeatMonitor",
    "OandaCredentials",
    "OandaPricingStream",
    "OandaStreamError",
    "OandaStreamTransport",
    "StalenessDetector",
    "StreamChunkDecoder",
    "elapsed_seconds",
    "jittered_backoff",
    "parse_heartbeat_message",
    "parse_price_message",
]

#: Seconds between OANDA heartbeats when no ticks are flowing.
HEARTBEAT_INTERVAL = dt.timedelta(seconds=5)

#: Quiet period after which a feed is declared stale, per the phase contract.
STALE_AFTER = dt.timedelta(seconds=10)

#: A quote gap longer than this is assumed to have missed real data.
RECONNECT_GAP = dt.timedelta(seconds=5)

_MICROSECONDS_PER_SECOND = Decimal(1_000_000)


class OandaStreamError(RuntimeError):
    """A malformed message, an unusable configuration, or an invalid backoff input."""


class FeedState(StrEnum):
    """Lifecycle of a single pricing-stream session.

    ``STALE_FEED`` is emitted as it happens and is observable through ``on_state_change``
    and the logs. It is deliberately *not* a terminal state: when the attempt budget runs
    out the stream reports ``FAILED`` so a supervisor never mistakes a stale feed for a
    clean exit.
    """

    IDLE = "IDLE"
    CONNECTING = "CONNECTING"
    STREAMING = "STREAMING"
    STALE_FEED = "STALE_FEED"
    RECONNECTING = "RECONNECTING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


# --------------------------------------------------------------------------- #
# exact duration arithmetic
# --------------------------------------------------------------------------- #
def _seconds(duration: dt.timedelta) -> Decimal:
    """Exact seconds for a timedelta, as a Decimal.

    ``timedelta.total_seconds()`` returns a float, which would inject binary rounding into
    the backoff arithmetic. This is the only sanctioned conversion.
    """
    whole = dt.timedelta(days=duration.days, seconds=duration.seconds)
    return Decimal(whole.days * 86_400 + whole.seconds) + Decimal(duration.microseconds) / (
        _MICROSECONDS_PER_SECOND
    )


def _as_timedelta(seconds: Decimal) -> dt.timedelta:
    """Convert Decimal seconds into an exact timedelta via integer microseconds."""
    return dt.timedelta(microseconds=int(seconds * _MICROSECONDS_PER_SECOND))


def elapsed_seconds(later: dt.datetime, earlier: dt.datetime) -> Decimal:
    """Seconds between two datetimes as an exact Decimal."""
    if not isinstance(later, dt.datetime) or not isinstance(earlier, dt.datetime):
        raise OandaStreamError("elapsed_seconds requires datetimes")
    return _seconds(later - earlier)


# --------------------------------------------------------------------------- #
# chunk decoding
# --------------------------------------------------------------------------- #
class StreamChunkDecoder:
    """Turns an arbitrary byte-chunk iterator into complete text lines.

    The decoder holds partial state on purpose: raw bytes accumulate until a newline
    terminates them. ``codecs.getincrementaldecoder`` handles a UTF-8 sequence that straddles
    a chunk boundary, which would otherwise surface as ``UnicodeDecodeError`` deep inside
    the stream reader.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._partial_bytes = bytearray()

    def feed(self, chunk: bytes | bytearray) -> list[str]:
        """Consume one raw chunk, returning every line it completed."""
        if not isinstance(chunk, (bytes, bytearray)):
            raise OandaStreamError(f"chunk must be bytes, got {type(chunk).__name__}")

        from codecs import getincrementaldecoder

        inbound = bytes(self._partial_bytes) + bytes(chunk)
        try:
            text = getincrementaldecoder("utf-8")().decode(inbound, final=False)
        except UnicodeDecodeError as exc:
            raise OandaStreamError(f"chunk is not valid UTF-8: {exc}") from exc

        self._partial_bytes = bytearray()
        self._buffer += text
        return self._drain_lines()

    def flush(self) -> list[str]:
        """Emit any buffered content when the stream ends without a final newline."""
        tail, self._buffer = self._buffer, ""
        self._partial_bytes = bytearray()
        return [tail.rstrip("\r")] if tail.strip() else []

    def _drain_lines(self) -> list[str]:
        lines: list[str] = []
        while "\n" in self._buffer:
            line, _, self._buffer = self._buffer.partition("\n")
            stripped = line.rstrip("\r")
            if stripped:
                lines.append(stripped)
        return lines


# --------------------------------------------------------------------------- #
# message parsing
# --------------------------------------------------------------------------- #
def _parse_oanda_time(value: Any) -> dt.datetime:  # noqa: ANN401 - OANDA JSON
    """Parse OANDA's RFC3339 timestamp with nanosecond precision into UTC."""
    if not isinstance(value, str):
        raise OandaStreamError(f"OANDA timestamp must be a string, got {type(value).__name__}")

    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # Python's ISO parser accepts at most six fractional digits; OANDA sends nine.
    head, _, tail = text.partition(".")
    if tail:
        fraction, sign, zone = _split_fraction(tail)
        text = head + "." + fraction[:6].ljust(6, "0") + (sign + zone if zone else "")

    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError as exc:
        raise OandaStreamError(f"unparseable OANDA timestamp {value!r}") from exc
    if parsed.tzinfo is None:
        raise OandaStreamError(f"OANDA timestamp has no offset: {value!r}")
    return parsed.astimezone(dt.UTC)


def _split_fraction(tail: str) -> tuple[str, str, str]:
    """Split ``fraction+zone`` into ``(fraction, timezone, zone)``."""
    for marker in ("+", "-"):
        if marker in tail:
            fraction, _, zone = tail.partition(marker)
            return fraction, marker, zone
    return tail, "", ""


def _first_level(levels: Any, side: str) -> dict[str, Any]:  # noqa: ANN401
    if not isinstance(levels, list) or not levels:
        raise OandaStreamError(f"PRICE message has no {side} levels")
    first = levels[0]
    if not isinstance(first, dict):
        raise OandaStreamError(f"PRICE message {side} level is not an object")
    if "price" not in first:
        raise OandaStreamError(f"PRICE message {side} level has no price")
    return first


def parse_price_message(payload: dict[str, Any]) -> Quote:
    """Convert one OANDA ``PRICE`` payload into a :class:`~engine.market.models.Quote`.

    Prices arrive as JSON strings and become Decimals directly, so a float never touches a
    price. The top of book (first bid and first ask) is used.
    """
    if not isinstance(payload, dict):
        raise OandaStreamError(f"PRICE payload must be an object, got {type(payload).__name__}")
    if payload.get("type") != "PRICE":
        raise OandaStreamError(f"payload is not a PRICE message: {payload.get('type')!r}")

    stamp = _parse_oanda_time(payload.get("time"))
    bid_text = str(_first_level(payload.get("bids"), "bids")["price"]).strip()
    ask_text = str(_first_level(payload.get("asks"), "asks")["price"]).strip()

    try:
        bid = Decimal(bid_text)
        ask = Decimal(ask_text)
    except Exception as exc:
        raise OandaStreamError(f"unparseable prices bid={bid_text!r} ask={ask_text!r}") from exc

    try:
        return Quote(bid=bid, ask=ask, timestamp_utc=stamp)
    except ValueError as exc:
        raise OandaStreamError(f"invalid quote: {exc}") from exc


def parse_heartbeat_message(payload: dict[str, Any]) -> dt.datetime:
    """Convert one OANDA ``HEARTBEAT`` payload into its UTC timestamp."""
    if not isinstance(payload, dict):
        raise OandaStreamError(
            f"HEARTBEAT payload must be an object, got {type(payload).__name__}"
        )
    if payload.get("type") != "HEARTBEAT":
        raise OandaStreamError(f"payload is not a HEARTBEAT: {payload.get('type')!r}")
    return _parse_oanda_time(payload.get("time"))


def decode_stream_line(line: str) -> dict[str, Any] | None:
    """Decode one line, returning ``None`` for anything that is not a JSON object.

    Unparseable lines are logged and skipped rather than raised: a partial message on a
    reconnecting stream must not abort the session.
    """
    text = line.strip()
    if not text or text.startswith(":"):
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("ignoring unparseable stream line (%.60s)", text)
        return None
    return payload if isinstance(payload, dict) else None


# --------------------------------------------------------------------------- #
# staleness
# --------------------------------------------------------------------------- #
def _newer(first: dt.datetime | None, second: dt.datetime | None) -> dt.datetime | None:
    if first is None:
        return second
    if second is None:
        return first
    return first if first >= second else second


class HeartbeatMonitor:
    """Tracks the most recent quote and heartbeat and answers "has this gone quiet?".

    The most recent of the two signals governs. A heartbeat-only feed counts as alive:
    OANDA sends heartbeats every 5s precisely so a quiet market is not mistaken for a dead
    connection.
    """

    def __init__(
        self,
        *,
        quiet_after: dt.timedelta = STALE_AFTER,
        retention: dt.timedelta | None = None,
    ) -> None:
        if quiet_after <= dt.timedelta(0):
            raise OandaStreamError("quiet_after must be positive")
        self._quiet_after = quiet_after
        self._retention = retention
        self._last_quote_at: dt.datetime | None = None
        self._last_heartbeat_at: dt.datetime | None = None

    @property
    def last_quote_at(self) -> dt.datetime | None:
        return self._last_quote_at

    @property
    def last_heartbeat_at(self) -> dt.datetime | None:
        return self._last_heartbeat_at

    @property
    def quiet_after(self) -> dt.timedelta:
        return self._quiet_after

    def last_activity(self, respect_at: dt.datetime | None = None) -> dt.datetime | None:
        """The most recent signal, or ``None`` once everything has aged past retention."""
        newest = _newer(self._last_quote_at, self._last_heartbeat_at)
        if newest is None or respect_at is None or self._retention is None:
            return newest
        if respect_at - newest > self._retention:
            return None
        return newest

    def record_quote(self, stamp: dt.datetime) -> None:
        self._last_quote_at = _newer(self._last_quote_at, stamp)

    def record_heartbeat(self, stamp: dt.datetime) -> None:
        self._last_heartbeat_at = _newer(self._last_heartbeat_at, stamp)

    def is_stale(self, respect_at: dt.datetime) -> bool:
        """True once the most recent signal is strictly older than ``quiet_after``."""
        newest = self.last_activity(respect_at)
        if newest is None:
            return False
        return respect_at - newest > self._quiet_after

    def reset(self) -> None:
        self._last_quote_at = None
        self._last_heartbeat_at = None


class StalenessDetector:
    """Decides whether silence has lasted long enough to declare a stale feed.

    Both signals must be quiet: quotes-only silence is a normal idle market, whereas
    quotes *and* heartbeats silent together means the connection is gone. Before the first
    heartbeat arrives, heartbeats are treated as quiet (there is nothing to contradict
    silence) and the quote side alone decides.
    """

    def __init__(
        self,
        *,
        quote_quiet: dt.timedelta = STALE_AFTER,
        heartbeat_quiet: dt.timedelta = STALE_AFTER,
    ) -> None:
        for name, value in (("quote_quiet", quote_quiet), ("heartbeat_quiet", heartbeat_quiet)):
            if not isinstance(value, dt.timedelta) or value <= dt.timedelta(0):
                raise OandaStreamError(f"{name} must be a positive timedelta, got {value!r}")
        self._quote_quiet = _seconds(quote_quiet)
        self._heartbeat_quiet = _seconds(heartbeat_quiet)

    def is_stale(
        self,
        *,
        quote_age: Decimal | None,
        heartbeat_age: Decimal | None,
        now: dt.datetime | None = None,
    ) -> bool:
        quotes_quiet = quote_age is not None and quote_age > self._quote_quiet
        heartbeats_quiet = heartbeat_age is None or heartbeat_age > self._heartbeat_quiet
        return quotes_quiet and heartbeats_quiet


# --------------------------------------------------------------------------- #
# backoff
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class BackoffPolicy:
    """Exponential backoff bounds."""

    base_delay: dt.timedelta
    max_delay: dt.timedelta
    multiplier: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.base_delay, dt.timedelta) or self.base_delay <= dt.timedelta(0):
            raise OandaStreamError("base_delay must be a positive timedelta")
        if not isinstance(self.max_delay, dt.timedelta) or self.max_delay < self.base_delay:
            raise OandaStreamError("max_delay must be at least base_delay")
        if isinstance(self.multiplier, float) or not isinstance(self.multiplier, Decimal):
            raise OandaStreamError(f"multiplier must be a Decimal, got {self.multiplier!r}")
        if self.multiplier < Decimal(1):
            raise OandaStreamError(f"multiplier must be at least 1, got {self.multiplier}")


def jittered_backoff(
    policy: BackoffPolicy, attempt: int, jitter: Decimal
) -> Decimal:
    """Delay in seconds for ``attempt`` (zero-based), scaled by ``jitter``.

    ``attempt`` must be a non-negative int. The result is always a Decimal in seconds and
    never exceeds ``max_delay``.
    """
    if isinstance(attempt, bool) or not isinstance(attempt, int):
        raise OandaStreamError(f"attempt must be an int, got {type(attempt).__name__}")
    if attempt < 0:
        raise OandaStreamError(f"attempt must not be negative, got {attempt}")

    if isinstance(jitter, bool) or not isinstance(jitter, Decimal):
        raise OandaStreamError(f"jitter must be a Decimal, got {type(jitter).__name__}")
    if not jitter.is_finite():
        raise OandaStreamError("jitter must be finite")
    if jitter < 0 or jitter > 1:
        raise OandaStreamError(f"jitter must be within 0..1, got {jitter}")

    grown = _seconds(policy.base_delay) * (policy.multiplier**attempt)
    capped = min(grown, _seconds(policy.max_delay))
    return capped * jitter


# --------------------------------------------------------------------------- #
# backfill
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class BackfillRequest:
    """An interval of missing market data to recover from REST."""

    start: dt.datetime
    end: dt.datetime

    def __post_init__(self) -> None:
        for field in ("start", "end"):
            value = getattr(self, field)
            if not isinstance(value, dt.datetime) or value.tzinfo is None:
                raise OandaStreamError(f"{field} must be a timezone-aware datetime")
        if self.end < self.start:
            raise OandaStreamError(f"end {self.end!r} is before start {self.start!r}")

    @property
    def duration(self) -> dt.timedelta:
        return self.end - self.start

    @classmethod
    def for_gap(cls, *, last_seen: dt.datetime, resumed: dt.datetime) -> BackfillRequest:
        """Build a request spanning ``last_seen`` to ``resumed``, in UTC."""
        return cls(
            start=last_seen.astimezone(dt.UTC),
            end=resumed.astimezone(dt.UTC),
        )


# --------------------------------------------------------------------------- #
# transport contract
# --------------------------------------------------------------------------- #
class OandaStreamTransport(Protocol):
    """Anything that opens a pricing stream and yields raw byte chunks.

    ``__call__`` is awaited first (the "connect" step) and the result is an async
    iterator (the "read" step). A real aiohttp session implements exactly this shape,
    which keeps the consumer free of any HTTP dependency.
    """

    def __call__(self) -> AsyncIterator[bytes]:
        ...


@dataclass(frozen=True, slots=True)
class OandaCredentials:
    """Account credentials. The token is required; the environment selects endpoints."""

    account_id: str
    token: str
    environment: str = "practice"

    def __post_init__(self) -> None:
        if not isinstance(self.account_id, str) or not self.account_id.strip():
            raise OandaStreamError("account_id must be a non-empty string")
        if not isinstance(self.token, str) or not self.token.strip():
            raise OandaStreamError("token must be a non-empty string")
        if self.environment not in {"practice", "live"}:
            raise OandaStreamError(f"unknown environment {self.environment!r}")

    @property
    def stream_url(self) -> str:
        host = self._host("stream")
        return f"https://{host}/v3/accounts/{self.account_id}/pricing/stream"

    @property
    def rest_url(self) -> str:
        host = self._host("api")
        return f"https://{host}/v3/accounts/{self.account_id}"

    def _host(self, kind: str) -> str:
        prefix = "fxpractice" if self.environment == "practice" else "fxtrade"
        return f"{kind}-{prefix}.oanda.com"


# --------------------------------------------------------------------------- #
# the consumer
# --------------------------------------------------------------------------- #
#: Object shapes the consumer accepts. They stay as runtime-importable Protocols so the
#: injected fakes in the test suite satisfy them structurally, without inheritance.
Sleeper = Callable[[dt.timedelta], Awaitable[None]]
JitterSource = Callable[[], Decimal]
StateCallback = Callable[[FeedState], None]
ShouldStop = Callable[[], bool]
Clock = Callable[[], dt.datetime]
QuoteSink = Callable[[Quote], None]


class OandaPricingStream:
    """Owns the lifecycle of one pricing stream and its reconnections.

    ``run`` is the whole state machine. It returns when the stream was stopped, exhausted
    its attempt budget, or finished a healthy session within budget.
    """

    def __init__(
        self,
        *,
        transport: OandaStreamTransport,
        credentials: OandaCredentials,
        policy: BackoffPolicy,
        staleness: StalenessDetector | None = None,
        clock: Clock | None = None,
        sleeper: Sleeper | None = None,
        jitter: JitterSource | None = None,
        instrument: str = "XAU_USD",
    ) -> None:
        self._transport = transport
        self._credentials = credentials
        self._policy = policy
        self._staleness = staleness or StalenessDetector()
        self._clock: Clock = clock or (lambda: dt.datetime.now(dt.UTC))
        self._sleeper: Sleeper = sleeper or _default_sleeper
        self._jitter: JitterSource = jitter or _uniform_jitter
        self._instrument = instrument

        self._state: FeedState = FeedState.IDLE
        self._monitor = HeartbeatMonitor()
        self._decoder = StreamChunkDecoder()
        self._attempts = 0
        self._backoff_step = 0
        self._backoff_pending = False
        self._healthy = False
        self._last_quote_at: dt.datetime | None = None
        self.backfill_requests: list[BackfillRequest] = []
        self.on_state_change: StateCallback | None = None

    # -- introspection ------------------------------------------------------- #
    @property
    def state(self) -> FeedState:
        return self._state

    @property
    def attempts(self) -> int:
        return self._attempts

    @property
    def monitor(self) -> HeartbeatMonitor:
        return self._monitor

    @property
    def credentials(self) -> OandaCredentials:
        return self._credentials

    @property
    def instrument(self) -> str:
        return self._instrument

    # -- lifecycle ----------------------------------------------------------- #
    def _set_state(self, state: FeedState) -> None:
        if state is self._state:
            return
        self._state = state
        logger.debug("pricing stream %s -> %s", self._instrument, state.value)
        if self.on_state_change is not None:
            self.on_state_change(state)

    async def run(
        self,
        on_quote: QuoteSink,
        *,
        max_attempts: int | None = None,
        should_stop: ShouldStop | None = None,
    ) -> None:
        """Consume the stream until it is asked to stop or exhausts its attempt budget.

        Args:
            on_quote: Callable invoked with each :class:`Quote`.
            max_attempts: Maximum number of connection attempts, ``None`` for unlimited.
            should_stop: Optional predicate polled between reconnects.
        """
        while True:
            if should_stop is not None and should_stop():
                self._set_state(FeedState.STOPPED)
                return

            if max_attempts is not None and self._attempts >= max_attempts:
                if not self._healthy:
                    self._set_state(FeedState.FAILED)
                return

            if self._backoff_pending:
                await self._reconnect()
                continue

            self._attempts += 1
            await self._consume(on_quote)
            if self._session_succeeded:
                self._healthy = True
                # A healthy session earns back the full backoff range.
                self._backoff_step = 0
            else:
                self._healthy = False
                self._backoff_pending = True

    async def _reconnect(self) -> None:
        self._set_state(FeedState.RECONNECTING)
        delay = jittered_backoff(self._policy, self._backoff_step, self._jitter())
        await self._sleeper(_as_timedelta(delay))
        self._backoff_step += 1
        self._backoff_pending = False

    async def _consume(self, on_quote: QuoteSink) -> None:
        """Connect once, consume every chunk, and close the session out."""
        self._set_state(FeedState.CONNECTING)
        self._decoder = StreamChunkDecoder()

        # Seed staleness from the last known quote. A reconnect that opens after a long
        # silence must be judged against that quote, not against an empty monitor, or the
        # gap would be invisible and STALE_FEED would fire for the wrong reason.
        self._monitor = HeartbeatMonitor(quiet_after=self._monitor.quiet_after)
        if self._last_quote_at is not None:
            self._monitor.record_quote(self._last_quote_at)

        self._session_succeeded = False
        self._session_stale = False
        saw_data = False

        connection = await self._transport()
        async for chunk in connection:
            if self._silence_exceeded():
                self._session_stale = True
                self._set_state(FeedState.STALE_FEED)
                logger.warning("feed went silent for more than %s", STALE_AFTER)
                return
            saw_data = self._process_chunk(chunk, on_quote) or saw_data

        if self._silence_exceeded():
            self._session_stale = True
            self._set_state(FeedState.STALE_FEED)
            logger.warning("feed closed after more than %s of silence", STALE_AFTER)
            return

        for line in self._decoder.flush():
            self._process_line(line, on_quote)
            saw_data = True

        self._session_succeeded = saw_data
        if saw_data:
            self._set_state(FeedState.STREAMING)
        else:
            # A stream that produced nothing at all is a dead connection, not a stale feed.
            self._set_state(FeedState.STALE_FEED)
            self._session_stale = True
            logger.warning("stream produced no data")

    def _process_chunk(self, chunk: bytes, on_quote: QuoteSink) -> bool:
        return any(self._process_line(line, on_quote) for line in self._decoder.feed(chunk))

    def _process_line(self, line: str, on_quote: QuoteSink) -> bool:
        payload = decode_stream_line(line)
        if payload is None:
            return False
        message_type = payload.get("type")
        if message_type == "PRICE":
            self._handle_price(payload, on_quote)
            return True
        if message_type == "HEARTBEAT":
            self._monitor.record_heartbeat(parse_heartbeat_message(payload))
            return True
        logger.debug("ignoring stream message type %r", message_type)
        return False

    def _handle_price(self, payload: dict[str, Any], on_quote: QuoteSink) -> None:
        quote = parse_price_message(payload)
        previous = self._last_quote_at
        self._monitor.record_quote(quote.timestamp_utc)

        if self._state is not FeedState.STREAMING:
            self._set_state(FeedState.STREAMING)

        on_quote(quote)

        if previous is not None and quote.timestamp_utc - previous > RECONNECT_GAP:
            self._record_backfill(previous, quote.timestamp_utc)
        self._last_quote_at = quote.timestamp_utc

    def _record_backfill(self, last_seen: dt.datetime, resumed: dt.datetime) -> None:
        request = BackfillRequest.for_gap(last_seen=last_seen, resumed=resumed)
        self.backfill_requests.append(request)
        logger.info("backfill required for %s", request.duration)

    def request_backfill(self, resumed_at: dt.datetime) -> BackfillRequest | None:
        """Record a backfill request for the gap since the last healthy quote."""
        if self._last_quote_at is None:
            return None
        if resumed_at.astimezone(dt.UTC) - self._last_quote_at < RECONNECT_GAP:
            return None
        self._record_backfill(self._last_quote_at, resumed_at)
        return self.backfill_requests[-1]

    def _silence_exceeded(self) -> bool:
        now = self._clock()
        quote_age = (
            elapsed_seconds(now, self._monitor.last_quote_at)
            if self._monitor.last_quote_at is not None
            else None
        )
        heartbeat_age = (
            elapsed_seconds(now, self._monitor.last_heartbeat_at)
            if self._monitor.last_heartbeat_at is not None
            else None
        )
        return self._staleness.is_stale(
            quote_age=quote_age, heartbeat_age=heartbeat_age, now=now
        )


# --------------------------------------------------------------------------- #
# module-level defaults
# --------------------------------------------------------------------------- #
async def _default_sleeper(duration: dt.timedelta) -> None:
    """Sleep for a duration.

    This is the one place a float is unavoidable: ``asyncio.sleep`` takes seconds as a
    float. It is confined to this function, which is replaced by an injected sleeper in
    every test, so no float reaches engine arithmetic.
    """
    await asyncio.sleep(duration.total_seconds())


_JITTER_SOURCE = random.Random()  # noqa: S311 - jitter only, never a security decision


def _uniform_jitter() -> Decimal:
    """A uniform jitter fraction in [0, 1), from integers so no float is ever formed.

    ``random.Random`` is deliberate: the value only spreads reconnect attempts so that many
    workers do not retry in lockstep. Nothing here touches a credential or a hash chain.
    """
    return Decimal(_JITTER_SOURCE.randrange(0, 1_000_000_000)) / Decimal(1_000_000_000)
