"""Tests for the replay engine and the no-repaint proof.

The replay is checked the way a backtest should be: not by re-asserting the
strategy's arithmetic, but by proving the *pipeline* is honest. Three properties carry
the weight.

**Nothing depends on a bar that had not printed.** :func:`verify_no_repaint` reruns
the whole replay over every prefix of a bar list and compares it against the full run
truncated at that prefix. A signal computed from a bar still developing, a level
quoted before it concluded, or a fill that needed the future would all show up as a
difference.

**A bar streamed from ticks is the bar a batch fold produces.** The same quote list is
aggregated twice -- once incrementally through the Phase 1 builders, once in bulk --
and reconciled, then replayed. If the two paths disagreed, every number the engine
prints would depend on how the data happened to arrive.

**The fills are pessimistic.** An entry needs a tick of travel, a stop triggers on a
touch and fills beyond it, and a bar that spans both is resolved against the trade.
"""

import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from engine.domain.orders import OrderSide
from engine.domain.positions import PositionSide
from engine.metrics.performance import compute_performance
from engine.strategy.backtest import (
    BacktestRunner,
    ExitReason,
    batch_m5,
    replay,
    stream_m5,
    verify_no_repaint,
    verify_tick_stream_matches_batch,
)
from engine.strategy.config import StrategyConfig
from engine.strategy.silver_bullet import RangeName, SilverBulletStrategy, StrategyFeedError

NEW_YORK = ZoneInfo("America/New_York")

#: The exit order's seeded slippage for run "test" on this instrument: one buy limit,
#: one stop, one dynamic close. Read off the fill so the assertion documents the
#: pessimism rather than the seed.


#: The simulated broker's ceiling on adverse slippage, in ticks.
MAX_SLIPPAGE_TICKS = 8


def _local(bar) -> dt.datetime:
    return bar.timestamp_utc.astimezone(NEW_YORK)


def _quotes(bars, quote_factory) -> list:
    return [quote for bar in bars for quote in quote_factory(bar)]


# --------------------------------------------------------------------------- #
# incremental consumption and observation
# --------------------------------------------------------------------------- #
def test_accept_feeds_one_bar_at_a_time(
    strategy_config: StrategyConfig, spec, canonical_day_factory
) -> None:
    bars = canonical_day_factory(dt.date(2026, 3, 9))
    strategy = SilverBulletStrategy(config=strategy_config, spec=spec, run_id="test")
    runner = BacktestRunner(strategy=strategy, spec=spec, run_id="test")

    closed: list[object] = []
    for bar in bars:
        closed.extend(runner.accept(bar))

    assert len(closed) == 1
    assert closed[0].exit_reason is ExitReason.TARGET
    assert runner.bars_consumed == len(bars)


def test_a_replay_is_exactly_a_loop_over_accept(
    strategy_config: StrategyConfig, spec, canonical_day_factory
) -> None:
    """Two implementations of the fill rules would be two fill rules."""
    bars = canonical_day_factory(dt.date(2026, 3, 9))

    streamed = SilverBulletStrategy(config=strategy_config, spec=spec, run_id="test")
    incremental = BacktestRunner(strategy=streamed, spec=spec, run_id="test")
    closed: list[object] = []
    for bar in bars:
        closed.extend(incremental.accept(bar))

    # A replay constructs its own strategy, which is what makes it a replay: the
    # incremental runner is single-use, and ``run`` documents that.
    replayed = replay(bars, config=strategy_config, spec=spec, run_id="test").trades

    assert tuple(closed) == replayed


def test_the_observer_sees_every_event_of_a_round_trip(
    strategy_config: StrategyConfig, spec, canonical_day_factory
) -> None:
    from engine.strategy.backtest import ReplayEventKind

    bars = canonical_day_factory(dt.date(2026, 3, 9))
    strategy = SilverBulletStrategy(config=strategy_config, spec=spec, run_id="test")
    seen: list[ReplayEventKind] = []
    runner = BacktestRunner(
        strategy=strategy,
        spec=spec,
        run_id="test",
        observer=lambda event: seen.append(event.kind),
    )

    runner.run(bars)

    # An intent, then the fill, then the exit and the trade it produced.
    assert seen[0] is ReplayEventKind.INTENT
    assert ReplayEventKind.ENTRY_FILL in seen
    assert seen[-1] is ReplayEventKind.TRADE
    assert seen.count(ReplayEventKind.TRADE) == 1


