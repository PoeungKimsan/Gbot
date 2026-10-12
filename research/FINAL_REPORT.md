# Final Research Report: ICT Asian Judas Swing & Silver Bullet Edge Verification

**Repository:** `Gbot` (Automated MetaTrader 5 Trading Engine)  
**Author:** Research Team  
**Date:** October 11, 2026  
**Status:** COMPLETE (Final Frozen Report)  
**Acceptance Gate Verdict:** **NO EDGE DEMONSTRATED** (0 of 8 pre-registered variants passed)  
**Sealed 2026 Data Status:** **UNTOUCHED AND UNVIOLATED**  

---

## 1. Research Question

> **Primary Question:** Does the ICT Asian Judas Swing strategy—or the ICT Silver Bullet setup—possess a statistically significant, positive edge net of realistic execution costs and broker friction on XAUUSD (and major FX currency pairs)?

The research protocol reframed the inquiry from "proving a strategy works" to an adversarial test of whether any detectable directional edge survives rigorous cost accounting, permutation null models, out-of-sample temporal holdouts, and execution realism constraints.

---

## 2. Data Sources & Granularity

The research protocol evaluated strategies strictly on **M5 intraday bars** (for setup detection, order entry, and intra-bar lifecycle tracking) and **Daily bars** (for higher-timeframe ATR-14 volatility and 20-day EMA trend bias), constructed from verified institutional tick-level archives:

| Dataset | Instrument(s) | Granularity | Date Range | Size / Bars | Status & Source |
|---|---|---|---|---|---|
| **XAUUSD Primary History** | XAUUSD (Gold) | M5 & Daily | 2015-01-01 to 2025-12-31 | 11 Years (782,000+ M5 bars) | Dukascopy tick-aggregated bars parsed to M5 & Daily |
| **FX Majors Intraday** | EURUSD, GBPUSD, USDJPY, AUDUSD | M5 & Daily | 2021-08-01 to 2024-10-01 | ~910,000 M5 bars | HuggingFace institutional FX intraday parquet |
| **Sealed Holdout** | XAUUSD | M5 & Daily | 2026-01-01 to YTD | Sealed | **Untouched and unviolated** (frozen behind gate) |

### Pre-2021 FX Data Availability
- **Free Source Audit:** Free public APIs (Yahoo Finance, Alpha Vantage free tier, Polygon free tier) do not provide unthrottled intraday M5 history prior to August 2021. Downloading 10–15 years of free FX data via Dukascopy `.bi5` binary files requires ~130,000 individual HTTP requests per pair (>500,000 requests total), which is rate-limited.
- **Common Window Used:** The longest common, clean multi-instrument intraday window available without paid institutional feeds is **August 2021 to October 2024** across XAUUSD, EURUSD, GBPUSD, and USDJPY.

---

## 3. Unified Transaction Cost Model

All strategies, variants, and null models were subjected to a single, immutable transaction cost function applied on every trade:

$$\\text{Cost Drag (R)} = \\frac{\\text{Half-Spread}_{\\text{entry}} + \\text{Half-Spread}_{\\text{exit}} + \\text{Slippage}_{\\text{stop}}}{\\text{Stop Distance}}$$

- **XAUUSD:** Round-trip spread = $\$0.20$ ($0.10$ entry, $0.10$ exit) + $\$0.05$ slippage on stop-outs. Total stop-out friction = $\$0.25$ per ounce.
- **EURUSD:** Round-trip spread = $1.0\\text{ pip}$ ($0.00010$) + $0.2\\text{ pip}$ slippage ($0.00002$) on stop-outs.
- **GBPUSD:** Round-trip spread = $1.2\\text{ pips}$ ($0.00012$) + $0.3\\text{ pip}$ slippage ($0.00003$) on stop-outs.
- **USDJPY:** Round-trip spread = $1.2\\text{ pips}$ ($0.012$) + $0.3\\text{ pip}$ slippage ($0.003$) on stop-outs.
- **ES Index Drag Correction:** Previously reported $2.77R$ cost drag on ES was identified as an artifact of the ticker `ES` in the stock dataset corresponding to Eversource Energy equity stock ($\\approx \\$60$ share price) rather than E-mini S&P 500 futures. ES was dropped from the multi-instrument pool.

