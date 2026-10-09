"""In-memory bar construction and reconciliation.

Two aggregators sit here:

* :class:`BarBuilder` folds quotes into completed M1 bars.
* :class:`BarAggregator` folds completed M1 bars into M5 bars.

Both are anchored to a trading session that opens at a fixed **New York wall-clock** time
(17:00 by default), not a fixed UTC hour. That distinction is the whole reason this module
exists: New York sits at UTC-5 in winter and UTC-4 in summer, so an anchor expressed in UTC
would drift an hour twice a year and silently mis-bucket every bar around the transition.

Reconciliation compares completed streamed bars against historical candles. A difference of
more than the tolerance -- one pip by default -- is reported, never silently corrected.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final
from zoneinfo import ZoneInfo

from engine.market.instrument import InstrumentSpec
from engine.market.models import OHLC, Bar, Quote, Timeframe, duration_of, mid_of

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

__all__ = [
    "DEFAULT_SESSION_CLOSE_HOUR",
    "BarAggregator",
    "BarBuilder",
    "BarReconciler",
    "Discrepancy",
    "DiscrepancyKind",
    "M1AnchoredSession",
    "SessionAnchor",
    "TradingSession",
    "bucket_start",
]


#: The New York clock hour at which a trading session opens. Wall clock, never UTC.
DEFAULT_SESSION_CLOSE_HOUR: Final[int] = 17


def bucket_start(timestamp_utc: dt.datetime, timeframe: Timeframe) -> dt.datetime:
    """Floor a UTC timestamp onto its bar grid.

    Grid boundaries are absolute UTC instants, so this is a pure modulo on UTC seconds and
    is immune to DST entirely. The *session anchor* is a separate, wall-clock notion
    computed by :class:`SessionAnchor`.
    """
    if not isinstance(timestamp_utc, dt.datetime) or timestamp_utc.tzinfo is None:
        raise ValueError(
            f"timestamp must be a timezone-aware datetime, got {timestamp_utc!r}"
        )
    if not isinstance(timeframe, Timeframe):
        raise ValueError(f"timeframe must be a Timeframe, got {timeframe!r}")

    span = int(duration_of(timeframe).total_seconds())
    epoch = dt.datetime(1970, 1, 1, tzinfo=dt.UTC)
    elapsed = int((timestamp_utc.astimezone(dt.UTC) - epoch).total_seconds())
    return epoch + dt.timedelta(seconds=(elapsed // span) * span)


@dataclass(frozen=True, slots=True)
class TradingSession:
    """One anchored trading session: ``[start, end)`` in UTC."""

    start: dt.datetime
    end: dt.datetime
    tz: ZoneInfo | None = None

    @property
    def duration(self) -> dt.timedelta:
        return self.end - self.start

    def contains(self, timestamp_utc: dt.datetime) -> bool:
        return self.start <= timestamp_utc < self.end

    def iter_m1(self) -> Iterable[dt.datetime]:
        """Every M1 boundary inside the session.

        Bucketing happens on UTC minutes, so this simply walks the interval. The
        spring-forward gap is deliberately *not* filled in: no wall-clock minute exists
        there, so inventing one would fabricate data.
        """
        cursor = self.start
        while cursor < self.end:
            yield cursor
            cursor = cursor + dt.timedelta(minutes=1)


class SessionAnchor:
    """Maps any instant to the trading session it belongs to.

    The anchor hour is interpreted in the session's timezone as wall clock. A timestamp at
    or after 17:00 New York belongs to the session that opens then; anything earlier
    belongs to the session that opened at 17:00 New York on the previous day.
    """

    def __init__(
        self,
        close_hour: int = DEFAULT_SESSION_CLOSE_HOUR,
        tz: ZoneInfo | None = None,
    ) -> None:
        if not isinstance(close_hour, int) or not 0 <= close_hour <= 23:
            raise ValueError(f"close_hour must be 0..23, got {close_hour!r}")
        self._close_hour = close_hour
        self._tz = tz if tz is not None else ZoneInfo("America/New_York")

    @property
    def close_hour(self) -> int:
        return self._close_hour

    @property
    def tz(self) -> ZoneInfo:
        return self._tz

    def _local_close(self, local_date: dt.date) -> dt.datetime:
        """The 17:00 New York instant on ``local_date``, expressed in UTC.

        Resolved through ``ZoneInfo``, so the offset reflects that date's DST rule and
        never a fixed UTC offset.
        """
        local = dt.datetime.combine(local_date, dt.time(self._close_hour), tzinfo=self._tz)
        return local.astimezone(dt.UTC)

    def session_start(self, timestamp_utc: dt.datetime) -> dt.datetime:
        """UTC instant of the session containing ``timestamp_utc``."""
        if not isinstance(timestamp_utc, dt.datetime) or timestamp_utc.tzinfo is None:
            raise ValueError(f"timestamp must be timezone-aware, got {timestamp_utc!r}")

        utc = timestamp_utc.astimezone(dt.UTC)
        local = utc.astimezone(self._tz)
        # Compare the full local time, not just the hour: an instant at 09:00 local on the
        # same date as an earlier 17:00 close belongs to that earlier session.
        if local.time() >= dt.time(self._close_hour):
            return self._local_close(local.date())
        return self._local_close(local.date() - dt.timedelta(days=1))

    def session_end(self, session_start_utc: dt.datetime) -> dt.datetime:
        """UTC instant 24 hours of wall clock after the session opened."""
        start_local = session_start_utc.astimezone(self._tz)
        next_local = start_local + dt.timedelta(hours=24)
        return next_local.astimezone(dt.UTC)

    def session(self, timestamp_utc: dt.datetime) -> TradingSession:
        start = self.session_start(timestamp_utc)
        return TradingSession(start=start, end=self.session_end(start), tz=self._tz)


class M1AnchoredSession:
    """A session's worth of M1 buckets, gap-aware across DST transitions."""

    def __init__(
        self,
        anchor: SessionAnchor,
        *,
        builder: BarBuilder | None = None,
        spec: InstrumentSpec | None = None,
    ) -> None:
        self._anchor = anchor
        if builder is not None:
            self._builder = builder
        elif spec is not None:
            self._builder = BarBuilder(spec=spec)
        else:
            self._builder = BarBuilder()
        self._start_utc: dt.datetime | None = None

    @property
    def anchor(self) -> SessionAnchor:
        return self._anchor

    def extend_to(self, timestamp_utc: dt.datetime, volume: int = 0) -> None:
        """Advance the session's M1 clock to ``timestamp_utc``, if later."""
        if self._start_utc is None:
            self._start_utc = self._anchor.session_start(timestamp_utc)
        elif timestamp_utc <= self._start_utc:
            return
        cursor = self._start_utc + dt.timedelta(minutes=1)
        while cursor <= timestamp_utc:
            self._builder.touch(cursor, volume=volume)
            cursor = cursor + dt.timedelta(minutes=1)

    def bars(self) -> list[Bar]:
        return self._builder.completed()


