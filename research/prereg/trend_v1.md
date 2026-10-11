# PRE-REGISTRATION: Trend Rules on Liquid Markets (`trend_v1.2`)

**Registration Version:** `trend_v1.2` (Amended Pre-Registration)  
**Amendment Date:** 2026-10-11  
**Prior Versions:**  
- `trend_v1` (`0ed2400`, SHA-256 `6bc1c9c9ae10fba5e69fbbe46d51a38f259c3c4b124c33b3121defa7cfc346a7`)  
- `trend_v1.1` (`b14b541`, SHA-256 `766bfbbe85d18823423c96760c438c1670b7cf7ae5fbfc55fd7ebcba1d31dfab`)  
**Status:** FROZEN PRIOR TO BACKTEST EXECUTION  

---

## 1. Summary of Amendments

### Amendments in `trend_v1.1`:
1. **Rule T1 Reset Condition:** Added an explicit channel reset rule following any stop-out or exit: no new entry is permitted until the breakout condition has reset, defined as a daily bar closing back inside the 55-day channel ($\text{Close} \le \text{High}_{55}$ for Longs, $\text{Close} \ge \text{Low}_{55}$ for Shorts). A subsequent entry requires a fresh breakout.
2. **Primary Evaluation Sample Window:** Expanded the primary sample from fixed 2010–2025 to **each market's full available history from first date + 1 year warm-up (252 trading bars) through 2025-12-31**. Sub-periods:
   - `Earliest–2009`: Report-only context.
   - `2010–2017`: Core sub-period evaluation for Gate 5.
   - `2018–2025`: Core sub-period evaluation for Gate 5.
3. **Statistical Power Labeling ($N < 200$):** If any pooled or sub-period sample size has $N < 200$, it is explicitly flagged as `[UNDERPOWERED]` (signaling insufficient sample size to reject $H_0$, rather than affirmative evidence of absence of edge). Expected trade counts per rule are stated.
4. **Data Quality Audit & Roll Handling:** Incorporated report-only data quality audit (bars with $H < L$, zero/NaN, stale closes, $|Close - Close_{prev}| > 6 \times ATR_{20}$). Stated continuous futures roll handling for `GC=F`, `SI=F`, and `BZ=F`.
5. **Bug & Execution Policy:** Explicitly codified that any defect fix mandates re-running the entire pre-registered test battery from scratch across all 12 assets, incrementing a run counter (`run_number`).
6. **Report-Only Outputs:** Formally mandated reporting of disaggregations (by market, by side), cost sensitivities (financing 0%, 2%, 4%; spread $2\times$), equal-risk portfolio drawdown, and script-generated report verification.

### Amendments in `trend_v1.2`:
1. **Pre-Registered Verdict Ladder:** Fixed a formal 3-tier verdict ladder:
   - **(a) SURVIVOR:** All five gates pass at primary costs ($1.5\times$ spread + 2% financing).
   - **(b) GROSS-OF-FINANCING SURVIVOR:** All five gates pass at 0% financing ($1.5\times$ spread + 0% financing) but not at 2% financing (reported as: *"edge before carry; viability depends on financing terms"*).
   - **(c) Otherwise INCONCLUSIVE: NO EDGE DEMONSTRATED.**
   - Gates and numerical thresholds are identical and unchanged. Holm-Bonferroni correction is applied within each financing level across the 3 rules, and the final report states that two financing levels (0% and 2%) were examined.
2. **Sealed 2026 Evaluation Policy:** Sealed 2026 out-of-sample data opens exactly once, and ONLY for rules qualifying under (a) `SURVIVOR` or (b) `GROSS-OF-FINANCING SURVIVOR`, evaluated at the financing level that qualified them (2% for (a), 0% for (b)). If no rule qualifies for (a) or (b), 2026 data remains strictly sealed and unopened.
3. **Granular Drag & Duration Disaggregation:** Added mandatory report-only breakdown of average drag by component (spread drag, slippage drag, financing drag in $R$) per rule and per market, alongside average holding duration ($H$ in days).

