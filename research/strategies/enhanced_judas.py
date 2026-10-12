"""Volatility-Normalized (ATR & Range-Scaled) Judas Swing Strategy for Research.

Supports the 14 pre-registered research variants:
- Volatility-normalized sweep: ATR multiple or % of Asian Range.
- Volatility-normalized stop buffer: ATR multiple.
- Asian range width quality gate in ATR units.
- Dynamic target modes: Opposite Extreme, 50% Equilibrium, or Fixed R.
- Direction toggle: Standard (mean-reversion) vs Flipped (contrarian breakout).
- Displacement candle body filter.
- London auction session bias filter.

Strictly zero-float: all price calculations in Decimal.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from enum import StrEnum
from zoneinfo import ZoneInfo

from engine.market.models import Bar
from engine.strategy.judas_swing import AsianRange, JudasSetup, JudasSide

NY_TZ = ZoneInfo("America/New_York")


class TargetMode(StrEnum):
    OPPOSITE_EXTREME = "opposite_extreme"
    EQUILIBRIUM = "equilibrium"
    FIXED_RR = "fixed_rr"


class SweepMode(StrEnum):
    TICKS = "ticks"
    ATR = "atr"
    RANGE_PCT = "range_pct"


class BufferMode(StrEnum):
    TICKS = "ticks"
    ATR = "atr"


class EnhancedJudasStrategy:
    """Pre-registered research strategy with volatility-normalized thresholds."""

    def __init__(
        self,
        # Sweep configuration
        sweep_mode: SweepMode = SweepMode.TICKS,
        sweep_ticks: int = 5,
        sweep_atr_mult: Decimal = Decimal("0.25"),
        sweep_range_pct: Decimal = Decimal("0.10"),
        tick_size: Decimal = Decimal("0.01"),
        # Stop buffer configuration
        buffer_mode: BufferMode = BufferMode.TICKS,
        buffer_ticks: int = 10,
        buffer_atr_mult: Decimal = Decimal("0.20"),
        # Asian range width gate
        use_range_gate: bool = False,
        min_range_atr: Decimal = Decimal("1.5"),
        max_range_atr: Decimal = Decimal("6.0"),
        # Target configuration
        target_mode: TargetMode = TargetMode.OPPOSITE_EXTREME,
        fixed_rr_multiple: Decimal = Decimal("2.0"),
        min_risk_reward: Decimal = Decimal("1.5"),
        # Filters
        require_displacement: bool = False,
        displacement_pct: Decimal = Decimal("0.40"),
        use_session_bias: bool = False,
        # Direction toggle
        flipped_direction: bool = False,
    ) -> None:
        self.sweep_mode = sweep_mode
        self.sweep_ticks = sweep_ticks
        self.sweep_atr_mult = sweep_atr_mult
        self.sweep_range_pct = sweep_range_pct
        self.tick_size = tick_size

        self.buffer_mode = buffer_mode
        self.buffer_ticks = buffer_ticks
        self.buffer_atr_mult = buffer_atr_mult

        self.use_range_gate = use_range_gate
        self.min_range_atr = min_range_atr
        self.max_range_atr = max_range_atr

        self.target_mode = target_mode
        self.fixed_rr_multiple = fixed_rr_multiple
        self.min_risk_reward = min_risk_reward

        self.require_displacement = require_displacement
        self.displacement_pct = displacement_pct
        self.use_session_bias = use_session_bias
        self.flipped_direction = flipped_direction

        # State tracking
        self._current_asian_date: dt.date | None = None
        self._temp_asian_high: Decimal | None = None
        self._temp_asian_low: Decimal | None = None
        self._locked_range: AsianRange | None = None

        self._session_open_price: Decimal | None = None
        self._swept_high_extreme: Decimal | None = None
        self._swept_low_extreme: Decimal | None = None
        self._traded_dates: set[dt.date] = set()

        # Continuous 14-period ATR
        self._atr: Decimal | None = None
        self._prev_close: Decimal | None = None

    @property
    def current_asian_range(self) -> AsianRange | None:
        return self._locked_range

    @property
    def current_atr(self) -> Decimal | None:
        return self._atr

    def on_bar(self, bar: Bar) -> JudasSetup | None:
        """Process closed 5-minute candle and evaluate for pre-registered variant."""
        ny_time = bar.timestamp_utc.astimezone(NY_TZ)
        bar_date = ny_time.date()
        hour = ny_time.hour

        mid = bar.mid_ohlc
        open_price = mid.open
        high = mid.high
        low = mid.low
        close = mid.close

        # Update continuous ATR(14)
        if self._prev_close is not None:
            tr = max(high - low, abs(high - self._prev_close), abs(low - self._prev_close))
            if self._atr is None:
                self._atr = tr
            else:
                self._atr = (self._atr * Decimal("13") + tr) / Decimal("14")
        else:
            self._atr = high - low
        self._prev_close = close

        atr_val = self._atr or (high - low) or Decimal("1.00")

        # ------------------------------------------------------------------- #
        # 1. Asian Session Measurement (18:00 - 00:00 NY)
        # ------------------------------------------------------------------- #
        if hour >= 18:
            trading_day = bar_date + dt.timedelta(days=1)
            if self._current_asian_date != trading_day:
                self._current_asian_date = trading_day
                self._temp_asian_high = high
                self._temp_asian_low = low
                self._swept_high_extreme = None
                self._swept_low_extreme = None
                self._session_open_price = open_price
            else:
                if self._temp_asian_high is not None:
                    self._temp_asian_high = max(self._temp_asian_high, high)
                if self._temp_asian_low is not None:
                    self._temp_asian_low = min(self._temp_asian_low, low)
            return None

        # Lock Asian Range at 00:00 NY
        if (
            0 <= hour < 18
            and self._temp_asian_high is not None
            and self._temp_asian_low is not None
            and (self._locked_range is None or self._locked_range.date != self._current_asian_date)
            and self._current_asian_date is not None
        ):
            self._locked_range = AsianRange(
                date=self._current_asian_date,
                high=self._temp_asian_high,
                low=self._temp_asian_low,
                complete=True,
            )
            self._swept_high_extreme = None
            self._swept_low_extreme = None

        if self._locked_range is None:
            return None

        # Only 1 trade per day
        if bar_date in self._traded_dates:
            return None

        asian_high = self._locked_range.high
        asian_low = self._locked_range.low
        range_width = asian_high - asian_low

        # Asian Range Quality Gate
        if self.use_range_gate:
            if range_width < (self.min_range_atr * atr_val) or range_width > (self.max_range_atr * atr_val):
                return None

        # ------------------------------------------------------------------- #
        # 2. London Open Window Check (02:00 - 05:00 NY)
        # ------------------------------------------------------------------- #
        in_london_window = 2 <= hour < 5
        if not in_london_window:
            return None

        # Track session open (first bar of London)
        if hour == 2 and ny_time.minute == 0 and self._session_open_price is None:
            self._session_open_price = open_price

        # Calculate sweep distance
        if self.sweep_mode == SweepMode.ATR:
            sweep_distance = self.sweep_atr_mult * atr_val
        elif self.sweep_mode == SweepMode.RANGE_PCT:
            sweep_distance = self.sweep_range_pct * range_width
        else:  # TICKS (baseline)
            sweep_distance = Decimal(str(self.sweep_ticks)) * self.tick_size

        # Calculate buffer distance
        if self.buffer_mode == BufferMode.ATR:
            buffer_distance = self.buffer_atr_mult * atr_val
        else:  # TICKS (baseline)
            buffer_distance = Decimal(str(self.buffer_ticks)) * self.tick_size

        # ------------------------------------------------------------------- #
        # 3. Sweep Tracking
        # ------------------------------------------------------------------- #
        if high >= asian_high + sweep_distance:
            if self._swept_high_extreme is None or high > self._swept_high_extreme:
                self._swept_high_extreme = high

        if low <= asian_low - sweep_distance:
            if self._swept_low_extreme is None or low < self._swept_low_extreme:
                self._swept_low_extreme = low

        # ------------------------------------------------------------------- #
        # 4. Setup Evaluation
        # ------------------------------------------------------------------- #
        bar_range = high - low

        # A. High Swept Reclaim
        if self._swept_high_extreme is not None and close < asian_high:
            if not self.flipped_direction:
                # Standard Short Judas
                entry = close
                sl = self._swept_high_extreme + buffer_distance
                if entry >= asian_low:
                    # Displacement Check
                    if self.require_displacement:
                        body = open_price - close  # positive for bearish
                        if bar_range > 0 and (body / bar_range) < self.displacement_pct:
                            return None

                    # Session bias
                    if self.use_session_bias and self._session_open_price is not None:
                        if high < self._session_open_price:
                            return None

                    risk = sl - entry
                    if risk > 0:
                        if self.target_mode == TargetMode.EQUILIBRIUM:
                            tp = (asian_high + asian_low) / Decimal("2")
                        elif self.target_mode == TargetMode.FIXED_RR:
                            tp = entry - (risk * self.fixed_rr_multiple)
                        else:
                            tp = asian_low

                        if tp < entry:
                            self._traded_dates.add(bar_date)
                            return JudasSetup(
                                side=JudasSide.SELL,
                                bar_time=bar.timestamp_utc,
                                entry_price=entry,
                                stop_loss=sl,
                                take_profit=tp,
                                asian_high=asian_high,
                                asian_low=asian_low,
                                sweep_extreme=self._swept_high_extreme,
                                reason="Judas Short: Asian High swept and reclaimed",
                            )
            else:
                # Flipped / Contrarian Breakout: Buy high continuation
                entry = close
                sl = asian_high - buffer_distance
                risk = entry - sl
                if risk > 0:
                    tp = entry + (risk * Decimal("2.0"))
                    self._traded_dates.add(bar_date)
                    return JudasSetup(
                        side=JudasSide.BUY,
                        bar_time=bar.timestamp_utc,
                        entry_price=entry,
                        stop_loss=sl,
                        take_profit=tp,
                        asian_high=asian_high,
                        asian_low=asian_low,
                        sweep_extreme=self._swept_high_extreme,
                        reason="Flipped Breakout Long: Asian High swept and holding",
                    )

        # B. Low Swept Reclaim
        if self._swept_low_extreme is not None and close > asian_low:
            if not self.flipped_direction:
                # Standard Long Judas
                entry = close
                sl = self._swept_low_extreme - buffer_distance
                if entry <= asian_high:
                    # Displacement Check
                    if self.require_displacement:
                        body = close - open_price  # positive for bullish
                        if bar_range > 0 and (body / bar_range) < self.displacement_pct:
                            return None

                    # Session bias
                    if self.use_session_bias and self._session_open_price is not None:
                        if low > self._session_open_price:
                            return None

                    risk = entry - sl
                    if risk > 0:
                        if self.target_mode == TargetMode.EQUILIBRIUM:
                            tp = (asian_high + asian_low) / Decimal("2")
                        elif self.target_mode == TargetMode.FIXED_RR:
                            tp = entry + (risk * self.fixed_rr_multiple)
                        else:
                            tp = asian_high

                        if tp > entry:
                            self._traded_dates.add(bar_date)
                            return JudasSetup(
                                side=JudasSide.BUY,
                                bar_time=bar.timestamp_utc,
                                entry_price=entry,
                                stop_loss=sl,
                                take_profit=tp,
                                asian_high=asian_high,
                                asian_low=asian_low,
                                sweep_extreme=self._swept_low_extreme,
                                reason="Judas Long: Asian Low swept and reclaimed",
                            )
            else:
                # Flipped / Contrarian Breakout: Sell low continuation
                entry = close
                sl = asian_low + buffer_distance
                risk = sl - entry
                if risk > 0:
                    tp = entry - (risk * Decimal("2.0"))
                    self._traded_dates.add(bar_date)
                    return JudasSetup(
                        side=JudasSide.SELL,
                        bar_time=bar.timestamp_utc,
                        entry_price=entry,
                        stop_loss=sl,
                        take_profit=tp,
                        asian_high=asian_high,
                        asian_low=asian_low,
                        sweep_extreme=self._swept_low_extreme,
                        reason="Flipped Breakout Short: Asian Low swept and holding",
                    )

        return None