def test_the_observer_gets_the_prices_a_journal_would_record(
    strategy_config: StrategyConfig, spec, canonical_day_factory
) -> None:
    from engine.strategy.backtest import ReplayEventKind

    bars = canonical_day_factory(dt.date(2026, 3, 9), reaches_target=False)
    strategy = SilverBulletStrategy(config=strategy_config, spec=spec, run_id="test")
    events: list[object] = []
    runner = BacktestRunner(
        strategy=strategy,
        spec=spec,
        run_id="test",
        observer=events.append,
    )

    runner.run(bars)

    fills = [event for event in events if event.kind is ReplayEventKind.TRADE]
    trade = fills[0].trade
    assert trade.exit_price == Decimal("2008.38")
    assert trade.pnl == Decimal("2.03")
    assert fills[0].fill.price == trade.exit_price


# --------------------------------------------------------------------------- #
# forced exits
# --------------------------------------------------------------------------- #
def test_withdrawing_every_order_empties_the_book(
    strategy_config: StrategyConfig, spec, canonical_day_factory
) -> None:
    bars = canonical_day_factory(dt.date(2026, 3, 9), reaches_target=False)
    strategy = SilverBulletStrategy(config=strategy_config, spec=spec, run_id="test")
    runner = BacktestRunner(strategy=strategy, spec=spec, run_id="test")
    runner.run(bars[: _cut(bars, dt.date(2026, 3, 9), dt.time(3, 25))])

    assert runner.strategy.resting_orders
    withdrawn = runner.withdraw_all(at=bars[0].timestamp_utc, reason="control plane")

    assert withdrawn
    assert runner.strategy.resting_orders == ()


def test_flattening_the_book_closes_every_position_at_market(
    strategy_config: StrategyConfig, spec, canonical_day_factory
) -> None:
    """A forced flatten is measured against the last quote, not a fabricated bar."""
    bars = canonical_day_factory(dt.date(2026, 3, 9), reaches_target=False)
    strategy = SilverBulletStrategy(config=strategy_config, spec=spec, run_id="test")
    runner = BacktestRunner(strategy=strategy, spec=spec, run_id="test")
    cut = _cut(bars, dt.date(2026, 3, 9), dt.time(3, 30))
    runner.run(bars[:cut])

    assert runner.strategy.positions
    closed = runner.flatten_all(bar=bars[cut - 1], reason="control plane")

    assert len(closed) == 1
    assert closed[0].exit_reason is ExitReason.DYNAMIC_CLOSE
    assert runner.strategy.positions == ()


def _cut(bars, day: dt.date, clock: dt.time) -> int:
    """The first bar at or after ``clock`` on ``day``, as a list length."""
    for index, bar in enumerate(bars):
        local = bar.timestamp_utc.astimezone(NEW_YORK)
        if local.date() == day and local.time() >= clock:
            return index
    return len(bars)


# --------------------------------------------------------------------------- #
# the round trip
# --------------------------------------------------------------------------- #
def test_a_canonical_day_produces_one_winning_trade(
    strategy_config: StrategyConfig, spec, canonical_day_factory
) -> None:
    bars = canonical_day_factory(dt.date(2026, 3, 9))
    result = replay(bars, config=strategy_config, spec=spec, run_id="test")

    assert result.trade_count == 1
    trade = result.trades[0]
    assert trade.side is PositionSide.LONG
    assert trade.reference is RangeName.ASIAN.value
    assert trade.entry_price == Decimal("2006.35")
    assert trade.exit_price == Decimal("2009.75")
    assert trade.exit_reason is ExitReason.TARGET
    assert trade.quantity == Decimal("1")
    assert trade.pnl == Decimal("3.40")
    assert trade.r_multiple == Decimal("2")
    assert result.expectancy_r == Decimal("2")
    assert result.wins == 1
    assert result.losses == 0


def test_the_order_lives_until_it_is_filled(
    strategy_config: StrategyConfig, spec, canonical_day_factory
) -> None:
    bars = canonical_day_factory(dt.date(2026, 3, 9))
    result = replay(bars, config=strategy_config, spec=spec, run_id="test")

    order = next(o for o in result.orders if o.status == "FILLED")
    assert order.side is OrderSide.BUY
    assert order.limit_price == Decimal("2006.35")
    assert order.filled_at == order.placed_at + dt.timedelta(minutes=5)
    assert order.fill_price == Decimal("2006.35")


