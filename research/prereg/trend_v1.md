# PRE-REGISTRATION: Trend Rules on Liquid Markets (trend_v1)

**Registration Date:** 2026-10-11  
**Study Identifier:** `trend_v1`  
**Status:** FROZEN PRIOR TO BACKTEST EXECUTION  

---

## 1. Hypothesis

**Core Hypothesis:** Simple, long-horizon trend rules earn positive net expectancy (mean net $R > 0$) across liquid global markets under realistic transaction costs, slippage, and financing drag, and are not idiosyncratic to gold (XAUUSD).

---

## 2. Universe & Instrument Identity Audit

### 2.1 Candidate Universe (Fixed)
The fixed universe consists of 12 liquid instruments across asset classes (commodities, FX majors, equity indices):
1. **XAUUSD** — Gold Continuous Futures (`GC=F`), quoted in USD/troy oz
2. **XAGUSD** — Silver Continuous Futures (`SI=F`), quoted in USD/troy oz
3. **EURUSD** — Euro / US Dollar (`EURUSD=X`)
4. **GBPUSD** — British Pound / US Dollar (`GBPUSD=X`)
5. **USDJPY** — US Dollar / Japanese Yen (`USDJPY=X`)
6. **AUDUSD** — Australian Dollar / US Dollar (`AUDUSD=X`)
7. **USDCAD** — US Dollar / Canadian Dollar (`USDCAD=X`)
8. **USDCHF** — US Dollar / Swiss Franc (`USDCHF=X`)
9. **US500** — S&P 500 Large-Cap Cash Index (`^GSPC`)
10. **GER40** — DAX 40 Performance Index (`^GDAXI`)
11. **Brent** — Brent Crude Oil Continuous Futures (`BZ=F`), quoted in USD/barrel
12. **NDX** — NASDAQ-100 Large-Cap Index (`^NDX`) [Selected 12th liquid index]

### 2.2 History Requirement & Audit Rule
- Minimum history required: **12 years** prior to 2026. Any symbol with $< 12$ years is automatically dropped and logged.
- **Instrument Identity Audit**: For each symbol, inspect first and last close, annual min/max closes, and confirm instrument identity (guarding against ticker confusion or proxy mismatch).

### 2.3 Instrument Identity Audit Results
All 12 instruments meet and exceed the 12-year history requirement:

| Symbol | Benchmark Ticker | Description | First Date | Last Date | History Span | Total Daily Bars | First Close | Last Close (2025-12-31) | Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **XAUUSD** | `GC=F` | Gold Futures (USD/oz) | 2001-10-11 | 2025-12-31 | 24.2 yrs | 6,082 | $281.80 | $4,341.10 | PASS |
| **XAGUSD** | `SI=F` | Silver Futures (USD/oz) | 2001-10-11 | 2025-12-31 | 24.2 yrs | 6,082 | $4.403 | $70.134 | PASS |
| **EURUSD** | `EURUSD=X` | Euro / US Dollar FX | 2003-12-01 | 2025-12-31 | 22.1 yrs | 5,730 | 1.1965 | 1.1747 | PASS |
| **GBPUSD** | `GBPUSD=X` | British Pound / USD FX | 2003-12-01 | 2025-12-31 | 22.1 yrs | 5,742 | 1.7186 | 1.3467 | PASS |
| **USDJPY** | `USDJPY=X` | USD / Japanese Yen FX | 2001-10-08 | 2025-12-31 | 24.2 yrs | 6,289 | 120.20 | 156.41 | PASS |
| **AUDUSD** | `AUDUSD=X` | Australian Dollar / USD FX | 2006-05-15 | 2025-12-31 | 19.6 yrs | 5,106 | 0.7671 | 0.6698 | PASS |
| **USDCAD** | `USDCAD=X` | USD / Canadian Dollar FX | 2003-09-16 | 2025-12-31 | 22.3 yrs | 5,798 | 1.3667 | 1.3695 | PASS |
| **USDCHF** | `USDCHF=X` | USD / Swiss Franc FX | 2003-09-16 | 2025-12-31 | 22.3 yrs | 5,796 | 1.3807 | 0.7917 | PASS |
| **US500** | `^GSPC` | S&P 500 Index | 2001-10-10 | 2025-12-31 | 24.2 yrs | 6,096 | 1,080.99 | 6,845.50 | PASS |
| **GER40** | `^GDAXI` | DAX 40 Index | 2001-10-09 | 2025-12-30 | 24.2 yrs | 6,150 | 4,472.42 | 24,490.41 | PASS |
| **Brent** | `BZ=F` | Brent Crude Oil (USD/bbl) | 2007-07-30 | 2025-12-31 | 18.4 yrs | 4,585 | $75.74 | $60.85 | PASS |
| **NDX** | `^NDX` | NASDAQ-100 Index | 2001-10-10 | 2025-12-31 | 24.2 yrs | 6,096 | 1,304.68 | 25,249.85 | PASS |