---

## 4. Methodology & Statistical Standards

1. **Pure Strategy Simulation:** Bar-by-bar execution using the production `engine.strategy.judas_swing.JudasSwingStrategy` and pure Silver Bullet detectors (`research.strategies.silver_bullet`). Zero look-ahead bias.
2. **Directional Falsification (Fade vs Mirror vs Null B):**
   - **Fade (V0):** ICT hypothesis (fade Asian range sweep after liquidity run).
   - **Mirror (V10 Rebuilt):** Simulated with **its own stop-loss and take-profit** in the opposite direction on the identical signal bar (BUY if Fade SOLD, SELL if Fade BOUGHT), with identical dollar stop distance and R-multiple target, forward-tracked bar-by-bar.
   - **Null B:** 1,000 random-direction draws on the exact same signal bars with full unified transaction costs.
3. **Cluster-Robust Bootstrap CIs:** 95% confidence intervals constructed via 1,000 bootstrap resamples clustered by calendar date to account for intraday cross-trade correlation.
4. **Acceptance Criteria (Pre-Registered Gate):**
   - Net Expectancy $> 0.00R$.
   - Lower bound of 95% Bootstrap CI $> 0.00R$.
   - Outperforms Null A and Null B at the 95th percentile ($p < 0.05$).
   - Sample size $N \\ge 200$.

---

## 5. Complete Pre-Registration Log

A total of **22 distinct strategy variants** were pre-registered and evaluated across the two phases of the study (14 in the initial exploration phase, 8 in the second confirmation phase):

### Initial Exploratory Phase (Variants V0–V13)
| ID | Variant Name | Description / Rule |
|---|---|---|
| **V0** | Baseline Live Logic | Asian Judas Swing: 18:00–00:00 Asian Range, sweep of Asian high/low during London window (02:00–05:00 NY), reclaim extreme, 1:2 R:R target, stop at sweep extreme. |
| **V1** | ATR-Normalized Sweep | Sweep buffer scaled by ATR(14) instead of fixed $0.05. |
| **V2** | Range-Normalized Sweep | Sweep buffer scaled by Asian range width. |
| **V3** | ATR-Normalized Buffer | Entry buffer scaled by ATR. |
| **V4** | Asian Range Width Gate | Skip setups when Asian range width is outside normal quantile. |
| **V5** | Equilibrium Target | Take profit set to 50% equilibrium of Asian range. |
| **V6** | Fixed 1.5R Target | Fixed 1:1.5 risk-to-reward ratio. |
| **V7** | Fixed 2.0R Target | Fixed 1:2.0 risk-to-reward ratio. |
| **V8** | Displacement Reclaim Gate | Reclaim bar must close beyond displacement threshold. |
| **V9** | Breakeven Trigger at +1.0R | Move stop-loss to breakeven once price reaches +1.0R. |
| **V10** | Flipped Contrarian Breakout (Orig) | Enter on breakout direction rather than fade (original unconstrained version). |
| **V11** | Session Open Auction Bias | Filter by London opening bar relation to Asian range. |
| **V12** | Composite Thesis A | ATR-Normalized Sweep combined with Fixed 1.5R Target. |
| **V13** | Composite Thesis B | ATR-Normalized Sweep combined with Equilibrium Target. |

### Confirmation Phase (Rebuilt & Hardened Variants)
| ID | Variant Name | Description / Rule |
|---|---|---|
| **V10 (Rebuilt)** | Mirror Judas Breakout | Opposite direction on same signal bar, same stop distance and target, forward-simulated with own SL/TP and full costs. |
| **C1** | Cost Drag Filter | Skip trade if round-trip spread + slippage exceeds 10% of stop distance. |
| **H1** | Daily Trend Bias Filter | Sweeps only in direction of 20-day daily EMA. |
| **H2** | Higher-Timeframe Proximity | Asian extreme within 0.5 ATR of previous day high/low. |
| **H3** | Macro News Filter | Skip London entries on days with high-impact EU/UK/US events. |
| **C1+H1** | Composite Filter | Combined C1 cost filter + H1 trend bias filter. |
| **SB Limit** | Silver Bullet Limit Entry | Limit order at Fair Value Gap (FVG) threshold. |
| **SB Market** | Silver Bullet Market Entry | Market order immediately after FVG confirmation bar. |