def test_a_held_position_is_flattened_at_the_dynamic_close(
    strategy_config: StrategyConfig, spec, canonical_day_factory
) -> None:
    bars = canonical_day_factory(dt.date(2026, 3, 9), reaches_target=False)
    result = replay(bars, config=strategy_config, spec=spec, run_id="test")
    closing_bar = next(
        bar
        for bar in bars
        if _local(bar).date() == dt.date(2026, 3, 9) and _local(bar).time() == dt.time(16, 45)
    )

    assert result.trade_count == 1
    trade = result.trades[0]
    assert trade.exit_reason is ExitReason.DYNAMIC_CLOSE
    # A market exit receives the lowest bid in the closing bar, less the seeded
    # slippage: not the mark, and never a better price than the bar printed. The
    # assertion bounds the slippage rather than pinning it to a seed.
    floor = closing_bar.bid_ohlc.low - (Decimal(MAX_SLIPPAGE_TICKS) * spec.tick)
    ceiling = closing_bar.bid_ohlc.low - spec.tick
    assert floor <= trade.exit_price <= ceiling
    assert trade.exit_time == closing_bar.timestamp_utc


def test_nothing_is_left_open_at_the_end_of_a_day(
    strategy_config: StrategyConfig, spec, canonical_day_factory
) -> None:
    """The dynamic close is the last word, so a book left open is a defect."""
    for reaches_target in (True, False):
        bars = canonical_day_factory(dt.date(2026, 3, 9), reaches_target=reaches_target)
        result = replay(bars, config=strategy_config, spec=spec, run_id="test")

        assert result.open_positions == ()


def test_a_bearish_day_produces_one_short_trade(
    strategy_config: StrategyConfig, spec, ny_am_day_factory
) -> None:
    bars = ny_am_day_factory(dt.date(2026, 3, 9))
    result = replay(bars, config=strategy_config, spec=spec, run_id="test")

    assert result.trade_count == 1
    trade = result.trades[0]
    assert trade.side is PositionSide.SHORT
    assert trade.reference is RangeName.LONDON.value
    assert trade.exit_reason is ExitReason.TARGET
    assert trade.r_multiple == Decimal("2")


def test_the_replay_is_reproducible(
    strategy_config: StrategyConfig, spec, canonical_day_factory
) -> None:
    bars = canonical_day_factory(dt.date(2026, 3, 9))

    first = replay(bars, config=strategy_config, spec=spec, run_id="test")
    second = replay(bars, config=strategy_config, spec=spec, run_id="test")

    assert first.trades == second.trades
    assert first.orders == second.orders


def test_the_trades_feed_the_reporting_layer(
    strategy_config: StrategyConfig, spec, canonical_day_factory
) -> None:
    """A replay's trades are the reporting layer's input, unchanged."""
    bars = canonical_day_factory(dt.date(2026, 3, 9))
    result = replay(bars, config=strategy_config, spec=spec, run_id="test")

    report = compute_performance(result.to_trade_results())

    assert report.trade_count == 1
    assert report.status == "INSUFFICIENT_DATA"
    assert report.expectancy_r == 2.0


# --------------------------------------------------------------------------- #
# no repaint
# --------------------------------------------------------------------------- #
def test_the_canonical_day_does_not_repaint(
    strategy_config: StrategyConfig, spec, canonical_day_factory
) -> None:
    bars = canonical_day_factory(dt.date(2026, 3, 9))
    report = verify_no_repaint(bars, config=strategy_config, spec=spec, prefix_stride=7)

    assert report.is_clean, report.reason
    assert report.bars == len(bars)
    assert report.prefixes == (len(bars) + 6) // 7


def test_no_repaint_is_checked_on_every_prefix(
    strategy_config: StrategyConfig, spec, ny_am_day_factory
) -> None:
    """A day cut short after the London window, so stride one stays quick."""
    bars = ny_am_day_factory(dt.date(2026, 3, 9))
    cut = next(
        index
        for index, bar in enumerate(bars)
        if _local(bar).time() >= dt.time(5, 0) and _local(bar).date() == dt.date(2026, 3, 9)
    )
    window = bars[:cut]

    report = verify_no_repaint(window, config=strategy_config, spec=spec)

    assert report.is_clean, report.reason
    assert report.prefixes == len(window)


