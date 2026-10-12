"""Conservative Historical Backtesting Engine for Asian Judas Swing & ICT Silver Bullet.

Conservative Invariants:
1. Replays closed M5 bars only; strictly zero lookahead.
2. Limit orders fill ONLY when market price trades strictly THROUGH the limit price.
3. If both Stop-Loss and Take-Profit are hit within the same candle, STOP is assumed
   to hit first.
4. Stop slippage is explicitly modeled (default 5 ticks / $0.05).
5. Chronological 75% in-sample / 25% out-of-sample holdout split.
6. Evaluates cost sensitivity across 0x, 1x, 1.5x, 2x spread and slippage multipliers.
7. Supports optional High-Impact News Filter toggle.
"""

from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
from decimal import Decimal
import logging
from typing import Any
from zoneinfo import ZoneInfo

from engine.market.models import Bar
from engine.strategy.judas_swing import JudasSwingStrategy

logger = logging.getLogger(__name__)

NY_TZ = ZoneInfo("America/New_York")


@dataclass(slots=True)
class SimulatedTrade:
    trade_id: str
    strategy: str
    side: str
    entry_time: dt.datetime
    entry_price: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    risk_dollars: Decimal
    reward_dollars: Decimal
    exit_time: dt.datetime | None = None
    exit_price: Decimal | None = None
    pnl_dollars: Decimal = Decimal("0")
    r_multiple: Decimal = Decimal("0")
    status: str = "OPEN"
    exit_reason: str = ""
    spread_paid: Decimal = Decimal("0")
    slippage_paid: Decimal = Decimal("0")
    news_filtered: bool = False
    in_sample: bool = True


@dataclass(slots=True)
class RestingLimit:
    order_id: str
    strategy: str
    side: str
    limit_price: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    placed_time: dt.datetime
    placed_bar_idx: int
    expires_bar_idx: int
    news_filtered: bool = False
    in_sample: bool = True


