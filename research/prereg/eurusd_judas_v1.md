# Pre-Registration: ICT Asian Judas Swing on EURUSD (2015–2025)

**Document:** `research/prereg/eurusd_judas_v1.md`  
**Registration Version:** `eurusd_judas_v1`  
**Target Symbol:** `EURUSD`  
**Sample Period:** 2015-01-01 to 2025-12-31 (11 Full Calendar Years)  
**Status:** FROZEN BEFORE DATA ACQUISITION & BACKTEST EXECUTION  

---

## 1. Context & Objective

This pre-registration locks the exact experimental protocol for evaluating the ICT Asian Session Range Expansion ("Judas Swing") strategy on EURUSD across the 11-year sample period (2015–2025).

In accordance with the pre-registration standard:
1. Every rule, parameter, formula, session window, time zone, and cost model is frozen in this document prior to inspecting EURUSD backtest results.
2. The exact strategy implementation and Git commit from the baseline XAUUSD production engine are fixed and reused without tuning.
3. A single primary hypothesis test is defined with three mandatory pass conditions. Any analysis beyond this single primary test is categorized strictly as EXPLORATORY and recorded in `variants_tried.csv`.

---

## 2. Strategy Code & Reused Baseline Commit

- **Strategy Implementation:** `engine.strategy.judas_swing.JudasSwingStrategy`
- **Source File:** `engine/src/engine/strategy/judas_swing.py`
- **Reused Baseline Commit:** `270ec4b8ab1dbd1ae776e1d5a2bf484ec3d41696` (and current tree `0fff845113835391eaf9ff16275e24e2c2ef1734`, where strategy code is identical).
- **Arithmetic Invariant:** Strict Decimal arithmetic in price/pips/R representations; zero floating-point arithmetic in domain/strategy logic.

---

## 3. Rules & Execution Parameters

The strategy operates on completed 5-minute (M5) bars constructed from 1-minute (M1) intraday bars:

1. **Asian Session Range Measurement (18:00 - 00:00 America/New_York):**
   - High and Low extremes are recorded across closed M5 bars within `18:00 <= hour < 24:00` NY time.
   - At `00:00` NY, the Asian Range is locked for the upcoming trading day:
     - `asian_high = max(highs during 18:00-00:00 NY)`
     - `asian_low = min(lows during 18:00-00:00 NY)`
   - Any prior sweep tracking state is cleanly reset upon session rollover.

2. **London Open Window Evaluation (02:00 - 05:00 America/New_York):**
   - Signal evaluation occurs exclusively on closed M5 bars where `2 <= hour < 5` NY time.
   - Outside this window, no new entries are triggered.

3. **Sweep Tracking:**
   - **Bearish Setup Sweep:** Price exceeds `asian_high + min_sweep` (`high > asian_high + min_sweep`). The highest extreme reached during the window is tracked as `swept_high_extreme`.
   - **Bullish Setup Sweep:** Price breaches below `asian_low - min_sweep` (`low < asian_low - min_sweep`). The lowest extreme reached during the window is tracked as `swept_low_extreme`.

4. **Reclaim & Entry Trigger:**
   - **SELL (Bearish Judas):** `swept_high_extreme` exists, and the current closed M5 bar closes back below `asian_high` (`close < asian_high`).
     - Boundary Guard: `entry >= asian_low` (entry candle close must remain within/above the opposite session boundary).
     - Entry price: Close of the reclaim M5 candle (`entry = close`).
   - **BUY (Bullish Judas):** `swept_low_extreme` exists, and the current closed M5 bar closes back above `asian_low` (`close > asian_low`).
     - Boundary Guard: `entry <= asian_high` (entry candle close must remain within/below the opposite session boundary).
     - Entry price: Close of the reclaim M5 candle (`entry = close`).

5. **Stop-Loss & Take-Profit Geometry:**
   - **Stop-Loss (SL):**
     - SELL: `sl = swept_high_extreme + buffer`
     - BUY: `sl = swept_low_extreme - buffer`
     - Risk: `risk = abs(entry - sl)`. Trades with `risk <= 0` are invalid.
   - **Take-Profit (TP):**
     - SELL: Target `asian_low`. If initial reward/risk `< 2.0`, target is extended to `entry - (risk * 2.0)`.
     - BUY: Target `asian_high`. If initial reward/risk `< 2.0`, target is extended to `entry + (risk * 2.0)`.
     - Target extension ensures minimum pre-trade reward-to-risk ratio of `2.0R`.

6. **Order Execution & Lifecycle Tracking:**
   - Market entry at the close of the reclaim M5 bar.
   - Intra-bar exit tracking:
     - If bar touches SL: Exit at SL price (Reason: `STOP_LOSS`).
     - If bar touches TP: Exit at TP price (Reason: `TAKE_PROFIT`).
     - Conflict rule: If both SL and TP are touched in the same bar, conservative assumption holds: Stop-Loss is hit first.
   - Dynamic Session Exit: If still open at `16:45` NY time, exit at the close price of the 16:45 M5 bar (Reason: `DYNAMIC_CLOSE_1645`).
   - No trade holds across the `17:00` NY session rollover.

7. **Trades Per Day:**
   - Maximum 1 trade per calendar trading day (`bar_date in self._traded_dates`).

---

## 4. Session Times, Time Zone & DST Handling

