"""Implementation of the 3 pre-registered trend strategies (trend_v1.1)."""

from __future__ import annotations

import datetime as dt
from research.trend.costs import compute_costs
from research.trend.models import DailyBar, TrendTrade


def run_t1_donchian(
    symbol: str,
    bars: list[DailyBar],
    start_date: dt.date | None = None,
    end_date: dt.date = dt.date(2025, 12, 31),
) -> list[TrendTrade]:
    """T1 Donchian: 55-day breakout, 20-day channel exit, 2x ATR stop, with channel reset gate."""
    if start_date is None:
        start_date = bars[252].date if len(bars) > 252 else bars[0].date

    trades: list[TrendTrade] = []
    in_pos = False
    direction = ""
    entry_date: dt.date = dt.date.min
    entry_price = 0.0
    stop_loss = 0.0
    stop_distance = 0.0
    pending_signal: str | None = None  # Signal from bar i to enter at bar i+1 Open
    trade_count = 0
    channel_reset = True  # True initially; set False on exit until price closes inside 55d channel

    n = len(bars)
    for i in range(n):
        bar = bars[i]

        # 1. Execute pending entry from bar i-1
        if pending_signal and not in_pos and bar.date >= start_date and bar.date <= end_date:
            direction = pending_signal
            entry_price = bar.open
            atr = bars[i - 1].atr20
            stop_distance = 2.0 * atr
            if stop_distance <= 0:
                pending_signal = None
                continue

            if direction == "LONG":
                stop_loss = entry_price - stop_distance
            else:
                stop_loss = entry_price + stop_distance

            in_pos = True
            entry_date = bar.date
            pending_signal = None

        pending_signal = None

        # 2. If in position, evaluate stop first on current bar
        if in_pos:
            is_stopped = False
            exit_price = 0.0
            exit_reason = ""

            if direction == "LONG":
                if bar.low <= stop_loss:
                    is_stopped = True
                    exit_price = bar.open if bar.open <= stop_loss else stop_loss
                    exit_reason = "STOP"
            elif direction == "SHORT":
                if bar.high >= stop_loss:
                    is_stopped = True
                    exit_price = bar.open if bar.open >= stop_loss else stop_loss
                    exit_reason = "STOP"

            if is_stopped:
                in_pos = False
                channel_reset = False  # Reset required: must close inside channel before new trade
                costs = compute_costs(
                    symbol=symbol,
                    direction=direction,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    stop_distance=stop_distance,
                    is_stop=True,
                    entry_date=entry_date,
                    exit_date=bar.date,
                )
                trade_count += 1
                trades.append(
                    TrendTrade(
                        trade_id=f"T1_{symbol}_{trade_count:04d}",
                        rule="T1_DONCHIAN",
                        symbol=symbol,
                        direction=direction,  # type: ignore
                        entry_date=entry_date,
                        entry_price=entry_price,
                        stop_loss=stop_loss,
                        stop_distance=stop_distance,
                        exit_date=bar.date,
                        exit_price=exit_price,
                        exit_reason=exit_reason,
                        holding_days=costs["holding_days"],
                        gross_pnl_pts=costs["gross_pnl_pts"],
                        gross_r=costs["gross_r"],
                        spread_cost_pts=costs["spread_cost_pts"],
                        slippage_cost_pts=costs["slippage_cost_pts"],
                        financing_cost_pts=costs["financing_cost_pts"],
                        drag_spread_r=costs["drag_spread_r"],
                        drag_slippage_r=costs["drag_slippage_r"],
                        drag_financing_r=costs["drag_financing_r"],
                        net_r=costs["net_r"],
                        net_r_fin0=costs["net_r_fin0"],
                        net_r_fin4=costs["net_r_fin4"],
                        net_r_spread2x=costs["net_r_spread2x"],
                        is_stop=True,
                    )
                )
                # Check if this same bar closed back inside channel
                if bar.donchian_low55 <= bar.close <= bar.donchian_high55:
                    channel_reset = True
                continue

            # Check 20-day opposite channel exit on bar close (exit occurs at next bar's Open)
            if direction == "LONG":
                if bar.close < bar.donchian_low20:
                    in_pos = False
                    channel_reset = False
                    if i + 1 < n and bars[i + 1].date <= end_date:
                        next_bar = bars[i + 1]
                        costs = compute_costs(
                            symbol=symbol,
                            direction=direction,
                            entry_price=entry_price,
                            exit_price=next_bar.open,
                            stop_distance=stop_distance,
                            is_stop=False,
                            entry_date=entry_date,
                            exit_date=next_bar.date,
                        )
                        trade_count += 1
                        trades.append(
                            TrendTrade(
                                trade_id=f"T1_{symbol}_{trade_count:04d}",
                                rule="T1_DONCHIAN",
                                symbol=symbol,
                                direction=direction,  # type: ignore
                                entry_date=entry_date,
                                entry_price=entry_price,
                                stop_loss=stop_loss,
                                stop_distance=stop_distance,
                                exit_date=next_bar.date,
                                exit_price=next_bar.open,
                                exit_reason="CHANNEL_EXIT",
                                holding_days=costs["holding_days"],
                                gross_pnl_pts=costs["gross_pnl_pts"],
                                gross_r=costs["gross_r"],
                                spread_cost_pts=costs["spread_cost_pts"],
                                slippage_cost_pts=costs["slippage_cost_pts"],
                                financing_cost_pts=costs["financing_cost_pts"],
                                drag_spread_r=costs["drag_spread_r"],
                                drag_slippage_r=costs["drag_slippage_r"],
                                drag_financing_r=costs["drag_financing_r"],
                                net_r=costs["net_r"],
                                net_r_fin0=costs["net_r_fin0"],
                                net_r_fin4=costs["net_r_fin4"],
                                net_r_spread2x=costs["net_r_spread2x"],
                                is_stop=False,
                            )
                        )
                    else:
                        costs = compute_costs(
                            symbol=symbol,
                            direction=direction,
                            entry_price=entry_price,
                            exit_price=bar.close,
                            stop_distance=stop_distance,
                            is_stop=False,
                            entry_date=entry_date,
                            exit_date=bar.date,
                        )
                        trade_count += 1
                        trades.append(
                            TrendTrade(
                                trade_id=f"T1_{symbol}_{trade_count:04d}",
                                rule="T1_DONCHIAN",
                                symbol=symbol,
                                direction=direction,  # type: ignore
                                entry_date=entry_date,
                                entry_price=entry_price,
                                stop_loss=stop_loss,
                                stop_distance=stop_distance,
                                exit_date=bar.date,
                                exit_price=bar.close,
                                exit_reason="PERIOD_END",
                                holding_days=costs["holding_days"],
                                gross_pnl_pts=costs["gross_pnl_pts"],
                                gross_r=costs["gross_r"],
                                spread_cost_pts=costs["spread_cost_pts"],
                                slippage_cost_pts=costs["slippage_cost_pts"],
                                financing_cost_pts=costs["financing_cost_pts"],
                                drag_spread_r=costs["drag_spread_r"],
                                drag_slippage_r=costs["drag_slippage_r"],
                                drag_financing_r=costs["drag_financing_r"],
                                net_r=costs["net_r"],
                                net_r_fin0=costs["net_r_fin0"],
                                net_r_fin4=costs["net_r_fin4"],
                                net_r_spread2x=costs["net_r_spread2x"],
                                is_stop=False,
                            )
                        )
                    if bar.donchian_low55 <= bar.close <= bar.donchian_high55:
                        channel_reset = True
                    continue

            elif direction == "SHORT":
                if bar.close > bar.donchian_high20:
                    in_pos = False
                    channel_reset = False
                    if i + 1 < n and bars[i + 1].date <= end_date:
                        next_bar = bars[i + 1]
                        costs = compute_costs(
                            symbol=symbol,
                            direction=direction,
                            entry_price=entry_price,
                            exit_price=next_bar.open,
                            stop_distance=stop_distance,
                            is_stop=False,
                            entry_date=entry_date,
                            exit_date=next_bar.date,
                        )
                        trade_count += 1
                        trades.append(
                            TrendTrade(
                                trade_id=f"T1_{symbol}_{trade_count:04d}",
                                rule="T1_DONCHIAN",
                                symbol=symbol,
                                direction=direction,  # type: ignore
                                entry_date=entry_date,
                                entry_price=entry_price,
                                stop_loss=stop_loss,
                                stop_distance=stop_distance,
                                exit_date=next_bar.date,
                                exit_price=next_bar.open,
                                exit_reason="CHANNEL_EXIT",
                                holding_days=costs["holding_days"],
                                gross_pnl_pts=costs["gross_pnl_pts"],
                                gross_r=costs["gross_r"],
                                spread_cost_pts=costs["spread_cost_pts"],
                                slippage_cost_pts=costs["slippage_cost_pts"],
                                financing_cost_pts=costs["financing_cost_pts"],
                                drag_spread_r=costs["drag_spread_r"],
                                drag_slippage_r=costs["drag_slippage_r"],
                                drag_financing_r=costs["drag_financing_r"],
                                net_r=costs["net_r"],
                                net_r_fin0=costs["net_r_fin0"],
                                net_r_fin4=costs["net_r_fin4"],
                                net_r_spread2x=costs["net_r_spread2x"],
                                is_stop=False,
                            )
                        )
                    else:
                        costs = compute_costs(
                            symbol=symbol,
                            direction=direction,
                            entry_price=entry_price,
                            exit_price=bar.close,
                            stop_distance=stop_distance,
                            is_stop=False,
                            entry_date=entry_date,
                            exit_date=bar.date,
                        )
                        trade_count += 1
                        trades.append(
                            TrendTrade(
                                trade_id=f"T1_{symbol}_{trade_count:04d}",
                                rule="T1_DONCHIAN",
                                symbol=symbol,
                                direction=direction,  # type: ignore
                                entry_date=entry_date,
                                entry_price=entry_price,
                                stop_loss=stop_loss,
                                stop_distance=stop_distance,
                                exit_date=bar.date,
                                exit_price=bar.close,
                                exit_reason="PERIOD_END",
                                holding_days=costs["holding_days"],
                                gross_pnl_pts=costs["gross_pnl_pts"],
                                gross_r=costs["gross_r"],
                                spread_cost_pts=costs["spread_cost_pts"],
                                slippage_cost_pts=costs["slippage_cost_pts"],
                                financing_cost_pts=costs["financing_cost_pts"],
                                drag_spread_r=costs["drag_spread_r"],
                                drag_slippage_r=costs["drag_slippage_r"],
                                drag_financing_r=costs["drag_financing_r"],
                                net_r=costs["net_r"],
                                net_r_fin0=costs["net_r_fin0"],
                                net_r_fin4=costs["net_r_fin4"],
                                net_r_spread2x=costs["net_r_spread2x"],
                                is_stop=False,
                            )
                        )
                    if bar.donchian_low55 <= bar.close <= bar.donchian_high55:
                        channel_reset = True
                    continue

        # 3. If flat, check channel reset and 55-day breakout signal
        if not in_pos and i >= 55:
            if not channel_reset:
                if bar.donchian_low55 <= bar.close <= bar.donchian_high55:
                    channel_reset = True

            if channel_reset:
                if bar.close > bar.donchian_high55:
                    pending_signal = "LONG"
                elif bar.close < bar.donchian_low55:
                    pending_signal = "SHORT"

    # Close open trade if at end
    if in_pos:
        costs = compute_costs(
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            exit_price=bars[-1].close,
            stop_distance=stop_distance,
            is_stop=False,
            entry_date=entry_date,
            exit_date=bars[-1].date,
        )
        trade_count += 1
        trades.append(
            TrendTrade(
                trade_id=f"T1_{symbol}_{trade_count:04d}",
                rule="T1_DONCHIAN",
                symbol=symbol,
                direction=direction,  # type: ignore
                entry_date=entry_date,
                entry_price=entry_price,
                stop_loss=stop_loss,
                stop_distance=stop_distance,
                exit_date=bars[-1].date,
                exit_price=bars[-1].close,
                exit_reason="PERIOD_END",
                holding_days=costs["holding_days"],
                gross_pnl_pts=costs["gross_pnl_pts"],
                gross_r=costs["gross_r"],
                spread_cost_pts=costs["spread_cost_pts"],
                slippage_cost_pts=costs["slippage_cost_pts"],
                financing_cost_pts=costs["financing_cost_pts"],
                drag_spread_r=costs["drag_spread_r"],
                drag_slippage_r=costs["drag_slippage_r"],
                drag_financing_r=costs["drag_financing_r"],
                net_r=costs["net_r"],
                net_r_fin0=costs["net_r_fin0"],
                net_r_fin4=costs["net_r_fin4"],
                net_r_spread2x=costs["net_r_spread2x"],
                is_stop=False,
            )
        )

    return trades