@dataclass(slots=True)
class _Bucket:
    """Accumulation state for one open bar."""

    open: Quote
    bid_high: Decimal
    bid_low: Decimal
    ask_high: Decimal
    ask_low: Decimal
    last: Quote
    ticks: int = 1

    @property
    def bid(self) -> OHLC:
        return OHLC(
            open=self.open.bid,
            high=self.bid_high,
            low=self.bid_low,
            close=self.last.bid,
        )

    @property
    def ask(self) -> OHLC:
        return OHLC(
            open=self.open.ask,
            high=self.ask_high,
            low=self.ask_low,
            close=self.last.ask,
        )


class BarBuilder:
    """Folds quotes into completed bars on a fixed grid."""

    def __init__(
        self,
        *,
        spec: InstrumentSpec | None = None,
        timeframe: Timeframe = Timeframe.M1,
    ) -> None:
        self._spec = spec
        self._timeframe = timeframe
        self._current: _Bucket | None = None
        self._current_start: dt.datetime | None = None
        self._completed: list[Bar] = []

    @property
    def spec(self) -> InstrumentSpec | None:
        return self._spec

    @property
    def timeframe(self) -> Timeframe:
        return self._timeframe

    @property
    def pending(self) -> Bar | None:
        """The bar currently being accumulated, if any."""
        if self._current is None or self._current_start is None:
            return None
        return self._finalize(self._current_start, self._current, complete=False)

    @property
    def open_start(self) -> dt.datetime | None:
        return self._current_start

    def add_quote(self, quote: Quote) -> list[Bar]:
        """Add one quote, returning any bars that its arrival completed."""
        started = bucket_start(quote.timestamp_utc, self._timeframe)
        return self._ingest(quote, started)

    def touch(self, timestamp_utc: dt.datetime, *, volume: int = 0) -> list[Bar]:
        """Advance the internal clock without adding a quote.

        Used by the session to close bars when no ticks arrive. ``volume`` is cumulative
        and ignored here: the quote count already carries it.
        """
        started = bucket_start(timestamp_utc, self._timeframe)
        if self._current_start is None:
            # A tick with no quote still opens the bucket so it can be closed later.
            self._current_start = started
            self._current = None
            return []
        if started > self._current_start:
            return self._roll(started)
        return []

    def _ingest(self, quote: Quote, started: dt.datetime) -> list[Bar]:
        if self._current_start is None:
            self._open(quote, started)
            return []

        if started < self._current_start:
            # A late quote for a bar that has already closed.
            return []

        if started == self._current_start:
            self._amend(quote)
            return []

        emitted = self._roll(started)
        self._open(quote, started)
        return emitted

    def _open(self, quote: Quote, started: dt.datetime) -> None:
        self._current_start = started
        self._current = _Bucket(
            open=quote,
            bid_high=quote.bid,
            bid_low=quote.bid,
            ask_high=quote.ask,
            ask_low=quote.ask,
            last=quote,
            ticks=1,
        )

    def _amend(self, quote: Quote) -> None:
        bucket = self._current
        if bucket is None:  # pragma: no cover - _ingest guarantees a bucket exists
            raise RuntimeError("no bar is open")
        bucket.bid_high = max(bucket.bid_high, quote.bid)
        bucket.bid_low = min(bucket.bid_low, quote.bid)
        bucket.ask_high = max(bucket.ask_high, quote.ask)
        bucket.ask_low = min(bucket.ask_low, quote.ask)
        bucket.last = quote
        bucket.ticks += 1

    def _roll(self, next_start: dt.datetime) -> list[Bar]:
        if self._current is None or self._current_start is None:
            self._current_start = next_start
            self._current = None
            return []
        emitted = [self._finalize(self._current_start, self._current, complete=True)]
        self._completed.extend(emitted)
        self._current = None
        self._current_start = None
        return emitted

    def _finalize(self, started: dt.datetime, bucket: _Bucket, *, complete: bool) -> Bar:
        return Bar(
            timeframe=self._timeframe,
            timestamp_utc=started,
            complete=complete,
            volume=bucket.ticks,
            bid_ohlc=bucket.bid,
            ask_ohlc=bucket.ask,
        )

    def flush(self) -> Bar | None:
        """Close and return the open bar, if there is one."""
        if self._current is None or self._current_start is None:
            return None
        bar = self._finalize(self._current_start, self._current, complete=True)
        self._completed.append(bar)
        self._current = None
        self._current_start = None
        return bar

    def completed(self) -> list[Bar]:
        """Every bar completed so far."""
        return list(self._completed)