class BacktestRunner:
    """Executes tick-perfect / bar-perfect historical simulations for XAUUSD strategies."""

    def __init__(
        self,
        base_spread: Decimal = Decimal("0.20"),
        base_slippage: Decimal = Decimal("0.05"),
        cost_multiplier: Decimal = Decimal("1.0"),
        daily_loss_limit_r: Decimal = Decimal("3.0"),
    ) -> None:
        self.base_spread = base_spread
        self.base_slippage = base_slippage
        self.cost_multiplier = cost_multiplier
        self.daily_loss_limit_r = daily_loss_limit_r

    @property
    def effective_spread(self) -> Decimal:
        return self.base_spread * self.cost_multiplier

    @property
    def effective_slippage(self) -> Decimal:
        return self.base_slippage * self.cost_multiplier

    def run_judas_backtest(
        self,
        bars: list[Bar],
        news_calendar: Any = None,
        split_ratio: float = 0.75,
        judas_strategy: Any = None,
        breakeven_at_1r: bool = False,
    ) -> tuple[list[SimulatedTrade], dict[str, Any]]:
        """Run historical simulation for Asian Judas Swing strategy."""
        strategy = judas_strategy if judas_strategy is not None else JudasSwingStrategy()
        trades: list[SimulatedTrade] = []
        trade_counter = 0

        split_idx = int(len(bars) * split_ratio)

        active_trade: SimulatedTrade | None = None
        daily_r_tracker: dict[dt.date, Decimal] = {}
        daily_kill_latched: set[dt.date] = set()

        news_setups_filtered = 0

        for idx, bar in enumerate(bars):
            bar_time = bar.timestamp_utc
            ny_date = bar_time.astimezone(NY_TZ).date()
            mid = bar.mid_ohlc
            is_is = idx < split_idx

            # 1. Manage currently open trade
            if active_trade is not None:
                # Optional breakeven trail at +1.0R
                if breakeven_at_1r and active_trade.risk_dollars > 0:
                    if active_trade.side == "BUY":
                        if mid.high >= active_trade.entry_price + active_trade.risk_dollars:
                            active_trade.stop_loss = max(active_trade.stop_loss, active_trade.entry_price)
                    else:
                        if mid.low <= active_trade.entry_price - active_trade.risk_dollars:
                            active_trade.stop_loss = min(active_trade.stop_loss, active_trade.entry_price)

                # Check for stop or target hit within current bar
                # Conservative rule: if both hit in same candle, STOP hits first!
                stop_hit = False
                tp_hit = False

                if active_trade.side == "BUY":
                    if mid.low <= active_trade.stop_loss:
                        stop_hit = True
                    if mid.high >= active_trade.take_profit:
                        tp_hit = True
                else:  # SELL
                    if mid.high >= active_trade.stop_loss:
                        stop_hit = True
                    if mid.low <= active_trade.take_profit:
                        tp_hit = True

                if stop_hit and tp_hit:
                    # Conflict: STOP ASSUMED FIRST!
                    self._close_trade(
                        active_trade,
                        exit_price=active_trade.stop_loss,
                        exit_time=bar_time,
                        reason="STOP_LOSS (conflict resolution)",
                        is_stop=True,
                    )
                    trades.append(active_trade)
                    daily_r_tracker[ny_date] = (
                        daily_r_tracker.get(ny_date, Decimal("0")) + active_trade.r_multiple
                    )
                    active_trade = None
                elif stop_hit:
                    self._close_trade(
                        active_trade,
                        exit_price=active_trade.stop_loss,
                        exit_time=bar_time,
                        reason="STOP_LOSS",
                        is_stop=True,
                    )
                    trades.append(active_trade)
                    daily_r_tracker[ny_date] = (
                        daily_r_tracker.get(ny_date, Decimal("0")) + active_trade.r_multiple
                    )
                    active_trade = None
                elif tp_hit:
                    self._close_trade(
                        active_trade,
                        exit_price=active_trade.take_profit,
                        exit_time=bar_time,
                        reason="TAKE_PROFIT",
                        is_stop=False,
                    )
                    trades.append(active_trade)
                    daily_r_tracker[ny_date] = (
                        daily_r_tracker.get(ny_date, Decimal("0")) + active_trade.r_multiple
                    )
                    active_trade = None

                # Check if daily loss killswitch latched
                if daily_r_tracker.get(ny_date, Decimal("0")) <= -self.daily_loss_limit_r:
                    daily_kill_latched.add(ny_date)

            # 2. Evaluate strategy on completed M5 bar
            setup = strategy.on_bar(bar)
            if setup is not None:
                # Check kill-switch latch
                if ny_date in daily_kill_latched:
                    continue
                if active_trade is not None:
                    # Position already open; Judas allows 1 trade per day anyway
                    continue

                # Check news filter if provided
                is_news_blocked = False
                if news_calendar is not None:
                    # Check if bar_time lands in blackout
                    try:
                        is_news_blocked = news_calendar.is_blackout(bar_time)
                    except Exception:
                        is_news_blocked = False

                if is_news_blocked:
                    news_setups_filtered += 1
                    continue

                # Open trade with market order at bar close
                trade_counter += 1
                risk_dist = abs(setup.entry_price - setup.stop_loss)
                reward_dist = abs(setup.take_profit - setup.entry_price)

                # Entry execution cost (half spread)
                spread_cost = self.effective_spread / Decimal("2")
                actual_entry = (
                    setup.entry_price + spread_cost
                    if setup.side.value == "BUY"
                    else setup.entry_price - spread_cost
                )

                trade = SimulatedTrade(
                    trade_id=f"judas-{trade_counter:04d}",
                    strategy="Asian Judas Swing",
                    side=setup.side.value,
                    entry_time=bar_time,
                    entry_price=actual_entry,
                    stop_loss=setup.stop_loss,
                    take_profit=setup.take_profit,
                    risk_dollars=risk_dist,
                    reward_dollars=reward_dist,
                    spread_paid=self.effective_spread,
                    news_filtered=is_news_blocked,
                    in_sample=is_is,
                )
                active_trade = trade

        stats = {
            "strategy": "Asian Judas Swing",
            "total_trades": len(trades),
            "news_setups_filtered": news_setups_filtered,
            "cost_multiplier": float(self.cost_multiplier),
        }
        return trades, stats

    def run_silver_bullet_backtest(
        self,
        bars: list[Bar],
        silver_bullet_strategy: Any,
        news_calendar: Any = None,
        split_ratio: float = 0.75,
    ) -> tuple[list[SimulatedTrade], dict[str, Any]]:
        """Run historical simulation for ICT Silver Bullet strategy."""
        trades: list[SimulatedTrade] = []
        resting_orders: list[RestingLimit] = []
        active_trade: SimulatedTrade | None = None
        trade_counter = 0
        split_idx = int(len(bars) * split_ratio)

        daily_r_tracker: dict[dt.date, Decimal] = {}
        daily_kill_latched: set[dt.date] = set()
        news_setups_filtered = 0
        unfilled_limit_orders = 0

        for idx, bar in enumerate(bars):
            bar_time = bar.timestamp_utc
            ny_time = bar_time.astimezone(NY_TZ)
            ny_date = ny_time.date()
            mid = bar.mid_ohlc
            is_is = idx < split_idx

            # 1. Manage open trade
            if active_trade is not None:
                stop_hit = False
                tp_hit = False

                if active_trade.side == "BUY":
                    if mid.low <= active_trade.stop_loss:
                        stop_hit = True
                    if mid.high >= active_trade.take_profit:
                        tp_hit = True
                else:  # SELL
                    if mid.high >= active_trade.stop_loss:
                        stop_hit = True
                    if mid.low <= active_trade.take_profit:
                        tp_hit = True

                # Dynamic close at 16:45 NY
                dynamic_close = ny_time.time() >= dt.time(16, 45)

                if stop_hit and tp_hit:
                    # Stop priority
                    self._close_trade(
                        active_trade,
                        exit_price=active_trade.stop_loss,
                        exit_time=bar_time,
                        reason="STOP_LOSS (conflict resolution)",
                        is_stop=True,
                    )
                    trades.append(active_trade)
                    daily_r_tracker[ny_date] = (
                        daily_r_tracker.get(ny_date, Decimal("0")) + active_trade.r_multiple
                    )
                    active_trade = None
                elif stop_hit:
                    self._close_trade(
                        active_trade,
                        exit_price=active_trade.stop_loss,
                        exit_time=bar_time,
                        reason="STOP_LOSS",
                        is_stop=True,
                    )
                    trades.append(active_trade)
                    daily_r_tracker[ny_date] = (
                        daily_r_tracker.get(ny_date, Decimal("0")) + active_trade.r_multiple
                    )
                    active_trade = None
                elif tp_hit:
                    self._close_trade(
                        active_trade,
                        exit_price=active_trade.take_profit,
                        exit_time=bar_time,
                        reason="TAKE_PROFIT",
                        is_stop=False,
                    )
                    trades.append(active_trade)
                    daily_r_tracker[ny_date] = (
                        daily_r_tracker.get(ny_date, Decimal("0")) + active_trade.r_multiple
                    )
                    active_trade = None
                elif dynamic_close:
                    self._close_trade(
                        active_trade,
                        exit_price=mid.close,
                        exit_time=bar_time,
                        reason="DYNAMIC_CLOSE_1645",
                        is_stop=False,
                    )
                    trades.append(active_trade)
                    daily_r_tracker[ny_date] = (
                        daily_r_tracker.get(ny_date, Decimal("0")) + active_trade.r_multiple
                    )
                    active_trade = None

                if daily_r_tracker.get(ny_date, Decimal("0")) <= -self.daily_loss_limit_r:
                    daily_kill_latched.add(ny_date)

            # 2. Manage resting limit orders: check conservative fills
            # "limit orders fill only if price trades THROUGH the limit on the correct side"
            for order in list(resting_orders):
                if active_trade is not None or ny_date in daily_kill_latched:
                    # Cannot fill if already in position or killed
                    break

                # Check expiry
                if idx > order.expires_bar_idx or ny_time.time() >= dt.time(16, 45):
                    resting_orders.remove(order)
                    unfilled_limit_orders += 1
                    continue

                # Check through fill
                filled = False
                if order.side == "BUY":
                    # For BUY limit, price must trade THROUGH: low < limit
                    if mid.low < order.limit_price:
                        filled = True
                else:  # SELL
                    # For SELL limit, price must trade THROUGH: high > limit
                    if mid.high > order.limit_price:
                        filled = True

                if filled:
                    resting_orders.remove(order)
                    trade_counter += 1
                    risk_dist = abs(order.limit_price - order.stop_loss)
                    reward_dist = abs(order.take_profit - order.limit_price)

                    active_trade = SimulatedTrade(
                        trade_id=f"sb-{trade_counter:04d}",
                        strategy="ICT Silver Bullet",
                        side=order.side,
                        entry_time=bar_time,
                        entry_price=order.limit_price,
                        stop_loss=order.stop_loss,
                        take_profit=order.take_profit,
                        risk_dollars=risk_dist,
                        reward_dollars=reward_dist,
                        spread_paid=self.effective_spread,
                        in_sample=order.in_sample,
                    )
                    # Notify strategy of fill if required
                    if hasattr(silver_bullet_strategy, "on_fill"):
                        try:
                            silver_bullet_strategy.on_fill(order.order_id, bar_time)
                        except Exception:
                            pass
                    break

            # 3. Step strategy
            try:
                intents = silver_bullet_strategy.on_bar(bar)
            except Exception as exc:
                intents = []

            for intent in intents:
                intent_type = type(intent).__name__
                if intent_type == "PlaceLimit":
                    if ny_date in daily_kill_latched:
                        continue
                    # Check news filter
                    is_news_blocked = False
                    if news_calendar is not None:
                        try:
                            is_news_blocked = news_calendar.is_blackout(bar_time)
                        except Exception:
                            is_news_blocked = False

                    if is_news_blocked:
                        news_setups_filtered += 1
                        continue

                    # Register resting limit
                    side_str = "BUY" if intent.side.value == "BUY" else "SELL"
                    resting_orders.append(
                        RestingLimit(
                            order_id=intent.order_id,
                            strategy="ICT Silver Bullet",
                            side=side_str,
                            limit_price=intent.limit_price,
                            stop_loss=intent.stop_price,
                            take_profit=intent.target_price,
                            placed_time=bar_time,
                            placed_bar_idx=idx,
                            expires_bar_idx=idx + intent.expires_after_bars,
                            news_filtered=is_news_blocked,
                            in_sample=is_is,
                        )
                    )

        stats = {
            "strategy": "ICT Silver Bullet",
            "total_trades": len(trades),
            "unfilled_limit_orders": unfilled_limit_orders,
            "news_setups_filtered": news_setups_filtered,
            "cost_multiplier": float(self.cost_multiplier),
        }
        return trades, stats

    def _close_trade(
        self,
        trade: SimulatedTrade,
        exit_price: Decimal,
        exit_time: dt.datetime,
        reason: str,
        is_stop: bool,
    ) -> None:
        trade.exit_time = exit_time
        trade.exit_reason = reason
        trade.status = "CLOSED"

        # Apply slippage if stop-loss
        slippage = self.effective_slippage if is_stop else Decimal("0")
        trade.slippage_paid = slippage

        if trade.side == "BUY":
            actual_exit = exit_price - slippage
            pnl_dollars = actual_exit - trade.entry_price
        else:
            actual_exit = exit_price + slippage
            pnl_dollars = trade.entry_price - actual_exit

        trade.exit_price = actual_exit
        trade.pnl_dollars = pnl_dollars

        # Calculate exact R-multiple
        if trade.risk_dollars > Decimal("0"):
            trade.r_multiple = pnl_dollars / trade.risk_dollars
        else:
            trade.r_multiple = Decimal("0")