---

## 6. Performance Tables

### 6.1 Baseline Judas: Fade vs Mirror vs Nulls (2020–2022 Validation)

*(Ground Truth Sources: `judas_fade_trades_2020_2022.csv`, `judas_mirror_trades_2020_2022.csv`, `null_simulations_summary.json`)*

| Setup | $N$ | Win Rate | Gross Exp ($R$) | Net Exp ($R$) | Cost Drag ($R$) | 95% Bootstrap CI | Null B %ile |
|---|---|---|---|---|---|---|---|
| **Judas Fade (V0)** | 525 | 20.38% | +0.0553R | **-0.1460R** | 0.2013R | [-0.3678, +0.0923] | 67.1% |
| **Judas Mirror (V10 Rebuilt)** | 525 | 22.67% | -0.0536R | **-0.2558R** | 0.2021R | [-0.4467, -0.0588] | 25.4% |
| **Null B (Cost-Aligned Mean)** | 525 | 48.67% | +0.0125R | **-0.1894R** | 0.2019R | [-0.3323, +0.0188] | 50.0% |

#### Mirror Trade Simulation Mechanics:
- **Own Stop and Target:** The mirror trade was forward-simulated with its own explicit stop-loss and take-profit (`m_sl = entry_p - s_dist`, `m_tp = entry_p + 2 * s_dist` for BUY mirror), tracked bar-by-bar across future bars up to 288 bars (24 hours).
- **Not a Sign Negation:** It was **not** computed by negating Fade PnL (`-pnl_fade`). 
- **R Distribution:** Min Net R = $-2.506R$, Max Net R = $+14.886R$, Median Net R = $-1.128R$, Std = $2.244R$. Losses are strictly clustered near $-1.0R$ minus cost drag (70.9% of trades exit in $[-1.5R, -1.0R)$). Exit breakdown: 406 Stop Loss, 117 Take Profit, 2 Timeout.
- **Percentile Reality:** Fade ranks at the **67.1th percentile** of cost-aligned Null B, and Mirror ranks at the **25.4th percentile**, demonstrating that both directions perform within standard random noise once market friction is deducted.

---

### 6.2 Pre-Registered Judas Variants (2020–2022 Validation & 2023–2025 Test)

*(Ground Truth Source: `step1_judas_variants_summary.json`)*

| Period | Variant | $N$ | Gross Exp ($R$) | Net Exp ($R$) | Cost Drag ($R$) | Win Rate | Status / Outcome |
|---|---|---|---|---|---|---|---|
| **2020–2022** | **V0 Baseline** | 525 | +0.0553R | **-0.1460R** | 0.2013R | 20.38% | Fails Gate ($<0$) |
| **2020–2022** | **C1 Cost Filter** | 214 | -0.1032R | **-0.1765R** | 0.0733R | 28.04% | Fails Gate ($<0$) |
| **2020–2022** | **H1 Trend Bias** | 241 | +0.0083R | **-0.1856R** | 0.1939R | 19.92% | Fails Gate ($<0$) |
| **2020–2022** | **H2 HTF Extreme** | 422 | +0.1043R | **-0.0929R** | 0.1972R | 21.09% | Fails Gate ($<0$) |
| **2020–2022** | **H3 News Filter** | 356 | +0.1781R | **-0.0172R** | 0.1952R | 22.19% | Fails Gate ($<0$) |
| **2020–2022** | **C1+H1 Composite** [Flag: $N<200$] | 93 | -0.1290R | **-0.1991R** | 0.0701R | 26.88% | Fails Gate ($<0$) |
| **2023–2025** | **V0 Baseline** | 488 | -0.0075R | **-0.1787R** | 0.1712R | 21.52% | Fails Gate ($<0$) |
| **2023–2025** | **C1 Cost Filter** | 246 | -0.1009R | **-0.1615R** | 0.0606R | 29.67% | Fails Gate ($<0$) |
| **2023–2025** | **H1 Trend Bias** | 223 | -0.0539R | **-0.2202R** | 0.1663R | 20.18% | Fails Gate ($<0$) |
| **2023–2025** | **H2 HTF Extreme** | 397 | +0.0497R | **-0.1175R** | 0.1672R | 22.42% | Fails Gate ($<0$) |
| **2023–2025** | **H3 News Filter** | 342 | +0.1079R | **-0.0652R** | 0.1730R | 22.22% | Fails Gate ($<0$) |
| **2023–2025** | **C1+H1 Composite** [Flag: $N<200$] | 114 | -0.1284R | **-0.1891R** | 0.0607R | 28.07% | Fails Gate ($<0$) |