@dataclass(slots=True)
class _AggregateState:
    """Running OHLC fold for an in-progress higher-timeframe bar."""

    bid_ohlc: OHLC
    ask_ohlc: OHLC
    volume: int
    count: int

    @classmethod
    def empty(cls, bar: Bar) -> _AggregateState:
        return cls(bid_ohlc=bar.bid_ohlc, ask_ohlc=bar.ask_ohlc, volume=bar.volume, count=1)

    def absorb(self, bar: Bar) -> None:
        self.bid_ohlc = OHLC(
            open=self.bid_ohlc.open,
            high=max(self.bid_ohlc.high, bar.bid_ohlc.high),
            low=min(self.bid_ohlc.low, bar.bid_ohlc.low),
            close=bar.bid_ohlc.close,
        )
        self.ask_ohlc = OHLC(
            open=self.ask_ohlc.open,
            high=max(self.ask_ohlc.high, bar.ask_ohlc.high),
            low=min(self.ask_ohlc.low, bar.ask_ohlc.low),
            close=bar.ask_ohlc.close,
        )
        self.volume += bar.volume
        self.count += 1


class BarAggregator:
    """Aggregates completed lower-timeframe bars into a higher timeframe."""

    def __init__(
        self,
        *,
        spec: InstrumentSpec | None = None,
        source: Timeframe = Timeframe.M1,
        target: Timeframe = Timeframe.M5,
    ) -> None:
        if not isinstance(source, Timeframe) or not isinstance(target, Timeframe):
            raise ValueError("source and target must be Timeframes")
        if duration_of(target) <= duration_of(source):
            raise ValueError("target timeframe must be longer than the source timeframe")
        self._spec = spec
        self._source = source
        self._target = target
        self._state: _AggregateState | None = None
        self._state_start: dt.datetime | None = None

    @property
    def timeframe(self) -> Timeframe:
        return self._target

    def add(self, bar: Bar) -> list[Bar]:
        """Add a completed source bar, returning any completed target bars."""
        if bar.timeframe != self._source:
            raise ValueError(f"expected a {self._source} bar, got {bar.timeframe}")
        if not bar.complete:
            return []

        started = bucket_start(bar.timestamp_utc, self._target)

        if self._state is None or self._state_start is None:
            self._state, self._state_start = _AggregateState.empty(bar), started
            return []

        if started == self._state_start:
            self._state.absorb(bar)
            return []

        if started < self._state_start:
            # An out-of-order source bar cannot extend an already closed bucket.
            return []

        emitted = [self._close()]
        self._state, self._state_start = _AggregateState.empty(bar), started
        return emitted

    def _close(self) -> Bar:
        if self._state is None or self._state_start is None:
            raise RuntimeError("no higher-timeframe bar is open")
        state = self._state
        bar = Bar(
            timeframe=self._target,
            timestamp_utc=self._state_start,
            complete=True,
            volume=state.volume,
            bid_ohlc=state.bid_ohlc,
            ask_ohlc=state.ask_ohlc,
        )
        self._state = None
        self._state_start = None
        return bar

    def flush(self) -> Bar | None:
        """Close and return the in-progress higher-timeframe bar, if any."""
        if self._state is None:
            return None
        return self._close()

    @property
    def pending(self) -> Bar | None:
        if self._state is None:
            return None
        return Bar(
            timeframe=self._target,
            timestamp_utc=self._state_start,
            complete=False,
            volume=self._state.volume,
            bid_ohlc=self._state.bid_ohlc,
            ask_ohlc=self._state.ask_ohlc,
        )