Zero instruments dropped. Complete multi-decade history confirmed for all 12 assets.

---

## 3. Data Specification & Integrity Invariants

1. **Granularity:** Daily bars ($1D$).
2. **Primary Evaluation Period:** 2010-01-01 through 2025-12-31 (16 full calendar years).
3. **Warm-up Window:** Historical data from 2006-2009 is utilized exclusively as indicator warm-up (Wilder ATR 20, SMA 200, 252-day return, 55-day Donchian channels) so all models are fully warmed up on the first bar of 2010.
4. **Hard-Stop Gate:** The data loader strictly rejects any bar with timestamp $> 2025\text{-}12\text{-}31$. Data for 2026 is sealed and stays completely unloaded until the sealed step.
5. **Lookahead Prohibition:** Signals evaluate on closed bars only ($t$). Order entry occurs at the next bar's Open ($t+1$).
6. **Volatility Normalizer:** Wilder's ATR(20) on daily bars:
   $$\text{TR}_t = \max(H_t - L_t, |H_t - C_{t-1}|, |L_t - C_{t-1}|)$$
   $$\text{ATR}_t = \frac{19 \times \text{ATR}_{t-1} + \text{TR}_t}{20}$$
   Initial ATR seeded with the 20-day simple moving average of True Range.

---

## 4. Trend Rules (3 Pre-Registered Specifications)

### Rule T1: Donchian Channel Breakout (Classic Turtle)
- **Entry Long:** Bar $t$ Close $> \max(\text{High}_{t-55}, \dots, \text{High}_{t-1})$. Enter Long at Bar $t+1$ Open.
- **Entry Short:** Bar $t$ Close $< \min(\text{Low}_{t-55}, \dots, \text{Low}_{t-1})$. Enter Short at Bar $t+1$ Open.
- **Exit Channel:**
  - Long: Bar $t$ Close $< \min(\text{Low}_{t-20}, \dots, \text{Low}_{t-1})$. Exit at Bar $t+1$ Open.
  - Short: Bar $t$ Close $> \max(\text{High}_{t-20}, \dots, \text{High}_{t-1})$. Exit at Bar $t+1$ Open.
- **Initial Stop Loss:** $2 \times \text{ATR}(20)$ at entry.
  - Long Stop: $\text{Entry} - 2 \times \text{ATR}$
  - Short Stop: $\text{Entry} + 2 \times \text{ATR}$
- **Initial Risk Unit ($R$):** $R = 2 \times \text{ATR}(20)$.

### Rule T2: Moving Average Cross (Golden / Death Cross)
- **Signal Regime:** SMA50 vs SMA200 on daily Closes.
- **Entry Long:** Fresh bullish cross (SMA50 crosses above SMA200 at Bar $t$). Enter Long at Bar $t+1$ Open.
- **Entry Short:** Fresh bearish cross (SMA50 crosses below SMA200 at Bar $t$). Enter Short at Bar $t+1$ Open.
- **Exit:** Opposite cross (SMA50 crosses below SMA200 for Longs; SMA50 crosses above SMA200 for Shorts). Exit at Bar $t+1$ Open.
- **Initial Stop Loss:** $3 \times \text{ATR}(20)$ at entry.
  - Long Stop: $\text{Entry} - 3 \times \text{ATR}$
  - Short Stop: $\text{Entry} + 3 \times \text{ATR}$
- **Re-entry Gate:** **After a stop-out, wait for the next fresh cross.** No re-entries within the same moving-average regime.
- **Initial Risk Unit ($R$):** $R = 3 \times \text{ATR}(20)$.