---

### 6.3 Historical 11-Year Judas Run (2015–2025) & Realism Floor Impact

*(Ground Truth Source: `followup_audit_v2_summary.json`)*

Testing the production engine class (`engine.strategy.judas_swing.JudasSwingStrategy`) across the historical 11-year dataset ($N = 1857$ trades):

| Filter Condition | $N$ | Gross Exp ($R$) | Net Exp ($R$) | Cost Drag ($R$) | Avg Dollar P&L / Trade (1.0 lot) |
|---|---|---|---|---|---|
| **Unfiltered (Raw Live Class)** | 1857 | +0.0560R | **-0.1792R** | 0.2352R | $-27.57 |
| **Realism Floor (Stop $\\ge \\$0.60$)** | 1568 | +0.0006R | **-0.1660R** | 0.1666R | $-30.28 |

> **Realism Floor Discrepancy Note:** The apparent gross positive expectancy of $+0.0560R$ on the unfiltered 11-year run was **100% driven by 289 micro-stop trades** (stop $< \$0.60$). When the broker realism floor ($\text{stop} \ge \max(\text{broker\_min}, 3\times\text{spread}) = \$0.60$) is enforced, the gross edge instantly collapses from $+0.0560R$ to $+0.0006R$, and net expectancy remains deeply negative ($-0.1660R$).

---

### 6.4 Silver Bullet Multi-Instrument Performance (2021–2024 Common Window)

*(Ground Truth Source: `followup_audit_v2_summary.json`)*

#### Limit Entries (Entry at FVG threshold)
| Instrument | $N$ | Dates | Cost Drag ($R$) | Gross Exp ($R$) | Net Exp ($R$) | Flag |
|---|---|---|---|---|---|---|
| **XAUUSD** | 23 | 2021-09-23 to 2024-08-21 | 0.1317R | +0.4348R | +0.3031R | [Flag: $N < 200$] |
| **EURUSD** | 29 | 2021-10-01 to 2024-09-27 | 0.1230R | +0.2308R | +0.1078R | [Flag: $N < 200$] |
| **GBPUSD** | 36 | 2021-08-06 to 2024-09-30 | 0.1116R | +0.2695R | +0.1579R | [Flag: $N < 200$] |
| **USDJPY** | 23 | 2021-10-06 to 2024-08-07 | 0.1771R | -0.2743R | -0.4514R | [Flag: $N < 200$] |
| **POOLED** | **111** | **2021–2024 Common Window** | **0.1323R** | **+0.1810R** | **+0.0486R** | [Flag: $N < 200$] |

- **Date-Clustered 95% CI (Pooled Limit):** [-0.2214, +0.3233] (spans zero).