def run_t2_sma_cross(
    symbol: str,
    bars: list[DailyBar],
    start_date: dt.date | None = None,
    end_date: dt.date = dt.date(2025, 12, 31),
) -> list[TrendTrade]:
    """T2 SMA Cross: 50/200 cross, 3x ATR initial stop, wait for fresh cross after stop."""
    if start_date is None:
        start_date = bars[252].date if len(bars) > 252 else bars[0].date

    trades: list[TrendTrade] = []
    in_pos = False
    direction = ""
    entry_date: dt.date = dt.date.min
    entry_price = 0.0
    stop_loss = 0.0
    stop_distance = 0.0
    stopped_out_regime: str | None = None  # Prevents re-entry until next fresh cross
    trade_count = 0

    n = len(bars)
    for i in range(1, n):
        bar = bars[i]
        prev_bar = bars[i - 1]

        # Evaluate cross that occurred at bar i-1 (using bars i-2 and i-1)
        if i >= 201:
            prev2 = bars[i - 2]
            bullish_cross = prev2.sma50 <= prev2.sma200 and prev_bar.sma50 > prev_bar.sma200
            bearish_cross = prev2.sma50 >= prev2.sma200 and prev_bar.sma50 < prev_bar.sma200
        else:
            bullish_cross = False
            bearish_cross = False

        # Reset stopped_out_regime upon fresh cross
        if bullish_cross and stopped_out_regime == "BULL":
            stopped_out_regime = None
        if bearish_cross and stopped_out_regime == "BEAR":
            stopped_out_regime = None

        # 1. Check stop first on current bar if in position
        if in_pos:
            is_stopped = False
            exit_price = 0.0

            if direction == "LONG":
                if bar.low <= stop_loss:
                    is_stopped = True
                    exit_price = bar.open if bar.open <= stop_loss else stop_loss
                    stopped_out_regime = "BULL"
            elif direction == "SHORT":
                if bar.high >= stop_loss:
                    is_stopped = True
                    exit_price = bar.open if bar.open >= stop_loss else stop_loss
                    stopped_out_regime = "BEAR"

            if is_stopped:
                in_pos = False
                costs = compute_costs(
                    symbol=symbol,
                    direction=direction,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    stop_distance=stop_distance,
                    is_stop=True,
                    entry_date=entry_date,
                    exit_date=bar.date,
                )
                trade_count += 1
                trades.append(
                    TrendTrade(
                        trade_id=f"T2_{symbol}_{trade_count:04d}",
                        rule="T2_SMA_CROSS",
                        symbol=symbol,
                        direction=direction,  # type: ignore
                        entry_date=entry_date,
                        entry_price=entry_price,
                        stop_loss=stop_loss,
                        stop_distance=stop_distance,
                        exit_date=bar.date,
                        exit_price=exit_price,
                        exit_reason="STOP",
                        holding_days=costs["holding_days"],
                        gross_pnl_pts=costs["gross_pnl_pts"],
                        gross_r=costs["gross_r"],
                        spread_cost_pts=costs["spread_cost_pts"],
                        slippage_cost_pts=costs["slippage_cost_pts"],
                        financing_cost_pts=costs["financing_cost_pts"],
                        drag_spread_r=costs["drag_spread_r"],
                        drag_slippage_r=costs["drag_slippage_r"],
                        drag_financing_r=costs["drag_financing_r"],
                        net_r=costs["net_r"],
                        net_r_fin0=costs["net_r_fin0"],
                        net_r_fin4=costs["net_r_fin4"],
                        net_r_spread2x=costs["net_r_spread2x"],
                        is_stop=True,
                    )
                )
                continue

            # Check opposite cross exit on bar.open
            opposite_cross = (direction == "LONG" and bearish_cross) or (direction == "SHORT" and bullish_cross)
            if opposite_cross:
                in_pos = False
                costs = compute_costs(
                    symbol=symbol,
                    direction=direction,
                    entry_price=entry_price,
                    exit_price=bar.open,
                    stop_distance=stop_distance,
                    is_stop=False,
                    entry_date=entry_date,
                    exit_date=bar.date,
                )
                trade_count += 1
                trades.append(
                    TrendTrade(
                        trade_id=f"T2_{symbol}_{trade_count:04d}",
                        rule="T2_SMA_CROSS",
                        symbol=symbol,
                        direction=direction,  # type: ignore
                        entry_date=entry_date,
                        entry_price=entry_price,
                        stop_loss=stop_loss,
                        stop_distance=stop_distance,
                        exit_date=bar.date,
                        exit_price=bar.open,
                        exit_reason="OPPOSITE_CROSS",
                        holding_days=costs["holding_days"],
                        gross_pnl_pts=costs["gross_pnl_pts"],
                        gross_r=costs["gross_r"],
                        spread_cost_pts=costs["spread_cost_pts"],
                        slippage_cost_pts=costs["slippage_cost_pts"],
                        financing_cost_pts=costs["financing_cost_pts"],
                        drag_spread_r=costs["drag_spread_r"],
                        drag_slippage_r=costs["drag_slippage_r"],
                        drag_financing_r=costs["drag_financing_r"],
                        net_r=costs["net_r"],
                        net_r_fin0=costs["net_r_fin0"],
                        net_r_fin4=costs["net_r_fin4"],
                        net_r_spread2x=costs["net_r_spread2x"],
                        is_stop=False,
                    )
                )

        # 2. If flat, check entry on fresh cross
        if not in_pos and bar.date >= start_date and bar.date <= end_date:
            atr = prev_bar.atr20
            stop_dist = 3.0 * atr

            if bullish_cross and stopped_out_regime != "BULL" and stop_dist > 0:
                direction = "LONG"
                entry_price = bar.open
                stop_distance = stop_dist
                stop_loss = entry_price - stop_distance
                entry_date = bar.date
                in_pos = True
                stopped_out_regime = None
            elif bearish_cross and stopped_out_regime != "BEAR" and stop_dist > 0:
                direction = "SHORT"
                entry_price = bar.open
                stop_distance = stop_dist
                stop_loss = entry_price + stop_distance
                entry_date = bar.date
                in_pos = True
                stopped_out_regime = None

    # Close open trade if at end
    if in_pos:
        costs = compute_costs(
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            exit_price=bars[-1].close,
            stop_distance=stop_distance,
            is_stop=False,
            entry_date=entry_date,
            exit_date=bars[-1].date,
        )
        trade_count += 1
        trades.append(
            TrendTrade(
                trade_id=f"T2_{symbol}_{trade_count:04d}",
                rule="T2_SMA_CROSS",
                symbol=symbol,
                direction=direction,  # type: ignore
                entry_date=entry_date,
                entry_price=entry_price,
                stop_loss=stop_loss,
                stop_distance=stop_distance,
                exit_date=bars[-1].date,
                exit_price=bars[-1].close,
                exit_reason="PERIOD_END",
                holding_days=costs["holding_days"],
                gross_pnl_pts=costs["gross_pnl_pts"],
                gross_r=costs["gross_r"],
                spread_cost_pts=costs["spread_cost_pts"],
                slippage_cost_pts=costs["slippage_cost_pts"],
                financing_cost_pts=costs["financing_cost_pts"],
                drag_spread_r=costs["drag_spread_r"],
                drag_slippage_r=costs["drag_slippage_r"],
                drag_financing_r=costs["drag_financing_r"],
                net_r=costs["net_r"],
                net_r_fin0=costs["net_r_fin0"],
                net_r_fin4=costs["net_r_fin4"],
                net_r_spread2x=costs["net_r_spread2x"],
                is_stop=False,
            )
        )

    return trades


