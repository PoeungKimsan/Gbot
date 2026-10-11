"""ICT Asian Session Range Expansion (Judas Swing) strategy.

Core logic:
1. Measures the Asian Session Range High and Low (18:00 - 00:00 America/New_York).
2. During London Open (02:00 - 05:00 America/New_York), monitors for an aggressive
   liquidity sweep of either the Asian High or Asian Low.
3. When the price sweeps the boundary and closes back inside the Asian range (the "Judas
   Swing" fakeout), generates an entry signal targeting the opposite side of the range.
4. Strictly zero-float: all calculations are exact Decimals.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from engine.market.models import Bar

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)

NY_TZ = ZoneInfo("America/New_York")

__all__ = [
    "AsianRange",
    "JudasSetup",
    "JudasSide",
    "JudasSwingStrategy",
]


class JudasSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True, slots=True)
class AsianRange:
    date: dt.date
    high: Decimal
    low: Decimal
    complete: bool


@dataclass(frozen=True, slots=True)
class JudasSetup:
    side: JudasSide
    bar_time: dt.datetime
    entry_price: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    asian_high: Decimal
    asian_low: Decimal
    sweep_extreme: Decimal
    reason: str


class JudasSwingStrategy:
    """Detects and trades ICT Judas Swing setups around London Open."""

    def __init__(
        self,
        min_sweep_ticks: int = 5,
        buffer_ticks: int = 10,
        tick_size: Decimal = Decimal("0.01"),
        min_risk_reward: Decimal = Decimal("2.0"),
    ) -> None:
        self.min_sweep_ticks = min_sweep_ticks
        self.buffer_ticks = buffer_ticks
        self.tick_size = tick_size
        self.min_risk_reward = min_risk_reward

        # State tracking
        self._current_asian_date: dt.date | None = None
        self._temp_asian_high: Decimal | None = None
        self._temp_asian_low: Decimal | None = None
        self._locked_range: AsianRange | None = None

        self._swept_high_extreme: Decimal | None = None
        self._swept_low_extreme: Decimal | None = None
        self._traded_dates: set[dt.date] = set()

    @property
    def current_asian_range(self) -> AsianRange | None:
        return self._locked_range

    def on_bar(self, bar: Bar) -> JudasSetup | None:
        """Process one closed M5 bar and evaluate Judas Swing setups."""
        time_ny = bar.timestamp_utc.astimezone(NY_TZ)
        hour = time_ny.hour
        bar_date = time_ny.date()

        # Mid OHLC values
        mid = bar.mid_ohlc
        high = mid.high
        low = mid.low
        close = mid.close

        # ------------------------------------------------------------------- #
        # 1. Asian Session Measurement (18:00 - 00:00 NY)
        # ------------------------------------------------------------------- #
        # In NY time, 18:00 to 23:59 belongs to the new trading day starting
        if hour >= 18 or hour < 0:
            trading_day = bar_date if hour < 18 else bar_date + dt.timedelta(days=1)
            if self._current_asian_date != trading_day:
                self._current_asian_date = trading_day
                self._temp_asian_high = high
                self._temp_asian_low = low
            else:
                if self._temp_asian_high is not None:
                    self._temp_asian_high = max(self._temp_asian_high, high)
                if self._temp_asian_low is not None:
                    self._temp_asian_low = min(self._temp_asian_low, low)
            return None

        # At 00:00 NY, the Asian range is locked
        if 0 <= hour < 18 and self._temp_asian_high is not None and self._temp_asian_low is not None:
            if self._locked_range is None or self._locked_range.date != self._current_asian_date:
                assert self._current_asian_date is not None
                self._locked_range = AsianRange(
                    date=self._current_asian_date,
                    high=self._temp_asian_high,
                    low=self._temp_asian_low,
                    complete=True,
                )
                self._swept_high_extreme = None
                self._swept_low_extreme = None
                logger.info(
                    "Locked Asian Range for %s: High=%s, Low=%s",
                    self._locked_range.date,
                    self._locked_range.high,
                    self._locked_range.low,
                )

        if self._locked_range is None:
            return None

        # ------------------------------------------------------------------- #
        # 2. London Open Window Evaluation (02:00 - 05:00 NY)
        # ------------------------------------------------------------------- #
        if not (2 <= hour < 5):
            return None

        # Prevent multiple trades on the same calendar day
        if bar_date in self._traded_dates:
            return None

        sweep_threshold = Decimal(self.min_sweep_ticks) * self.tick_size
        buffer = Decimal(self.buffer_ticks) * self.tick_size
        asian_high = self._locked_range.high
        asian_low = self._locked_range.low

        # Track any breach during the window
        if high > (asian_high + sweep_threshold):
            self._swept_high_extreme = max(self._swept_high_extreme or high, high)

        if low < (asian_low - sweep_threshold):
            self._swept_low_extreme = min(self._swept_low_extreme or low, low)

        # ------------------------------------------------------------------- #
        # 3. Setup A: Bearish Judas Swing (Asian High Swept & Reclaimed)
        # ------------------------------------------------------------------- #
        if self._swept_high_extreme is not None and close < asian_high:
            # Bar breached above Asian High and closed back inside the range!
            entry = close
            sl = self._swept_high_extreme + buffer
            tp = asian_low  # Target the opposite side of the Asian range

            risk = sl - entry
            reward = entry - tp

            # If Asian Low is too close, extend target to at least min_risk_reward R
            if risk > 0 and reward / risk < self.min_risk_reward:
                tp = entry - (risk * self.min_risk_reward)

            if risk > 0 and tp < entry:
                self._traded_dates.add(bar_date)
                setup = JudasSetup(
                    side=JudasSide.SELL,
                    bar_time=bar.timestamp_utc,
                    entry_price=entry,
                    stop_loss=sl,
                    take_profit=tp,
                    asian_high=asian_high,
                    asian_low=asian_low,
                    sweep_extreme=self._swept_high_extreme,
                    reason=f"Judas Short: Asian High ({asian_high}) swept to {self._swept_high_extreme} and reclaimed.",
                )
                logger.info("Found Judas Swing Setup: %s", setup)
                return setup

        # ------------------------------------------------------------------- #
        # 4. Setup B: Bullish Judas Swing (Asian Low Swept & Reclaimed)
        # ------------------------------------------------------------------- #
        if self._swept_low_extreme is not None and close > asian_low:
            # Bar breached below Asian Low and closed back inside the range!
            entry = close
            sl = self._swept_low_extreme - buffer
            tp = asian_high  # Target the opposite side of the Asian range

            risk = entry - sl
            reward = tp - entry

            # If Asian High is too close, extend target to at least min_risk_reward R
            if risk > 0 and reward / risk < self.min_risk_reward:
                tp = entry + (risk * self.min_risk_reward)

            if risk > 0 and tp > entry:
                self._traded_dates.add(bar_date)
                setup = JudasSetup(
                    side=JudasSide.BUY,
                    bar_time=bar.timestamp_utc,
                    entry_price=entry,
                    stop_loss=sl,
                    take_profit=tp,
                    asian_high=asian_high,
                    asian_low=asian_low,
                    sweep_extreme=self._swept_low_extreme,
                    reason=f"Judas Long: Asian Low ({asian_low}) swept to {self._swept_low_extreme} and reclaimed.",
                )
                logger.info("Found Judas Swing Setup: %s", setup)
                return setup

        return None