### Rule T3: Time-Series Momentum (TSMOM / 12-Month Sign)
- **Evaluation Timing:** Calendar month-ends (last trading day of each month).
- **Signal:** 252-day return sign:
  $$\text{Ret}_{252} = \frac{C_{\text{month-end}} - C_{\text{month-end}-252}}{C_{\text{month-end}-252}}$$
  - Long if $\text{Ret}_{252} > 0$
  - Short if $\text{Ret}_{252} < 0$
- **Holding Episode:** One trade per episode. Maintain position while the sign persists across consecutive month-ends.
- **Exit:** When month-end sign reverses (e.g., from positive to negative), exit at next bar's Open.
- **Initial Stop Loss:** $3 \times \text{ATR}(20)$ at entry.
- **Re-entry Gate:** If stopped out during an ongoing episode, the position remains flat until a new episode begins (upon sign flip).
- **Initial Risk Unit ($R$):** $R = 3 \times \text{ATR}(20)$.

### Stop Execution Protocol (All Rules)
- Daily stops are evaluated against each bar's High and Low.
- **Conservative Same-Bar Rule:** Stop is checked first before profit exit.
- **Gap Rule:** If the bar's Open gaps beyond the stop price, the fill occurs at the Open (pessimistic fill), capturing full gap slippage.

---

## 5. Frozen Cost & Friction Model

### 5.1 Spread Table (Frozen with $1.5\times$ Safety Margin)
Spreads are fixed per instrument with an explicit $1.5\times$ institutional safety multiplier applied to typical market spreads:

| Instrument | Typical Spread | Safety Multiplier | Frozen Base Spread | Spread Units |
| :--- | :--- | :--- | :--- | :--- |
| **EURUSD** | 0.00010 (1.0 pip) | $1.5\times$ | **0.00015** (1.5 pips) | Price |
| **GBPUSD** | 0.00012 (1.2 pips) | $1.5\times$ | **0.00018** (1.8 pips) | Price |
| **USDJPY** | 0.010 (1.0 pip) | $1.5\times$ | **0.015** (1.5 pips) | Price |
| **AUDUSD** | 0.00012 (1.2 pips) | $1.5\times$ | **0.00018** (1.8 pips) | Price |
| **USDCAD** | 0.00015 (1.5 pips) | $1.5\times$ | **0.000225** (2.25 pips) | Price |
| **USDCHF** | 0.00015 (1.5 pips) | $1.5\times$ | **0.000225** (2.25 pips) | Price |
| **XAUUSD** | $0.25 | $1.5\times$ | **$0.375** | USD/oz |
| **XAGUSD** | $0.020 | $1.5\times$ | **$0.030** | USD/oz |
| **US500** | 0.50 pts | $1.5\times$ | **0.75 pts** | Index pts |
| **GER40** | 1.50 pts | $1.5\times$ | **2.25 pts** | Index pts |
| **Brent** | $0.04 | $1.5\times$ | **$0.06** | USD/bbl |
| **NDX** | 1.50 pts | $1.5\times$ | **2.25 pts** | Index pts |

### 5.2 Stop Slippage
- Orders exiting via Stop-Loss incur an extra **$0.5 \times \text{spread}$** slippage penalty beyond the exit spread.

### 5.3 Financing / Overnight Carry Drag
- **Primary Rate:** **2.0% per annum** of notional value on every day held, charged symmetrically on both Long and Short positions.
- **Sensitivity Scenarios:**
  1. Financing at **0.0%** per annum.
  2. Financing at **4.0%** per annum.
  3. Spread at **$2.0\times$** baseline.

### 5.4 Unified R-Normalized Drag Formulation
For a trade with entry price $P_{\text{entry}}$, stop distance $D_{\text{stop}}$, and holding duration of $H$ calendar days:
$$\text{Gross } R = \begin{cases} \frac{P_{\text{exit}} - P_{\text{entry}}}{D_{\text{stop}}} & \text{(Long)} \\ \frac{P_{\text{entry}} - P_{\text{exit}}}{D_{\text{stop}}} & \text{(Short)} \end{cases}$$
$$\text{Drag}_{\text{spread}} = \frac{\text{Spread}}{D_{\text{stop}}}$$
$$\text{Drag}_{\text{slippage}} = \begin{cases} \frac{0.5 \times \text{Spread}}{D_{\text{stop}}} & \text{if exit is Stop Loss} \\ 0 & \text{otherwise} \end{cases}$$
$$\text{Drag}_{\text{financing}} = \frac{P_{\text{entry}}}{D_{\text{stop}}} \times r_{\text{annual}} \times \frac{H}{365.0}$$
$$\text{Net } R = \text{Gross } R - \text{Drag}_{\text{spread}} - \text{Drag}_{\text{slippage}} - \text{Drag}_{\text{financing}}$$