---

## 2. Hypothesis

**Core Hypothesis:** Simple, long-horizon trend rules earn positive net expectancy (mean net $R > 0$) across liquid global markets under realistic transaction costs, slippage, and financing drag, and are not idiosyncratic to gold (XAUUSD).

---

## 3. Fixed Universe & Instrument Identity Audit

### 3.1 Candidate Universe (Fixed 12 Assets)
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

### 3.2 History Requirement
Minimum history required: **12 years** prior to 2026. Any symbol with $< 12$ years is automatically dropped.

### 3.3 Instrument Identity Audit Table
All 12 instruments meet and exceed the 12-year history requirement:

| Symbol | Benchmark Ticker | Asset Class / Description | First Date | Last Date | History Span | Total Daily Bars | First Close | Last Close (2025-12-31) | Status |
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

Zero instruments dropped. Multi-decade continuous history confirmed for all 12 assets.

---

## 4. Data Quality Audit & Sanitization Invariants

### 4.1 Empirical Data Quality Scan (Report-Only)
Across all 68,070 daily bars in the 12-instrument universe:
- **Bars with $High < Low$:**
  - XAUUSD: 1 bar (2009-11-23, where raw recorded High was $1,163.00 while Open/Close was $1,164.30).
  - Brent: 6 bars (historical quotes in 2009–2010 where High was recorded slightly below Open/Close by a few cents).
  - Other 10 instruments: 0 bars with $High < Low$.
- **Zero or NaN Prices:** 0 occurrences across all 12 instruments.
- **Stale Closes (Identical consecutive closes):** Occurred on 0 to 39 bars per symbol across 20+ years (typical of quiet holiday market sessions).
- **Large Price Jumps ($|Close_t - Close_{t-1}| > 6 \times ATR_{20}$):**
  - XAUUSD: 2 instances (notably 2013-04-15 historic gold flash crash).
  - XAGUSD: 4 instances (notably 2013-04-15 silver crash and 2006/2007/2016 volatility events).
  - EURUSD: 4 instances (2008 GFC volatility spikes).
  - USDJPY: 2 instances (2008 GFC volatility spikes).
  - USDCHF: 1 instance (2015-01-15 SNB EURCHF peg removal).
  - Brent (`BZ=F`): 0 instances $> 6 \times ATR_{20}$.
  - US500, GER40, NDX, AUDUSD, USDCAD, GBPUSD: 0 instances $> 6 \times ATR_{20}$.

### 4.2 Data Sanitization & Roll Handling Rules (Frozen)
1. **Geometric Bar Sanitization Invariant:** For all bars loaded into the engine, True High and True Low are strictly enforced:
   $$\text{High}_t = \max(\text{High}_t, \text{Open}_t, \text{Close}_t)$$
   $$\text{Low}_t = \min(\text{Low}_t, \text{Open}_t, \text{Close}_t)$$
2. **Continuous Futures Roll Handling:** Yahoo Finance continuous contracts (`GC=F`, `SI=F`, `BZ=F`) reflect front-month pricing. Because back-adjusted ratio series are not provided by free public feeds without proprietary vendor licensing, roll discontinuities are evaluated under standard gap rules: if a roll gap breaches a stop loss, the fill occurs at the bar's Open, applying full gap slippage.

---

## 5. Sample Window, Sub-Periods & Statistical Power

1. **Primary Evaluation Sample:** Each market's full available history from **first date + 1 year warm-up (252 trading bars) through 2025-12-31**.
   - For assets starting 2001 (XAUUSD, XAGUSD, USDJPY, US500, GER40, NDX): starts late 2002.
   - For assets starting 2003 (EURUSD, GBPUSD, USDCAD, USDCHF): starts late 2004.
   - For AUDUSD (starts 2006): starts mid-2007.
   - For Brent (starts mid-2007): starts mid-2008.