#### Market Entries (Entry at close of FVG formation bar)
| Instrument | $N$ | Dates | Cost Drag ($R$) | Gross Exp ($R$) | Net Exp ($R$) | Flag |
|---|---|---|---|---|---|---|
| **XAUUSD** | 57 | 2021-08-04 to 2024-08-21 | 0.0513R | +0.1064R | +0.0551R | [Flag: $N < 200$] |
| **EURUSD** | 53 | 2021-10-01 to 2024-09-27 | 0.0628R | +0.1009R | +0.0380R | [Flag: $N < 200$] |
| **GBPUSD** | 62 | 2021-08-06 to 2024-09-30 | 0.0618R | +0.0183R | -0.0435R | [Flag: $N < 200$] |
| **USDJPY** | 49 | 2021-08-04 to 2024-09-30 | 0.0607R | +0.0038R | -0.0569R | [Flag: $N < 200$] |
| **POOLED** | **221** | **2021–2024 Common Window** | **0.0591R** | **+0.0576R** | **-0.0015R** | $N \\ge 200$ |

- **Date-Clustered 95% CI (Pooled Market):** [-0.1115, +0.1106] (centered on zero).

---

### 6.5 Live Engine Position Sizing, Leverage & Clamping Audit

*(Ground Truth Source: `closing_leverage_table.json`)*

The live trading engine sizes orders via:
$$\\text{Raw Lots} = \\text{floor}\\left(\\frac{\\text{Equity} \\times 0.01}{\\text{Stop Distance} \\times 100}, 2\\right), \\quad \\text{Clamped Lots} = \\min(\\text{Raw Lots}, 10.00)$$

| Equity | Stop Distance | Raw Lots | Lots (Clamped) | 10-Lot Clamp Binds? | Notional (@$2,000) | Leverage (@$2,000) | Notional (@$4,100) | Leverage (@$4,100) | Effective Risk % |
|---|---|---|---|---|---|---|---|---|---|
| $10,000 | $0.21 | 4.76 | 4.76 | False | $952,000 | **95.2x** | $1,951,600 | **195.2x** | 1.00% |
| $10,000 | $0.30 | 3.33 | 3.33 | False | $666,000 | **66.6x** | $1,365,300 | **136.5x** | 1.00% |
| $10,000 | $0.50 | 2.00 | 2.00 | False | $400,000 | **40.0x** | $820,000 | **82.0x** | 1.00% |
| $10,000 | $1.00 | 1.00 | 1.00 | False | $200,000 | 20.0x | $410,000 | 41.0x | 1.00% |
| $50,000 | $0.21 | 23.80 | 10.00 | **True** | $2,000,000 | **40.0x** | $4,100,000 | **82.0x** | **0.42%** |
| $50,000 | $0.30 | 16.66 | 10.00 | **True** | $2,000,000 | **40.0x** | $4,100,000 | **82.0x** | **0.60%** |
| $50,000 | $0.50 | 10.00 | 10.00 | False | $2,000,000 | **40.0x** | $4,100,000 | **82.0x** | 1.00% |
| $50,000 | $1.00 | 5.00 | 5.00 | False | $1,000,000 | 20.0x | $2,050,000 | 41.0x | 1.00% |
| $100,000 | $0.21 | 47.61 | 10.00 | **True** | $2,000,000 | **20.0x** | $4,100,000 | **41.0x** | **0.21%** |
| $100,000 | $0.30 | 33.33 | 10.00 | **True** | $2,000,000 | **20.0x** | $4,100,000 | **41.0x** | **0.30%** |
| $100,000 | $0.50 | 20.00 | 10.00 | **True** | $2,000,000 | **20.0x** | $4,100,000 | **41.0x** | **0.50%** |
| $100,000 | $1.00 | 10.00 | 10.00 | False | $2,000,000 | 20.0x | $4,100,000 | 41.0x | 1.00% |

---

### 6.6 Performance by Year (2020–2026 YTD)

*(Ground Truth Source: `followup_analysis_report.json`)*

