# Final Research Report: Long-Only Monthly ETF Trend Filter (Faber 10-Month SMA)

## Pre-Registration & Research Protocol
- **Pre-Registration File:** [`research/prereg/etf_trend_v1.md`](file:///c:/Users/Admin/Documents/code/Gbot/.wt/etf-trend/research/prereg/etf_trend_v1.md)
- **SHA-256 Hash:** `56d76443708030fa2519083ba18912f27a702dfbeec5dca0f6e08381e7138411`
- **Full Commit ID:** `d0c291c0cf5b6dacda332bf0e322245efcda3035`
- **Commit Timestamp:** `2026-10-12 15:52:15 +0700`
- **Target Universe:** SPY, EFA, IEF, VNQ, GLD, DBC (Cash Proxy: FRED TB3MS)
- **Sample Period:** 2007-01-01 to 2025-12-31 (19 full calendar years, 228 monthly holding periods)
- **Hard-Stop Enforcement:** Truncated strictly at 2025-12-31; 2026 data remains unloaded.

---

## 1. Executive Summary & Gate Decision Matrix

### **Primary Decision Verdict: PASS** (Pre-Registered CAGR Sharpe Definition)
*Comparative Reference: PARTIAL under Mean Monthly Excess Sharpe Definition*

> *Qualification:* Descriptive historical evidence on ~3-5 distinct bear episodes (2008 GFC, 2011/2015, 2020 Covid, 2022 inflation/rates); not a permanent structural guarantee.

### Primary Gate Evaluation Matrix (Pre-Registered CAGR Definition)
| Gate | Condition Description | Required Threshold | Empirical Value | Verdict |
| :--- | :--- | :--- | :--- | :---: |
| **G1** | Filtered MaxDD $\le 75\%$ of Benchmark (a) | $\le 28.63\%$ | **12.42%** | **PASS** |
| **G2** | Filtered Sharpe $\ge$ Benchmark (a) Sharpe (CAGR def) | $\ge 0.4135$ | **0.5423** (Diff: +0.1289) | **PASS** |
| **G3** | Real Calmar $\ge$ 95th Percentile of Placebo | $\ge 0.3685$ | **0.4032** (97.0th pct) | **PASS** |
| **G4** | G1 & G2 hold in 2007–2015 and 2016–2025 | Both Periods PASS | Period 1: **PASS** | Period 2: **PASS** | **PASS** |

### Dual Sharpe Definition Comparison & Verdict Sensitivity
| Metric / Gate Component | Pre-Registered CAGR Definition | Mean Monthly Excess Definition | Methodological Note / Impact |
| :--- | :---: | :---: | :--- |
| **Full Period Strategy Sharpe (Net)** | **0.5423** | **0.5589** | Formula: $(R_{CAGR} - R_{f,CAGR})/\sigma_{ann}$ vs $\bar{r}_{exc}/\sigma_{m} \times \sqrt{12}$ |
| **Full Period Benchmark (a) Sharpe (Net)** | **0.4135** | **0.4621** | Equal-weight 6 ETFs rebalanced monthly |
| **G2 Point Difference (Strategy - Bench)** | **+0.1289** | **+0.0968** | Both definitions PASS G2 |
| **Sub-Period 1 (2007–2015) Sharpe** | Strat 0.4243 vs Bench 0.2227 | Strat 0.4526 vs Bench 0.2926 | Both PASS G2 in Period 1 |
| **Sub-Period 2 (2016–2025) Sharpe** | Strat **0.6978** vs Bench **0.6841** | Strat **0.6996** vs Bench **0.7015** | CAGR def: **PASS (+0.0137)**<br>Mean excess def: **FAIL (-0.0019)** |
| **Gate G4 Verdict** | **PASS** | **FAIL** | Sub-period consistency gate |
| **Overall Decision Ladder Verdict** | **PASS** | **PARTIAL** | PASS under pre-reg CAGR def; PARTIAL under mean excess def |

### Bootstrap vs Point Sharpe Difference Analysis
- **Point Estimate Difference (Mean Excess):** $\Delta \text{Sharpe}_{\text{point, mean}} = +0.0968$ (0.5589 vs 0.4621)
- **Point Estimate Difference (CAGR Def):** $\Delta \text{Sharpe}_{\text{point, CAGR}} = +0.1289$ (0.5423 vs 0.4135)
- **Bootstrap Mean Difference (Mean Excess):** $\Delta \text{Sharpe}_{\text{mean}} = +0.0498$
- **Bootstrap Median Difference:** $\Delta \text{Sharpe}_{\text{median}} = +0.0542$
- **Bootstrap 12-Month Block 95% CI:** `[-0.2739, +0.3425]` (spans zero)
- **Empirical Attribution of Difference (Measured by Script):**
  1. *Draws containing 2008 crash months:* 1748 of 2,000 draws (87.4%) $\rightarrow$ Mean Sharpe Difference: **+0.0702**.
  2. *Draws omitting 2008 crash months:* 252 of 2,000 draws (12.6%) $\rightarrow$ Mean Sharpe Difference: **-0.0913**.
  3. *Regime Effect:* When the 2008 crisis is absent from resampled blocks, the trend filter lagged buy-and-hold by an average of 0.0913 Sharpe. The 12.6% probability of omitting 2008 and the variable weighting of crisis blocks empirically explains the downward shift from the full-sample point estimate (+0.0968) to the bootstrap mean (+0.0498).
  4. *Non-linear ratio estimator bias (Jensen's inequality):* `UNVERIFIED` (unmeasured theoretical conjecture; cause beyond measured 2008 block omission is unknown).

---

## 2. Core Comparative Performance Table (2007–2025)

| Metric | 10M SMA Trend (Net) | 10M SMA Trend (Gross) | Benchmark (a) EW B&H (Net) | Benchmark (a) EW B&H (Gross) | Benchmark (b) SPY B&H (Net) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **CAGR** | **5.01%** | 5.22% | **6.67%** | 6.71% | **10.66%** |
| **Annualized Volatility** | **6.50%** | 6.50% | **12.53%** | 12.53% | **17.30%** |
| **Sharpe Ratio (Pre-Reg CAGR)** | **0.5423** | 0.5749 | **0.4135** | 0.4168 | **0.5304** |
| **Sharpe Ratio (Mean Excess)** | **0.5589** | 0.5901 | **0.4621** | 0.4652 | **0.5905** |
| **Maximum Drawdown** | **12.42%** | 12.36% | **38.18%** | 38.13% | **52.89%** |
| **Calmar Ratio** | **0.4032** | 0.4223 | **0.1746** | 0.1759 | **0.2016** |
| **Worst Calendar Year** | **2008 (-5.18%)** | -5.04% | **2008 (-20.64%)** | — | **2008 (-34.31%)** |

---

## 3. Placebo Null Model Analysis (2,000 Draws)
- **Null Construction:** 2,000 synthetic simulations matching exact sleeve-specific time in cash and switch counts.
- **Real Strategy Calmar:** **0.4032** $\rightarrow$ **97.0th Percentile** (G3 PASS: exceeds 95th pct 0.3685).
- **Real Strategy Sharpe:** **0.5589** $\rightarrow$ **89.0th Percentile** (Median 0.4152, 95th pct 0.6098).

| Placebo Percentile | Calmar Ratio | Sharpe Ratio |
| :--- | :---: | :---: |
| **50th Percentile (Median)** | 0.1783 | 0.4152 |
| **90th Percentile** | 0.3170 | — |
| **95th Percentile** | 0.3685 | 0.6098 |
| **99th Percentile** | 0.4804 | — |
| **Real Filtered Strategy** | **0.4032 (97.0th pct)** | **0.5589 (89.0th pct)** |

---

## 4. Sleeve Breakdown Table

| Ticker | Fund Name | Time in Market (%) | Months Invested / Cash | Switches / Year | Filtered CAGR (%) | Filtered MaxDD (%) | B&H CAGR (%) | B&H MaxDD (%) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **SPY** | SPY | 78.9% | 180 / 48 | 1.58 | 8.22% | 24.22% | 10.67% | 52.89% |
| **EFA** | EFA | 67.1% | 153 / 75 | 1.79 | 4.00% | 33.07% | 4.48% | 58.96% |
| **IEF** | IEF | 67.5% | 154 / 74 | 2.11 | 2.62% | 13.37% | 3.36% | 22.24% |
| **VNQ** | VNQ | 66.2% | 151 / 77 | 2.00 | 2.84% | 48.31% | 5.01% | 70.39% |
| **GLD** | GLD | 68.0% | 155 / 73 | 1.79 | 7.93% | 37.54% | 10.23% | 42.50% |
| **DBC** | DBC | 53.5% | 122 / 106 | 1.79 | 2.53% | 50.74% | 0.77% | 75.31% |

---

## 5. Annual Performance Table (2007–2025, First-Trading-Day-to-First-Trading-Day Windows)

> *Note on Window Alignment:* Each annual window $Y$ measures portfolio performance from the close of the first trading day of year $Y$ to the close of the first trading day of year $Y+1$ (or 2025-12-31 close for year 2025). This precisely reflects the one-day execution delay on signals generated at calendar month-ends.

| Year Window | 10M SMA Net (%) | 10M SMA Gross (%) | Benchmark (a) EW (%) | Benchmark (b) SPY (%) | Cash (TB3MS) (%) | Annual Turnover |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **2007** | +14.47% | +14.69% | +12.76% | +4.31% | 4.44% | 2.005 |
| **2008** | -5.18% | -5.04% | -20.64% | -34.31% | 1.37% | 1.455 |
| **2009** | +16.21% | +16.36% | +22.53% | +24.74% | 0.15% | 1.330 |
| **2010** | +6.80% | +7.10% | +16.59% | +14.31% | 0.14% | 2.857 |
| **2011** | +1.04% | +1.20% | +5.23% | +2.46% | 0.05% | 1.505 |
| **2012** | +0.21% | +0.45% | +10.74% | +17.10% | 0.09% | 2.454 |
| **2013** | +4.30% | +4.45% | -0.30% | +27.77% | 0.06% | 1.427 |
| **2014** | +4.12% | +4.29% | +2.11% | +14.50% | 0.03% | 1.613 |
| **2015** | -5.10% | -4.90% | -6.25% | -0.13% | 0.05% | 2.059 |
| **2016** | +2.34% | +2.57% | +9.58% | +14.45% | 0.32% | 2.301 |
| **2017** | +7.96% | +8.17% | +12.07% | +21.65% | 0.93% | 1.925 |
| **2018** | -2.51% | -2.31% | -6.40% | -5.15% | 1.96% | 2.081 |
| **2019** | +9.73% | +9.92% | +20.92% | +32.31% | 2.08% | 1.768 |
| **2020** | +5.99% | +6.23% | +8.38% | +15.64% | 0.37% | 2.289 |
| **2021** | +15.97% | +16.18% | +18.40% | +31.26% | 0.04% | 1.812 |
| **2022** | -5.04% | -4.78% | -9.26% | -18.99% | 2.04% | 2.757 |
| **2023** | +1.86% | +2.19% | +10.80% | +26.00% | 5.19% | 3.238 |
| **2024** | +9.57% | +9.76% | +10.78% | +25.28% | 5.08% | 1.682 |
| **2025** | +16.95% | +17.16% | +20.70% | +18.01% | 4.14% | 1.821 |

---

## 6. Report-Only Sensitivities & Variations

| Strategy Variant | CAGR (%) | Annual Vol (%) | Sharpe Ratio | Max Drawdown (%) | Calmar Ratio |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Lookback 8-Month SMA** | 5.29% | 6.59% | 0.5924 | 12.20% | 0.4333 |
| **Lookback 12-Month SMA** | 5.08% | 6.20% | 0.5939 | 9.60% | 0.5294 |
| **12-Month Return Sign (Momentum)** | 5.10% | 7.52% | 0.5040 | 12.85% | 0.3967 |
| **200-Day Daily SMA** | 5.01% | 6.62% | 0.5490 | 13.09% | 0.3825 |
| **Cost Sensitivity: 0 bps (Gross)** | 5.22% | 6.50% | 0.5901 | 12.36% | 0.4223 |
| **Cost Sensitivity: 25 bps** | 4.69% | 6.50% | 0.5121 | 12.52% | 0.3749 |
| **Cash Sensitivity: 0% Cash Yield (TB3MS Hurdle)** | 4.56% | 6.50% | 0.4918 | 12.63% | 0.3609 |
| **Ex-Gold & Commodities (4 ETFs)** | 4.59% | 7.46% | 0.4422 | 13.23% | 0.3469 |

### Extended History (VFINX + VBMFX 1988–2025, 38 Years)
- **Sample Window:** 1988-2025 (38 Years, 456 Months)
- **Filtered 10M SMA (Net):** CAGR **7.33%** | Vol **5.80%** | Sharpe **0.7422** | MaxDD **14.37%** | Calmar **0.5098**
- **50/50 Buy & Hold (Net):** CAGR **8.45%** | Vol **8.30%** | Sharpe **0.6653** | MaxDD **27.83%** | Calmar **0.3036**

---

## 7. Venue Check (Exness MT5 Demo Terminal)

| Target Instrument | MT5 Proxy Symbol | Match Quality | Venue Audit Notes |
| :--- | :--- | :---: | :--- |
| **SPY** | `US500m` | `CLOSE_INDEX_PROXY` | Cash CFD on S&P 500 Index (tracks underlying index without ETF expense ratio). |
| **GLD** | `XAUUSDm` | `CLOSE_COMMODITY_PROXY` | Spot Gold vs USD (tracks physical gold spot price closely). |
| **DBC** | `USOILm` | `UNVERIFIED` | USOILm tracks WTI Crude Oil only. DBC is a diversified basket of 14 energy, metal, and agricultural commodities. Incomplete proxy. |
| **EFA** | `NONE` | `UNVERIFIED` | No direct MSCI EAFE ETF or composite developed ex-US index CFD exists on Exness MT5 terminal. |
| **IEF** | `NONE` | `UNVERIFIED` | No US Treasury bond CFDs or sovereign fixed-income instruments exist on Exness MT5 terminal. |
| **VNQ** | `NONE` | `UNVERIFIED` | No real estate investment trust (REIT) index or ETF CFDs exist on Exness MT5 terminal. |
| **CASH** | `USD Margin Balance` | `UNVERIFIED` | Broker cash margin balance yields 0.0% interest; does not earn risk-free Treasury yield. |

---

## 8. Audit & Verification Trail
1. **Data Scan:** Audited 5,008 to 11,595 daily rows across all instruments. 0 zero prices, 0 NaN values, all price adjustments verified clean.
2. **Hand-Trace Audit:** 30 switch events (5 per sleeve) hand-traced against raw prices; 100% verified bit-for-bit in [`switch_events_hand_trace.csv`](file:///c:/Users/Admin/Documents/code/Gbot/.wt/etf-trend/research/etf_trend/output/switch_events_hand_trace.csv).
3. **Independent Recomputation:** [`verify.py`](file:///c:/Users/Admin/Documents/code/Gbot/.wt/etf-trend/research/etf_trend/verify.py) recomputed every metric and table from saved monthly return CSVs alone; 0 discrepancies found.
4. **Monthly Signals Utility:** [`monthly_signals.py`](file:///c:/Users/Admin/Documents/code/Gbot/.wt/etf-trend/research/etf_trend/monthly_signals.py) generates read-only month-end allocation signals and appends to [`monthly_signals.csv`](file:///c:/Users/Admin/Documents/code/Gbot/.wt/etf-trend/research/etf_trend/output/monthly_signals.csv) with zero live engine side-effects.
5. **List of Unverified Items (`UNVERIFIED`):**
   - EFA direct proxy in Exness MT5 terminal (`UNVERIFIED`).
   - IEF Treasury bond CFD proxy in Exness MT5 terminal (`UNVERIFIED`).
   - VNQ REIT index CFD proxy in Exness MT5 terminal (`UNVERIFIED`).
   - DBC multi-commodity basket representation via single-commodity USOIL (`UNVERIFIED`).
   - Broker cash balance Treasury interest yield (`UNVERIFIED`).
   - Real-world investor tax friction across non-sheltered accounts (`UNVERIFIED`).