2. **Sub-Periods:**
   - `Earliest–2009`: Report-only baseline.
   - `2010–2017`: Core sub-period (Gate 5 evaluation).
   - `2018–2025`: Core sub-period (Gate 5 evaluation).
3. **Statistical Power Labeling ($N < 200$):**
   - If a test sample has $N < 200$, it is explicitly flagged with `[FLAG: N < 200, UNDERPOWERED]`. This denotes that the statistical test lacks statistical power to detect modest effect sizes, rather than affirmative evidence against edge.
4. **Expected Trade Counts:**
   - For T1 Donchian: expected $\approx 40\text{--}70$ trades per market over 16–23 years ($\approx 600\text{--}800$ pooled).
   - For T2 SMA Cross: expected $\approx 15\text{--}30$ trades per market over 16–23 years ($\approx 250\text{--}350$ pooled).
   - For T3 TSMOM: expected $\approx 15\text{--}35$ trades per market over 16–23 years ($\approx 250\text{--}350$ pooled).
5. **Sealed 2026 Evaluation Policy:**
   - The data loader strictly hard-stops at **2025-12-31**. Data for 2026 remains completely unloaded until the sealed out-of-sample sanity check.
   - **Sealed 2026 opens once, and ONLY for rules qualifying under the Verdict Ladder as (a) `SURVIVOR` or (b) `GROSS-OF-FINANCING SURVIVOR`, evaluated at the financing level that qualified them (2% for SURVIVOR, 0% for GROSS-OF-FINANCING SURVIVOR).**
   - If no rule qualifies for (a) or (b), 2026 data remains strictly sealed and unopened.

---

## 6. Trend Rules (3 Pre-Registered Specifications)

### Rule T1: Donchian Channel Breakout (with Reset Condition)
- **Signal:**
  - Long if Bar $t$ Close $> \max(\text{High}_{t-55}, \dots, \text{High}_{t-1})$. Enter Long at Bar $t+1$ Open.
  - Short if Bar $t$ Close $< \min(\text{Low}_{t-55}, \dots, \text{Low}_{t-1}).$ Enter Short at Bar $t+1$ Open.
- **Exit Channel:**
  - Long: Bar $t$ Close $< \min(\text{Low}_{t-20}, \dots, \text{Low}_{t-1})$. Exit at Bar $t+1$ Open.
  - Short: Bar $t$ Close $> \max(\text{High}_{t-20}, \dots, \text{High}_{t-1})$. Exit at Bar $t+1$ Open.
- **Initial Stop Loss:** $2 \times \text{ATR}(20)$ at entry.
- **Reset Condition (v1.1 Amendment):** **After a stop-out or channel exit, no new entry is permitted until the breakout condition has reset (a daily bar closing back inside the 55-day channel: $\text{Close} \le \text{High}_{55}$ for Longs, or $\text{Close} \ge \text{Low}_{55}$ for Shorts). Only after closing back inside the channel can a subsequent fresh breakout trigger a new trade.**
- **Initial Risk Unit ($R$):** $R = 2 \times \text{ATR}(20)$.

### Rule T2: Moving Average Cross (Golden / Death Cross)
- **Signal:** SMA50 vs SMA200 on daily Closes.
- **Entry:** Fresh cross (SMA50 crosses above SMA200 for Long; crosses below for Short). Enter at Bar $t+1$ Open.
- **Exit:** Opposite cross. Exit at Bar $t+1$ Open.
- **Initial Stop Loss:** $3 \times \text{ATR}(20)$ at entry.
- **Re-entry Gate:** **After a stop-out, wait for the next fresh cross.** No re-entries within the same moving-average regime.
- **Initial Risk Unit ($R$):** $R = 3 \times \text{ATR}(20)$.