- **Reference Clock:** `America/New_York` algorithmic time, evaluated using Python `zoneinfo.ZoneInfo("America/New_York")` with pinned `tzdata` package.
- **UTC Equivalents:**
  - **Winter (EST, UTC-5):**
    - Asian Range (18:00–00:00 NY) = 23:00 UTC (previous day) to 05:00 UTC.
    - London Window (02:00–05:00 NY) = 07:00 UTC to 10:00 UTC.
    - Daily Cutoff (16:45 NY) = 21:45 UTC.
  - **Summer (EDT, UTC-4):**
    - Asian Range (18:00–00:00 NY) = 22:00 UTC (previous day) to 04:00 UTC.
    - London Window (02:00–05:00 NY) = 06:00 UTC to 09:00 UTC.
    - Daily Cutoff (16:45 NY) = 20:45 UTC.
- All bar timestamps in data storage and logs are stored as UTC.

---

## 5. Instrument-Specific Constants & Fixed Conversion Formulas

To prevent parameter tuning, all XAUUSD constants are mapped to EURUSD via a single fixed formula based on the instrument's native price increment (tick/point) and standard spread conventions:

| Parameter | XAUUSD Production Engine | EURUSD Fixed Conversion Formula | EURUSD Value |
|---|---|---|---|
| **Tick Size (`tick_size`)** | `0.01` ($0.01/point) | Native minimum price increment (5-digit broker) | `0.00001` (0.1 pip) |
| **Pip Size** | $0.10 (10 points) | Standard FX convention ($10^{-4}$) | `0.00010` (10 points) |
| **Sweep Ticks (`min_sweep_ticks`)** | 5 ticks ($0.05) | Retain 5 native ticks: $5 \times \text{tick\_size}$ | `0.00005` (0.5 pip / 5 points) |
| **Buffer Ticks (`buffer_ticks`)** | 10 ticks ($0.10) | Retain 10 native ticks: $10 \times \text{tick\_size}$ | `0.00010` (1.0 pip / 10 points) |
| **Min Risk:Reward (`min_risk_reward`)** | 2.0 | Identical fixed parameter | `2.0` |
| **Contract Size** | 100 oz | Standard Forex lot | 100,000 EUR |
| **Risk Budget (1% of $1,000)** | $10.00 | 1% of $1,000 account | $10.00 |
| **Min Lot / Lot Step** | 0.01 / 0.01 | Broker demo specification (`EURUSDm`) | 0.01 / 0.01 |

**Position Sizing Formula:**
$$\text{Lots} = \max\left(0.01, \left\lfloor \frac{\text{Risk Budget USD}}{\text{Stop Distance in pips} \times \$10/\text{pip}} \times 100 \right\rfloor \div 100\right)$$

---

## 6. Transaction Cost Model

All trade results are evaluated in R-multiples where $1R = \text{Stop Distance}$:
$$\text{Gross } R = \frac{\text{Raw Exit PnL}}{\text{Stop Distance}}$$
$$\text{Cost Drag } (R) = \frac{\text{Total Cost in pips}}{\text{Stop Distance in pips}}$$
$$\text{Net } R = \text{Gross } R - \text{Cost Drag } (R)$$

### Four Standard Cost Scenarios:
1. **Scenario (a) - Primary:** Measured per-bar spread + broker commission. (On the demo account `EURUSDm`, commission = $0.00/lot, so cost = measured spread).
2. **Scenario (b):** Scenario (a) plus 0.5 pip slippage per side (1.0 pip total round-trip slippage on stop-out).
3. **Scenario (c):** Flat 1.0 pip round-trip spread (0.00010).
4. **Scenario (d):** Zero cost = Gross R.

**Overnight Swaps:**
Trades are closed by 16:45 NY before the 17:00 NY rollover; zero trades hold overnight, so overnight swap drag is $0.00.

---

## 7. Data Sources

Two independent data sources covering 2015-01-01 to 2025-12-31:
- **Primary Source:** Dukascopy Bank tick-aggregated 1-minute OHLCV bars (HuggingFace `loppy03/fx-ticks-dukascopy` archive with measured `spread_avg` per bar).
- **Secondary Source:** HistData MetaTrader 1-minute archives (`DAT_MT_EURUSD_M1_{YYYY}.csv` direct downloads from `histdata.com`).

---

## 8. ONE Primary Test & Pre-Registered Pass Criteria

The primary test evaluates the exact Judas Swing strategy on the Primary Data Source (Dukascopy 2015–2025) under Cost Scenario (a).

To declare that the strategy demonstrates a statistically verified edge on EURUSD, **ALL THREE** of the following conditions must pass:
1. **Criterion (a):** Net Mean R 95% Confidence Interval lower bound $> 0.00R$, computed via 1,000 week-block bootstrap resamples.
2. **Criterion (b):** Gross Mean R exceeds the 95th percentile of the Placebo Null Model ($p < 0.05$).
3. **Criterion (c):** Net Mean R $> 0.00R$ on the Secondary Data Source (HistData 2015–2025).

If any of (a), (b), or (c) fails, the strategy FAILS the pre-registration gate.

---

## 9. Placebo Null Specification

- 1,000 randomized simulation runs.
- In each run, entry dates and entry times are randomly permuted within the same London Open window (02:00–05:00 NY).
- Trade stop-loss distances are sampled directly from the empirical stop-size distribution of the real trades to match risk geometry.
- Identical 1:2 R:R target and dynamic close (16:45 NY) logic applied.

---

## 10. Post-Commit Immutability

Upon Git commit of this document:
- No rules, session times, conversion formulas, or parameters may be altered.
- All analysis scripts must execute read-only from the committed specification.
- Any secondary exploratory test must be explicitly labeled `EXPLORATORY` and recorded in `variants_tried.csv`.