class DiscrepancyKind(StrEnum):
    """Why a streamed bar did not match the historical record."""

    MISSING = "MISSING"
    UNEXPECTED = "UNEXPECTED"
    PRICE = "PRICE"


@dataclass(frozen=True, slots=True)
class Discrepancy:
    """One disagreement between the stream and the historical record."""

    kind: DiscrepancyKind
    bucket_start: dt.datetime
    timeframe: Timeframe
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def difference(self) -> Decimal | None:
        return self.detail.get("difference")


_COMPARED_FIELDS: Final[tuple[tuple[str, str], ...]] = (
    ("bid_open", "bid_ohlc.open"),
    ("bid_high", "bid_ohlc.high"),
    ("bid_low", "bid_ohlc.low"),
    ("bid_close", "bid_ohlc.close"),
    ("ask_open", "ask_ohlc.open"),
    ("ask_high", "ask_ohlc.high"),
    ("ask_low", "ask_ohlc.low"),
    ("ask_close", "ask_ohlc.close"),
    ("mid_open", "mid_ohlc.open"),
    ("mid_high", "mid_ohlc.high"),
    ("mid_low", "mid_ohlc.low"),
    ("mid_close", "mid_ohlc.close"),
)


def _resolve(bar: Bar, path: str) -> Decimal:
    head, _, leaf = path.partition(".")
    if head == "mid_ohlc":
        return getattr(bar.mid_ohlc, leaf)
    return getattr(getattr(bar, head), leaf)