### Rule T3: Time-Series Momentum (TSMOM / 12-Month Sign)
- **Evaluation Timing:** Calendar month-ends (last trading day of month).
- **Signal:** Sign of 252-day return: $\text{Ret}_{252} = (C_t - C_{t-252}) / C_{t-252}$.
  - Long if $\text{Ret}_{252} > 0$; Short if $\text{Ret}_{252} < 0$.
- **Holding Episode:** One trade per episode. Hold while the sign persists across consecutive month-ends.
- **Exit:** Opposite sign at month-end. Exit at Bar $t+1$ Open.
- **Initial Stop Loss:** $3 \times \text{ATR}(20)$ at entry.
- **Re-entry Gate:** If stopped out during an episode, hold flat until a new episode begins upon sign flip.
- **Initial Risk Unit ($R$):** $R = 3 \times \text{ATR}(20)$.

### Stop Execution Protocol (All Rules)
- Daily stops are evaluated against each bar's High and Low.
- **Conservative Same-Bar Rule:** Stop is checked first before profit/channel exits.
- **Gap Rule:** If the bar's Open gaps beyond the stop price, fill at the Open (pessimistic fill).

---

## 7. Frozen Cost & Friction Model

### 7.1 Spread Table (Frozen with $1.5\times$ Safety Multiplier)
| Instrument | Base Typical Spread | Multiplier | Frozen Base Spread | Spread Units |
| :--- | :--- | :--- | :--- | :--- |
| **EURUSD** | 0.00010 (1.0 pip) | $1.5\times$ | **0.00015** | Price |
| **GBPUSD** | 0.00012 (1.2 pips) | $1.5\times$ | **0.00018** | Price |
| **USDJPY** | 0.010 (1.0 pip) | $1.5\times$ | **0.015** | Price |
| **AUDUSD** | 0.00012 (1.2 pips) | $1.5\times$ | **0.00018** | Price |
| **USDCAD** | 0.00015 (1.5 pips) | $1.5\times$ | **0.000225** | Price |
| **USDCHF** | 0.00015 (1.5 pips) | $1.5\times$ | **0.000225** | Price |
| **XAUUSD** | $0.25 | $1.5\times$ | **$0.375** | USD/oz |
| **XAGUSD** | $0.020 | $1.5\times$ | **$0.030** | USD/oz |
| **US500** | 0.50 pts | $1.5\times$ | **0.75 pts** | Index pts |
| **GER40** | 1.50 pts | $1.5\times$ | **2.25 pts** | Index pts |
| **Brent** | $0.04 | $1.5\times$ | **$0.06** | USD/bbl |
| **NDX** | 1.50 pts | $1.5\times$ | **2.25 pts** | Index pts |

### 7.2 Stop Slippage
Orders exiting via Stop-Loss incur an extra **$0.5 \times \text{spread}$** slippage penalty.

### 7.3 Financing / Overnight Carry Drag
- **Primary Rate:** **2.0% per annum** of notional value on every calendar day held, charged symmetrically on both Long and Short positions.
- **Sensitivities:** 0% financing, 4% financing, and Spread $2\times$ baseline.

### 7.4 Formulas
$$\text{Drag}_{\text{spread}} = \frac{\text{Spread}}{D_{\text{stop}}}, \quad \text{Drag}_{\text{slippage}} = \frac{0.5 \times \text{Spread}}{D_{\text{stop}}} \ (\text{if stop}), \quad \text{Drag}_{\text{financing}} = \frac{P_{\text{entry}}}{D_{\text{stop}}} \times r \times \frac{H}{365.0}$$
$$\text{Net } R = \text{Gross } R - \text{Drag}_{\text{spread}} - \text{Drag}_{\text{slippage}} - \text{Drag}_{\text{financing}}$$

---

## 8. Statistical Inference & Null Models