Gross and Net $R$ are reported side-by-side in all study outputs.

---

## 6. Statistical Inference & Null Models

### 6.1 Calendar Month Block Bootstrap
- Trades across all markets are mapped to their exit calendar month $(Y, M)$.
- Resampling unit: Entire calendar month blocks (preserving contemporaneous cross-market correlation).
- Resamples: **10,000 bootstrap iterations**.
- Statistic: Mean Net $R$.
- Hypothesis Test: One-sided $p$-value for $H_0: \mathbb{E}[\text{Net } R] \le 0$:
  $$p = \frac{1}{B} \sum_{b=1}^B \mathbb{I}(\overline{\text{Net } R}^{(b)} \le 0)$$
- Multiple Testing Adjustment: **Holm-Bonferroni correction** across the 3 rules at $\alpha_{\text{family}} = 0.05$.
- Confidence Interval: Two-sided $95\%$ bootstrap percentile interval $[2.5\%, 97.5\%]$.

### 6.2 Null Distributions (Fixed Seed = 42, Iterations $\ge 2,000$)
1. **Null A (Drift-Matched Random Entry):**
   - For every real trade in market $M$ with direction $D$, stop multiple $S$, and duration $H$:
   - Draw a random entry date $t_{\text{rand}}$ from market $M$.
   - Enter in the **same** direction $D$, with stop distance $S \times \text{ATR}(t_{\text{rand}})$.
   - Simulate for holding duration $H$ (or until stop is triggered).
   - Apply identical cost, slippage, and financing functions.
   - Compute mean Net $R$ across trades for each iteration.
2. **Null B (Direction Shuffle):**
   - For every real trade, retain the exact entry date, market, duration, and stop multiple.
   - Randomly assign direction (Long vs Short, $p=0.50$).
   - Apply identical stop logic, duration, and cost functions.
   - Compute mean Net $R$ across trades for each iteration.

---

## 7. Pre-Registered Acceptance Gates (Survival Criteria)

A rule is declared an empirical **SURVIVOR** if and only if **ALL FIVE** conditions are satisfied:
1. **Sample Size:** $N \ge 200$ pooled trades across the universe.
2. **Net Positive Expectancy:** Mean Net $R > 0$ under primary costs ($1.5\times$ spread + 2% financing).
3. **Statistical Significance:** Holm-adjusted bootstrap $p$-value $< 0.05$.
4. **Superiority over Nulls:** Real Mean Net $R \ge$ 95th percentile of Null A **AND** 95th percentile of Null B.
5. **Sub-Period & Breadth Robustness:**
   - Mean Net $R > 0$ in Sub-period 1 (2010–2017), AND
   - Mean Net $R > 0$ in Sub-period 2 (2018–2025), AND
   - Mean Net $R > 0$ in Ex-Metals sample (excluding XAUUSD and XAGUSD).

**Default Verdict:** If no rule satisfies all five criteria, the outcome is formally and unambiguously recorded as:
> **"INCONCLUSIVE: NO EDGE DEMONSTRATED"**  
No ad-hoc parameter searches, post-hoc filtering, or further optimization permitted.

---

## 8. Sealed 2026 Out-of-Sample Sanity Check

- Only if a rule satisfies all 5 acceptance gates will 2026 data (2026-01-01 to latest) be loaded.
- Evaluated once as a sanity check reporting $N_{2026}$ and Net $R_{2026}$.
- If no rule survives, 2026 remains strictly sealed.

---

## 9. Verification & Audit Protocol

1. **Deterministic Hand-Traces:** 5 trades per rule explicitly audited against raw daily bars, showing exact bar dates, OHLC, indicator values, stop fills, exit channels, and full dollar / $R$ fee decompositions.
2. **Independent Recomputation:** Standalone verification script (`verify_trend.py`) reads trade CSVs directly and recomputes all summary tables, bootstrap metrics, and gate outcomes.
3. **Preregistration Commitment:** The SHA-256 hash of this document and its Git commit hash are recorded into all generated output tables, reports, and logs.