class BarReconciler:
    """Compares completed streamed bars against historical candles.

    Only differences strictly greater than the tolerance are reported; a difference of
    exactly one pip is treated as agreement. Mid-side values are compared too, because a
    strategy reading mid bars would otherwise never notice a divergence in both sides.
    """

    def __init__(
        self,
        *,
        spec: InstrumentSpec,
        timeframe: Timeframe = Timeframe.M1,
        tolerance_pips: Decimal | None = None,
    ) -> None:
        if not isinstance(spec, InstrumentSpec):
            raise ValueError("spec must be an InstrumentSpec")
        if not isinstance(timeframe, Timeframe):
            raise ValueError(f"timeframe must be a Timeframe, got {timeframe!r}")
        self._spec = spec
        self._timeframe = timeframe
        self._tolerance = (
            spec.pip if tolerance_pips is None else Decimal(tolerance_pips) * spec.pip
        )

    @property
    def tolerance(self) -> Decimal:
        return self._tolerance

    @property
    def spec(self) -> InstrumentSpec:
        return self._spec

    def reconcile(
        self, streamed: Sequence[Bar], historical: Sequence[Bar]
    ) -> list[Discrepancy]:
        """Return every discrepancy in bucketed, then field, order."""
        for bar in (*streamed, *historical):
            if bar.timeframe != self._timeframe:
                raise ValueError(
                    f"reconciler is configured for {self._timeframe}, got a {bar.timeframe} bar"
                )

        # An incomplete bar is still evidence that the bucket exists: it simply had not
        # closed yet. Treating it as MISSING would report a reconciliation failure on every
        # bar the reconciler happens to straddle.
        stream_map: dict[dt.datetime, Bar] = {}
        for bar in streamed:
            started = bucket_start(bar.timestamp_utc, self._timeframe)
            existing = stream_map.get(started)
            if existing is None or bar.complete:
                stream_map[started] = bar
        incomplete = {s for s, b in stream_map.items() if not b.complete}

        history_map = {
            bucket_start(bar.timestamp_utc, self._timeframe): bar
            for bar in historical
            if bar.complete
        }

        discrepancies: list[Discrepancy] = []
        for started in sorted(set(stream_map) | set(history_map)):
            streamed_bar = stream_map.get(started)
            historical_bar = history_map.get(started)

            if streamed_bar is None:
                discrepancies.append(
                    Discrepancy(
                        kind=DiscrepancyKind.MISSING,
                        bucket_start=started,
                        timeframe=self._timeframe,
                    )
                )
                continue
            if started in incomplete:
                # The stream has the bucket open but not closed; nothing to compare yet.
                continue
            if historical_bar is None:
                discrepancies.append(
                    Discrepancy(
                        kind=DiscrepancyKind.UNEXPECTED,
                        bucket_start=started,
                        timeframe=self._timeframe,
                    )
                )
                continue

            discrepancies.extend(self._compare(started, streamed_bar, historical_bar))
        return discrepancies

    def _compare(self, started: dt.datetime, streamed: Bar, historical: Bar) -> list[Discrepancy]:
        found: list[Discrepancy] = []
        for label, path in _COMPARED_FIELDS:
            streamed_value = _resolve(streamed, path)
            historical_value = _resolve(historical, path)
            difference = abs(streamed_value - historical_value)
            if difference > self._tolerance:
                found.append(
                    Discrepancy(
                        kind=DiscrepancyKind.PRICE,
                        bucket_start=started,
                        timeframe=self._timeframe,
                        detail={
                            "field": label,
                            "streamed": str(streamed_value),
                            "historical": str(historical_value),
                            "difference": difference,
                            "tolerance": self._tolerance,
                        },
                    )
                )
        return found


#: Mid derivation helper exposed so callers do not re-implement it.
def mid_ohlc_of(bar: Bar) -> OHLC:
    return OHLC(
        open=mid_of(bar.bid_ohlc.open, bar.ask_ohlc.open),
        high=mid_of(bar.bid_ohlc.high, bar.ask_ohlc.high),
        low=mid_of(bar.bid_ohlc.low, bar.ask_ohlc.low),
        close=mid_of(bar.bid_ohlc.close, bar.ask_ohlc.close),
    )