1. **Calendar Month Block Bootstrap:**
   - Resampling unit: Calendar exit month blocks across all markets simultaneously.
   - Resamples: **10,000 bootstrap iterations**.
   - Hypothesis: One-sided $p$-value for $H_0: \mathbb{E}[\text{Net } R] \le 0$.
   - Correction: **Holm-Bonferroni correction** across the 3 rules applied within each financing level (0% and 2%) at $\alpha_{\text{family}} = 0.05$. The final report will explicitly state that two financing levels were examined.
   - Confidence Intervals: 95% bootstrap percentile intervals $[2.5\%, 97.5\%]$.
2. **Null Distributions (Fixed Seed = 42, Iterations $\ge 2,000$):**
   - **Null A (Drift-Matched Random Entry):** Random entry date in same market, SAME direction, same stop multiple, same duration, same costs.
   - **Null B (Direction Shuffle):** Real entry dates, random 50/50 direction, same stop multiple, same durations, same costs.

---

## 9. Pre-Registered Acceptance Gates & Verdict Ladder

### 9.1 The Five Acceptance Gates
A rule is evaluated against **ALL FIVE** gates:
1. **Sample Size:** $N \ge 200$ pooled trades across the universe.
2. **Net Positive Expectancy:** Mean Net $R > 0$ under the tested financing condition.
3. **Statistical Significance:** Holm-adjusted bootstrap $p$-value $< 0.05$ (applied within financing level across the 3 rules).
4. **Superiority over Nulls:** Real Mean Net $R \ge$ 95th percentile of Null A **AND** 95th percentile of Null B.
5. **Sub-Period & Breadth Robustness:**
   - Mean Net $R > 0$ in Sub-period 2010–2017, AND
   - Mean Net $R > 0$ in Sub-period 2018–2025, AND
   - Mean Net $R > 0$ in Ex-Metals sample (excluding XAUUSD and XAGUSD).

### 9.2 The Pre-Registered Verdict Ladder (Fixed)
The outcome for each candidate rule is determined strictly by the following ladder:

- **(a) SURVIVOR:**  
  All five gates pass at primary costs ($1.5\times$ spread + 2% financing carry).

- **(b) GROSS-OF-FINANCING SURVIVOR:**  
  All five gates pass at 0% financing carry ($1.5\times$ spread + 0% financing), but not at 2% financing carry.  
  *Reported as:* **"edge before carry; viability depends on financing terms"**.

- **(c) Otherwise INCONCLUSIVE: NO EDGE DEMONSTRATED:**  
  The rule fails one or more gates under both financing conditions.

*Note:* Gates and numerical thresholds are identical and unchanged. Holm-Bonferroni correction is applied within each financing level across the 3 rules, and the report will explicitly disclose that two financing levels were examined.

---

## 10. Bug Policy, Audit & Reporting Mandates

1. **Bug & Re-Run Policy:** If any software bug or pipeline flaw is discovered, the fix must be documented in git, and the **ENTIRE pre-registered battery across all 12 markets must be re-run from scratch**. Selective re-running of individual rules or markets is forbidden. A run counter (`run_number`) must be incremented and recorded in all metadata.
2. **Deterministic Hand-Traces:** 5 trades per rule audited against raw daily bars.
3. **Report-Only Disaggregations & Drag Breakdown:**
   - Average drag by component (spread drag, slippage drag, financing drag in $R$) per rule and per market.
   - Average holding duration ($H$ in days) per rule and per market.
   - By-market breakdown (12 markets).
   - By-side breakdown (Long vs Short).
   - Spread $2\times$ sensitivity.
   - Financing rate sensitivities (0%, 2%, 4% for every rule).
   - Equal-risk portfolio equity curve and maximum drawdown in $R$.
4. **Script-Generated Final Report:** `research/trend/FINAL_REPORT.md` must be generated by script from the JSON/CSV result files with zero hand-typed numbers, accompanied by a test that asserts every number in the markdown matches the result files bit-for-bit.