def run_t3_tsmom(
    symbol: str,
    bars: list[DailyBar],
    start_date: dt.date | None = None,
    end_date: dt.date = dt.date(2025, 12, 31),
) -> list[TrendTrade]:
    """T3 TSMOM: Month-end 252-day return sign, hold while sign persists (1 trade/episode), 3x ATR stop."""
    if start_date is None:
        start_date = bars[252].date if len(bars) > 252 else bars[0].date

    trades: list[TrendTrade] = []
    in_pos = False
    direction = ""
    entry_date: dt.date = dt.date.min
    entry_price = 0.0
    stop_loss = 0.0
    stop_distance = 0.0
    current_episode_sign: int = 0  # +1 for positive return, -1 for negative return
    trade_count = 0

    n = len(bars)
    for i in range(1, n):
        bar = bars[i]
        prev_bar = bars[i - 1]

        # 1. If in position, check stop first on current bar
        if in_pos:
            is_stopped = False
            exit_price = 0.0

            if direction == "LONG":
                if bar.low <= stop_loss:
                    is_stopped = True
                    exit_price = bar.open if bar.open <= stop_loss else stop_loss
            elif direction == "SHORT":
                if bar.high >= stop_loss:
                    is_stopped = True
                    exit_price = bar.open if bar.open >= stop_loss else stop_loss

            if is_stopped:
                in_pos = False
                costs = compute_costs(
                    symbol=symbol,
                    direction=direction,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    stop_distance=stop_distance,
                    is_stop=True,
                    entry_date=entry_date,
                    exit_date=bar.date,
                )
                trade_count += 1
                trades.append(
                    TrendTrade(
                        trade_id=f"T3_{symbol}_{trade_count:04d}",
                        rule="T3_TSMOM",
                        symbol=symbol,
                        direction=direction,  # type: ignore
                        entry_date=entry_date,
                        entry_price=entry_price,
                        stop_loss=stop_loss,
                        stop_distance=stop_distance,
                        exit_date=bar.date,
                        exit_price=exit_price,
                        exit_reason="STOP",
                        holding_days=costs["holding_days"],
                        gross_pnl_pts=costs["gross_pnl_pts"],
                        gross_r=costs["gross_r"],
                        spread_cost_pts=costs["spread_cost_pts"],
                        slippage_cost_pts=costs["slippage_cost_pts"],
                        financing_cost_pts=costs["financing_cost_pts"],
                        drag_spread_r=costs["drag_spread_r"],
                        drag_slippage_r=costs["drag_slippage_r"],
                        drag_financing_r=costs["drag_financing_r"],
                        net_r=costs["net_r"],
                        net_r_fin0=costs["net_r_fin0"],
                        net_r_fin4=costs["net_r_fin4"],
                        net_r_spread2x=costs["net_r_spread2x"],
                        is_stop=True,
                    )
                )
                continue

        # 2. Check if prev_bar was month-end (evaluating return sign)
        if prev_bar.is_month_end and i >= 253:
            ret = prev_bar.return_252
            new_sign = 1 if ret > 0 else (-1 if ret < 0 else 0)

            if new_sign != 0:
                # If sign flipped
                if new_sign != current_episode_sign:
                    # If in position, exit old trade at bar.open
                    if in_pos:
                        in_pos = False
                        costs = compute_costs(
                            symbol=symbol,
                            direction=direction,
                            entry_price=entry_price,
                            exit_price=bar.open,
                            stop_distance=stop_distance,
                            is_stop=False,
                            entry_date=entry_date,
                            exit_date=bar.date,
                        )
                        trade_count += 1
                        trades.append(
                            TrendTrade(
                                trade_id=f"T3_{symbol}_{trade_count:04d}",
                                rule="T3_TSMOM",
                                symbol=symbol,
                                direction=direction,  # type: ignore
                                entry_date=entry_date,
                                entry_price=entry_price,
                                stop_loss=stop_loss,
                                stop_distance=stop_distance,
                                exit_date=bar.date,
                                exit_price=bar.open,
                                exit_reason="SIGN_FLIP",
                                holding_days=costs["holding_days"],
                                gross_pnl_pts=costs["gross_pnl_pts"],
                                gross_r=costs["gross_r"],
                                spread_cost_pts=costs["spread_cost_pts"],
                                slippage_cost_pts=costs["slippage_cost_pts"],
                                financing_cost_pts=costs["financing_cost_pts"],
                                drag_spread_r=costs["drag_spread_r"],
                                drag_slippage_r=costs["drag_slippage_r"],
                                drag_financing_r=costs["drag_financing_r"],
                                net_r=costs["net_r"],
                                net_r_fin0=costs["net_r_fin0"],
                                net_r_fin4=costs["net_r_fin4"],
                                net_r_spread2x=costs["net_r_spread2x"],
                                is_stop=False,
                            )
                        )

                    # Start new episode!
                    current_episode_sign = new_sign

                    # Enter new trade if within evaluation window
                    if bar.date >= start_date and bar.date <= end_date:
                        direction = "LONG" if new_sign == 1 else "SHORT"
                        entry_price = bar.open
                        stop_dist = 3.0 * prev_bar.atr20
                        if stop_dist > 0:
                            stop_distance = stop_dist
                            if direction == "LONG":
                                stop_loss = entry_price - stop_distance
                            else:
                                stop_loss = entry_price + stop_distance
                            entry_date = bar.date
                            in_pos = True

    # Close open trade if at end
    if in_pos:
        costs = compute_costs(
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            exit_price=bars[-1].close,
            stop_distance=stop_distance,
            is_stop=False,
            entry_date=entry_date,
            exit_date=bars[-1].date,
        )
        trade_count += 1
        trades.append(
            TrendTrade(
                trade_id=f"T3_{symbol}_{trade_count:04d}",
                rule="T3_TSMOM",
                symbol=symbol,
                direction=direction,  # type: ignore
                entry_date=entry_date,
                entry_price=entry_price,
                stop_loss=stop_loss,
                stop_distance=stop_distance,
                exit_date=bars[-1].date,
                exit_price=bars[-1].close,
                exit_reason="PERIOD_END",
                holding_days=costs["holding_days"],
                gross_pnl_pts=costs["gross_pnl_pts"],
                gross_r=costs["gross_r"],
                spread_cost_pts=costs["spread_cost_pts"],
                slippage_cost_pts=costs["slippage_cost_pts"],
                financing_cost_pts=costs["financing_cost_pts"],
                drag_spread_r=costs["drag_spread_r"],
                drag_slippage_r=costs["drag_slippage_r"],
                drag_financing_r=costs["drag_financing_r"],
                net_r=costs["net_r"],
                net_r_fin0=costs["net_r_fin0"],
                net_r_fin4=costs["net_r_fin4"],
                net_r_spread2x=costs["net_r_spread2x"],
                is_stop=False,
            )
        )

    return trades
