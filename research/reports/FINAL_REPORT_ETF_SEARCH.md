# Final Research Report: Systematic Multi-Asset ETF Strategy Search (v1.0)

## Pre-Registration & Version Control Audit
- **Pre-Registration File:** [`research/prereg/etf_search_v1.md`](file:///C:/Users/Admin/Documents/code/Gbot/.wt/etf-search/research/prereg/etf_search_v1.md)
- **Pre-Registration Commit:** `bc08d461c3fab1d35b5a146b729bc157193755b4`
- **Commit Date:** `Mon Oct 12 17:03:59 2026 +0700`
- **Frozen Winner Config:** [`research/etf_search/config/winner_candidate_v1.json`](file:///C:/Users/Admin/Documents/code/Gbot/.wt/etf-search/research/etf_search/config/winner_candidate_v1.json)
- **Common Sample Window:** 2006-02-06 to 2025-12-31 (Evaluation: 2007-03-01 to 2025-12-31)
- **Total Trading Days Audited:** 5,008 days across 12 ETFs (NaNs: False)
- **Out-of-Sample / 2026 Data:** Strictly unloaded.

---

## 1. Executive Summary & Decision Ladder Verdict

### **Overall Decision Verdict: FAIL**

> [!IMPORTANT]
> **Verdict Rationale:** While Candidate `VAR_117` passes standard historical gates G1, G2, G3, G5, and G6, it **FAILS Gate G4 (Deflated Sharpe Ratio = 0.3057 < 0.9500)**. After adjusting for multiple testing across the 294 explored variants and the dispersion of tested Sharpes ($\sigma = 0.1827$), the expected maximum Sharpe under pure selection bias is 1.0386, exceeding the candidate's observed Sharpe (0.9166). Per the pre-registered protocol, any gate failure yields a final verdict of **FAIL**.

### Pre-Registered Gate Evaluation Table
| Gate | Condition Description | Evaluation Window | Required Threshold | Empirical Value | Verdict |
| :--- | :--- | :--- | :--- | :--- | :---: |
| **Gate G1** | Absolute Return Hurdle (Net CAGR > Cash) | 2007-03-01 to 2025-12-31 (18.8y) | $> 1.45\%$ | **11.15\%** (vs Cash: 1.45\%) | **PASS** |
| **Gate G2** | Risk-Adjusted Edge (Net Sharpe CAGR > Bench A) | 2007-03-01 to 2025-12-31 (18.8y) | $> 0.4760$ | **0.9166** (Bench A: 0.4760) | **PASS** |
| | *Dual Metric: Mean Monthly Excess Sharpe* | 2007-03-01 to 2025-12-31 (18.8y) | $> 0.5132$ | **0.9153** (Bench A: 0.5132) | *PASS* |
| **Gate G3** | Downside Protection (Net MaxDD < Bench A) | 2007-03-01 to 2025-12-31 (18.8y) | $< 32.48\%$ | **16.71\%** (Bench A: 32.48\% [2007–2025 window]) | **PASS** |
| **Gate G4** | Multiple Testing Adjustment (Deflated Sharpe Ratio) | 2007-03-01 to 2025-12-31 (227m) | $\ge 0.9500$ ($N=294$) | **0.3057** (z = -0.51) | **FAIL** |
| **Gate G5** | Parameter Plateau Stability (Neighbours Pass G1–G3) | Grid neighbours | $\ge 70.0\%$ | **83.3\%** (5/6 neighbours pass) | **PASS** |
| **Gate G6** | Sub-Period Consistency (Both Halves Pass G1–G3) | P1 (07-16) & P2 (16-25) | P1 & P2 Pass | **P1: PASS** (0.8563), **P2: PASS** (0.9887) | **PASS** |
| **Gate G7** | Forward Paper-Trading Verification (6 Months) | Forward 6-month paper run | 6 Months Logged | **NOT STARTED** | **NOT STARTED** |

> [!NOTE]
> **Sharpe Difference vs Benchmark A Confidence Interval:**
> analytic (Memmel 2003), rho=0.55 (assumed, unverified), T=226, SE 0.2226, CI [0.004, 0.877], p=0.048, conditional on VAR_117 being the best of 294 variants.
> 
> **Turnover & Cost Attribution:**
> Backtest cost code is uncommitted (definition unverified). Two turnover interpretations for annual turnover (3.61x):
> - 3.61x is the cost-bearing sum $|\Delta w|$ per year, giving about 9.3% net CAGR at 50 bps.
> - 3.61x is one-way (half of sum $|\Delta w|$) and costs are per side ($2 \times 3.61x = 7.22x$), giving about 7.3% net CAGR at 50 bps.
> Benchmark A is 6.86% (2007–2025 full sample).


---

## 2. Selected Candidate Strategy: Specification & Performance
- **Variant Identifier:** `VAR_117`
- **Strategy Family:** MomVol (Momentum Rotation with Inverse-Volatility Weighting)
- **Rebalancing Frequency:** Monthly (Signal at month-end close, trade executed at next trading day close)
- **Momentum Lookback ($L$):** 9 Months (189 trading days)
- **Top Assets Selected ($K$):** Top 3 assets ranked by trailing 9-month total return
- **Cash Filter ($CF$):** ON (Assets in Top 3 only held if 9M return > 0; non-qualifying weight held in cash)
- **Volatility Window ($V$):** 12 Months (252 trading days)
- **Weighting Scheme:** Inverse-volatility weighting computed across the selected top 3, then zeroed on failure; failed share held in cash (survivors do not re-expand)

### Full Evaluation Period Performance (2007-03-01 to 2025-12-31, 18.8 Years)
| Metric | Gross (0 bps) | Primary Net (5 bps) | Stress Net (25 bps) | Equal-Weight (Bench A) | 60/40 (SPY/IEF) | Cash (TB3MS) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **CAGR** | 11.35% | **11.15%** | 10.36% | 6.86% | 8.26% | 1.45% |
| **Annualized Volatility** | 10.58% | **10.58%** | 10.60% | 11.36% | 10.26% | 0.00% |
| **Sharpe Ratio (CAGR)** | 0.9358 | **0.9166** | 0.8400 | 0.4760 | 0.6629 | 0.0000 |
| **Sharpe Ratio (Mean Excess)** | 0.9327 | **0.9153** | 0.8459 | 0.5132 | 0.6629 | 0.0000 |
| **Maximum Drawdown** | 16.62% | **16.71%** | 17.04% | 32.48% | 30.27% | 0.00% |
| **Annual One-Way Turnover** | 3.61x | **3.61x** | 3.61x | 0.12x | 0.08x | 0.00x |
| **Zero-Hurdle Sharpe (CAGR)** | 1.0407 | **1.0407** | 1.0407 | 0.6039 | 0.8051 | N/A |

---

## 3. Sub-Period Robustness Analysis (Gate G6)
| Period Window | Sample Role | Candidate CAGR | Candidate Sharpe (CAGR) | Candidate MaxDD | Bench A Sharpe | Bench A MaxDD | Status |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Sub-Period 1 (2007–2016)** | GFC Crisis & Recovery | 9.88% | **0.8563** | **12.53%** | 0.4400 | 32.48% | **PASS** |
| **Sub-Period 2 (2016–2025)** | Tech Expansion & Inflation | 12.56% | **0.9887** | **16.71%** | 0.5332 | 18.00% | **PASS** |

---

## 4. Parameter Plateau Stability (Gate G5)
Immediate parameter neighbours in the grid space around `VAR_117` ($L=9\text{M}, K=3, CF=\text{ON}, V=12\text{M}$):

| Variant | Lookback ($L$) | Top-$K$ | Cash Filter | Vol Window ($V$) | Net CAGR | Net Sharpe | MaxDD | G1–G3 Status |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `VAR_093` | 6M | 3 | ON | 12M | 8.55% | 0.5731 | 19.39% | **PASS** |
| `VAR_111` | 9M | 2 | ON | 12M | 8.64% | 0.5298 | 30.49% | **PASS** |
| `VAR_114` | 9M | 3 | OFF | 12M | 11.04% | 0.9035 | 16.71% | **PASS** |
| `VAR_116` | 9M | 3 | ON | 6M | 11.21% | 0.9160 | 16.69% | **PASS** |
| `VAR_123` | 9M | 4 | ON | 12M | 8.87% | 0.7681 | 13.36% | **PASS** |
| `VAR_141` | 12M | 3 | ON | 12M | 7.01% | 0.4583 | 23.91% | **FAIL** |

- **Neighbour Pass Percentage:** 83.3% (5 of 6 passing all three gates G1–G3; threshold $\ge 70.0\%$) $\rightarrow$ **PASS**

---

## 5. Multiple Testing Adjustment & Deflated Sharpe Analysis (Gate G4)
- **Total Variants Tested ($N$):** 294
- **Grid Sharpe Distribution:** Mean = 0.5108 | Standard Deviation = 0.1827
- **Expected Maximum Sharpe under Null ($E[\max S]$):** 1.0386
- **Candidate Winner Sharpe ($S^*$):** 0.9166
- **Monthly Return Skewness:** -0.1423 | **Kurtosis (Pearson):** 3.7422
- **Sample Length ($T$):** 227 monthly periods
- **Deflated Sharpe Z-Score:** -0.5082
- **Deflated Sharpe Ratio (DSR):** **0.3057** (Threshold required: $\ge 0.9500$)
- **Interpretation:** Because 294 variants were evaluated over the full historical sample, selection bias accounts for an expected maximum Sharpe of 1.0386. The observed winner Sharpe (0.9166) does not exceed this selection expectation at the 95% confidence level.

---

## 6. Sizing Feasibility Check ($1,000 and $10,000 Portfolios)
### Portfolio: $1,000
- **Position Size Range:** $36.20 to $915.20 (Mean position: $329.74)
- **Share Allocation Range:** 0.2212 to 31.7895 shares
- **Fractional Shares Requirement:** Mandatory (positions require sub-unit allocations)
- **Cambodian Resident Broker Feasibility:** Interactive Brokers / retail fractional access status is **UNVERIFIED**

### Portfolio: $10,000
- **Position Size Range:** $362.00 to $9152.00 (Mean position: $3297.39)
- **Share Allocation Range:** 2.2121 to 317.8949 shares
- **Fractional Shares Requirement:** Mandatory (positions require sub-unit allocations)
- **Cambodian Resident Broker Feasibility:** Interactive Brokers / retail fractional access status is **UNVERIFIED**

---

## 7. Rebalance Hand-Traces Against Raw Historical Prices
Five key historical rebalance points audited against daily closes:

### Rebalance: Signal 2007-02-28 $\rightarrow$ Execution 2007-03-01
- **Top 3 Assets Selected:** VNQ, EEM, EFA
  - **VNQ**: 9M Mom = +31.94% | 12M Vol = 15.38% | Target Weight = 38.75% | Exec Price = $35.57 $\rightarrow$ Exit Price = $35.41 (-0.46%)
  - **EEM**: 9M Mom = +16.48% | 12M Vol = 27.93% | Target Weight = 21.33% | Exec Price = $24.32 $\rightarrow$ Exit Price = $26.17 (+7.60%)
  - **EFA**: 9M Mom = +14.81% | 12M Vol = 14.93% | Target Weight = 39.92% | Exec Price = $41.02 $\rightarrow$ Exit Price = $42.76 (+4.24%)
- **Cash Allocation:** 0.00%

### Rebalance: Signal 2008-09-30 $\rightarrow$ Execution 2008-10-01
- **Top 3 Assets Selected:** DBC, TLT, IEF
  - **DBC**: 9M Mom = +7.38% | 12M Vol = 26.83% | Target Weight = 16.78% | Exec Price = $27.55 $\rightarrow$ Exit Price = $20.90 (-24.14%)
  - **TLT**: 9M Mom = +5.03% | 12M Vol = 13.90% | Target Weight = 32.40% | Exec Price = $55.74 $\rightarrow$ Exit Price = $54.17 (-2.82%)
  - **IEF**: 9M Mom = +4.97% | 12M Vol = 8.86% | Target Weight = 50.82% | Exec Price = $57.93 $\rightarrow$ Exit Price = $57.12 (-1.40%)
- **Cash Allocation:** 0.00%

### Rebalance: Signal 2020-02-28 $\rightarrow$ Execution 2020-03-02
- **Top 3 Assets Selected:** GLD, TLT, QQQ
  - **GLD**: 9M Mom = +21.93% | 12M Vol = 12.48% | Target Weight = 36.66% | Exec Price = $149.20 $\rightarrow$ Exit Price = $149.45 (+0.17%)
  - **TLT**: 9M Mom = +21.24% | 12M Vol = 12.63% | Target Weight = 36.22% | Exec Price = $125.73 $\rightarrow$ Exit Price = $136.63 (+8.67%)
  - **QQQ**: 9M Mom = +17.17% | 12M Vol = 16.87% | Target Weight = 27.12% | Exec Price = $208.12 $\rightarrow$ Exit Price = $175.69 (-15.58%)
- **Cash Allocation:** 0.00%

### Rebalance: Signal 2022-03-31 $\rightarrow$ Execution 2022-04-01
- **Top 3 Assets Selected:** DBC, GLD, VNQ
  - **DBC**: 9M Mom = +34.89% | 12M Vol = 22.43% | Target Weight = 24.53% | Exec Price = $22.82 $\rightarrow$ Exit Price = $24.32 (+6.56%)
  - **GLD**: 9M Mom = +8.69% | 12M Vol = 13.52% | Target Weight = 40.69% | Exec Price = $179.50 $\rightarrow$ Exit Price = $173.63 (-3.27%)
  - **VNQ**: 9M Mom = +8.25% | 12M Vol = 15.82% | Target Weight = 34.78% | Exec Price = $92.30 $\rightarrow$ Exit Price = $84.74 (-8.19%)
- **Cash Allocation:** 0.00%

### Rebalance: Signal 2025-11-28 $\rightarrow$ Execution 2025-12-01
- **Top 3 Assets Selected:** GLD, EEM, QQQ
  - **GLD**: 9M Mom = +47.33% | 12M Vol = 19.41% | Target Weight = 34.45% | Exec Price = $389.75 $\rightarrow$ Exit Price = $396.31 (+1.68%)
  - **EEM**: 9M Mom = +26.93% | 12M Vol = 17.80% | Target Weight = 37.58% | Exec Price = $53.24 $\rightarrow$ Exit Price = $54.43 (+2.23%)
  - **QQQ**: 9M Mom = +22.32% | 12M Vol = 23.91% | Target Weight = 27.97% | Exec Price = $614.28 $\rightarrow$ Exit Price = $612.22 (-0.34%)
- **Cash Allocation:** 0.00%

---

## 8. Summary of Unverified Items (`UNVERIFIED`)
1. **Retail Broker Access in Cambodia:** Availability of retail brokerages offering US-listed fractional ETFs and low trading commissions for Cambodian resident accounts (`UNVERIFIED`).
2. **Live Execution Slippage for Fractional ETF Orders:** Actual execution spread and market impact on monthly rebalance executions using retail fractional share orders (`UNVERIFIED`).
3. **Dividend Withholding Tax Friction:** Impact of US non-resident alien dividend withholding tax (typically 30% without tax treaty) on gross yield reinvestment for international accounts (`UNVERIFIED`).