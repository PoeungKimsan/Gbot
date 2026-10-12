"""Data models for trend backtesting."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Literal


@dataclass(slots=True)
class DailyBar:
    date: dt.date
    open: float
    high: float
    low: float
    close: float
    volume: int
    atr20: float = 0.0
    sma50: float = 0.0
    sma200: float = 0.0
    donchian_high55: float = 0.0
    donchian_low55: float = 0.0
    donchian_high20: float = 0.0
    donchian_low20: float = 0.0
    return_252: float = 0.0
    is_month_end: bool = False


@dataclass(slots=True)
class TrendTrade:
    trade_id: str
    rule: Literal["T1_DONCHIAN", "T2_SMA_CROSS", "T3_TSMOM"]
    symbol: str
    direction: Literal["LONG", "SHORT"]
    entry_date: dt.date
    entry_price: float
    stop_loss: float
    stop_distance: float
    exit_date: dt.date
    exit_price: float
    exit_reason: str
    holding_days: int
    gross_pnl_pts: float
    gross_r: float
    spread_cost_pts: float
    slippage_cost_pts: float
    financing_cost_pts: float
    drag_spread_r: float
    drag_slippage_r: float
    drag_financing_r: float
    net_r: float
    net_r_fin0: float
    net_r_fin4: float
    net_r_spread2x: float
    is_stop: bool