| Year | $N$ | Avg Stop ($) | Avg Asian Range ($) | Avg Cost ($R$) | Win Rate | Net Expectancy ($R$) | Profit Factor |
|---|---|---|---|---|---|---|---|
| **2020** | 166 | $2.65 | $12.30 | 0.1622R | 18.67% | -0.0452R | 0.95 |
| **2021** | 174 | $2.48 | $8.78 | 0.1674R | 20.69% | -0.2853R | 0.68 |
| **2022** | 185 | $1.95 | $8.93 | 0.1986R | 21.62% | **+0.1300R** | 1.14 |
| **2023** | 166 | $2.31 | $8.00 | 0.1863R | 22.89% | **+0.0345R** | 1.04 |
| **2024** | 171 | $3.00 | $12.36 | 0.1528R | 25.15% | -0.0249R | 0.97 |
| **2025** | 151 | $4.90 | $29.21 | 0.1104R | 15.89% | -0.3590R | 0.61 |
| **2026 (YTD)** | [SEALED] | [SEALED] | [SEALED] | [SEALED] | [SEALED] | **[SEALED]** | [SEALED] |

---

## 7. ATR Definition

Behind the Asian range / ATR buckets reported throughout this study:
- **Timeframe:** Daily bars constructed from M5 intraday bars, session-aligned to 17:00 America/New_York trading-day rollover.
- **Period:** 14 trading days ($N = 14$).
- **True Range (TR):** $\\text{TR}_i = \\max(H_i - L_i, |H_i - C_{i-1}|, |L_i - C_{i-1}|)$
- **ATR Formula:** 14-day simple moving average of True Range: $\\text{ATR}_{14}(d) = \\frac{1}{14} \\sum_{j=0}^{13} \\text{TR}_{d-j}$
- **Asian Range Definition:** Width in dollars formed between 18:00 and 00:00 NY time ($H_{\\text{Asian}} - L_{\\text{Asian}}$).
- **Ratio:** $\\text{Ratio} = \\frac{H_{\\text{Asian}} - L_{\\text{Asian}}}{\\text{ATR}_{14}}$.

---

## 8. Limitations & Methodological Caveats

1. **Small Sample Sizes ($N < 200$):**
   - Every individual instrument in Silver Bullet Limit ($N = 23, 29, 36, 23$) and Market ($N = 57, 53, 62, 49$) has $N < 200$.
   - Pooled Silver Bullet Limit has only $N = 111$.
   - Pre-registered Judas composite variant C1+H1 has $N < 200$ ($N=93$ in 2020–22, $N=114$ in 2023–25).
2. **Micro-Stop Selection Bias:**
   - Apparent gross profitability in unconstrained setups was driven by tiny stops ($< \\$0.60$). Enforcing the broker/spread floor ($0.60) collapses gross edge to $+0.0006R$.
3. **Data Availability:**
   - Free intraday FX data prior to August 2021 is unavailable via automated public APIs without paid subscriptions.

---

## 9. Conclusion & Final Protocol Verdict

### Verdict: **NO EDGE DEMONSTRATED**

1. The historical 11-year run (2015–2025) is an **in-sample / full historical sample backtest** (not an out-of-sample holdout) and shows a **consistently negative net expectancy** ($-0.1792R$ unfiltered; $-0.1660R$ floored; 95% bootstrap CI $[-0.2957, -0.0593]$ strictly below zero).
2. In single-year testing, **2022 was net positive (+0.1300R)**, but this isolated profitable year failed to persist, and overall performance is dominated by cost drag and chop. Under cost-aligned benchmarks, Judas Fade ranks at the **67.1th percentile of Null B**, well below the required 95th percentile threshold.
3. Every pre-registered filter (trend bias, news, higher-timeframe levels, cost thresholds) failed to yield a positive net expectancy in either the validation or test splits.
4. The Silver Bullet strategy demonstrates a net expectancy indistinguishable from zero (Limit Net $+0.0486R$, CI $[-0.22, +0.32]$; Market Net $-0.0015R$, CI $[-0.11, +0.11]$).
5. **Sealed 2026 data remains untouched and unviolated.** Because zero variants met the pre-registered qualification gate, the 2026 dataset was not opened.

---

## 10. One-Command Reproduction

The entire verification harness can be reproduced in a single command:

```bash
uv run python research/verify.py
```

All 532 production engine unit tests remain passing:
```bash
uv run pytest
```
The safety controls proposal test suite passes independently:
```bash
uv run pytest research/proposals/test_safety_controls_patch.py
```