def test_a_shorter_prefix_of_the_same_day_agrees(
    strategy_config: StrategyConfig, spec, canonical_day_factory
) -> None:
    bars = canonical_day_factory(dt.date(2026, 3, 9))
    full = replay(bars, config=strategy_config, spec=spec, run_id="test")
    cut = next(index for index, bar in enumerate(bars) if _local(bar).time() >= dt.time(3, 30))
    prefix = replay(bars[:cut], config=strategy_config, spec=spec, run_id="test")

    boundary = bars[cut - 1].timestamp_utc
    assert prefix.trades == tuple(
        trade for trade in full.trades if trade.exit_time <= boundary
    )
    assert prefix.orders == tuple(order for order in full.orders if order.placed_at <= boundary)


# --------------------------------------------------------------------------- #
# tick stream vs batch
# --------------------------------------------------------------------------- #
def test_a_bar_streamed_from_ticks_is_the_bar_a_batch_fold_produces(
    strategy_config: StrategyConfig, spec, canonical_day_factory, quote_factory
) -> None:
    bars = canonical_day_factory(dt.date(2026, 3, 9))
    quotes = _quotes(bars, quote_factory)

    live = stream_m5(quotes, spec=spec)
    batched = batch_m5(quotes, spec=spec)

    # The stream publishes a bar only once the bucket after it has started, so it is
    # one bar behind the batch fold of the same quotes.
    assert len(batched) == len(bars)
    assert len(live) == len(bars) - 1
    assert [bar.timestamp_utc for bar in live] == [bar.timestamp_utc for bar in batched[:-1]]


def test_the_two_aggregation_paths_reconcile(
    strategy_config: StrategyConfig, spec, canonical_day_factory, quote_factory
) -> None:
    bars = canonical_day_factory(dt.date(2026, 3, 9))
    quotes = _quotes(bars, quote_factory)

    report = verify_tick_stream_matches_batch(quotes, config=strategy_config, spec=spec)

    assert report.live_bars == len(bars) - 1
    assert report.batch_bars == len(bars)
    assert report.is_clean, "the tick stream and the batch fold disagree"


def test_the_tick_streamed_bars_match_the_source_bars(
    strategy_config: StrategyConfig, spec, canonical_day_factory, quote_factory
) -> None:
    """The strongest form of the same claim: the stream rebuilds the source bars."""
    bars = canonical_day_factory(dt.date(2026, 3, 9))
    quotes = _quotes(bars, quote_factory)

    live = stream_m5(quotes, spec=spec)
    source_by_start = {bar.timestamp_utc: bar for bar in bars}
    # Every bar the stream published is the source bar for that bucket, price for
    # price. The stream simply has not published the last one yet.
    assert len(live) == len(bars) - 1
    for streamed in live:
        source = source_by_start[streamed.timestamp_utc]
        assert streamed.mid_ohlc.high == source.mid_ohlc.high
        assert streamed.mid_ohlc.low == source.mid_ohlc.low
        assert streamed.mid_ohlc.close == source.mid_ohlc.close
        assert streamed.mid_ohlc.open == source.mid_ohlc.open


def test_the_tick_stream_path_makes_the_same_trade(
    strategy_config: StrategyConfig, spec, canonical_day_factory, quote_factory
) -> None:
    bars = canonical_day_factory(dt.date(2026, 3, 9))
    quotes = _quotes(bars, quote_factory)

    live = replay(
        stream_m5(quotes, spec=spec), config=strategy_config, spec=spec, run_id="test"
    )
    direct = replay(bars[:-1], config=strategy_config, spec=spec, run_id="test")

    assert live.trades == direct.trades


def test_the_replay_refuses_a_bar_the_strategy_cannot_read(
    strategy_config: StrategyConfig, spec, canonical_day_factory
) -> None:
    bars = canonical_day_factory(dt.date(2026, 3, 9))
    strategy = SilverBulletStrategy(config=strategy_config, spec=spec, run_id="test")

    with pytest.raises(StrategyFeedError):
        BacktestRunner(strategy=strategy, spec=spec, run_id="test").run(
            [*bars[:10], bars[10], *bars[10:]]
        )
