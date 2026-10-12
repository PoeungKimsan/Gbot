# Pre-Registration Document: Systematic Multi-Asset ETF Strategy Search (v1.0)

- **Document ID:** `research/prereg/etf_search_v1.md`
- **Status:** FROZEN PRE-REGISTRATION
- **Date:** 2026-10-12
- **Author:** PoeungKimsan
- **Target Worktree:** `.wt/etf-search`
- **Target Branch:** `research/etf-search`

---

## 1. Research Objective & Scope

The purpose of this study is to perform a pre-registered, fully enumerated grid search across long-only systematically rebalanced ETF portfolios to determine whether an algorithmic rule can, **net of transaction costs**:
1. Outperform risk-free cash (US 3-Month Treasury Bills, FRED `TB3MS`) in compound annual growth rate (CAGR),
2. Produce a superior risk-adjusted return (Sharpe ratio) relative to an equal-weight buy-and-hold benchmark of the same asset universe, and
3. Suffer a lower maximum peak-to-trough drawdown than that equal-weight benchmark,
4. Maintain statistical significance after adjusting for multiple testing (Deflated Sharpe Ratio $\ge 0.95$),
5. Exhibit parameter plateau stability across neighbouring grid cells,
6. Persist across distinct market regimes (split-sample sub-periods), and
7. Run flawlessly in forward paper trading for 6 months without operational failure or rule alteration.

All investigations are strictly confined to paper trading and historical research. No live trading orders will be sent, and no files in `engine/`, `api/`, `config/`, or `journal.db` will be modified or inspected.

---

## 2. Asset Universe & Data Specifications

### 2.1 ETF Universe
The universe consists of 12 highly liquid, low-cost US exchange-traded funds representing broad asset classes (Equities, Fixed Income, Real Estate, Commodities) accessible to retail accounts supporting fractional shares:

| Symbol | Asset Class | Sub-Segment | Inception Date | First Common Trade Date |
| :--- | :--- | :--- | :---: | :---: |
| **SPY** | US Equities | S&P 500 Large Cap | 1993-01-22 | 2006-02-06 |
| **QQQ** | US Equities | Nasdaq 100 Growth/Tech | 1999-03-10 | 2006-02-06 |
| **IWM** | US Equities | Russell 2000 Small Cap | 2000-05-22 | 2006-02-06 |
| **EFA** | International Equities | MSCI EAFE Developed ex-US | 2001-08-14 | 2006-02-06 |
| **EEM** | International Equities | MSCI Emerging Markets | 2003-04-07 | 2006-02-06 |
| **IEF** | Fixed Income | 7–10 Year US Treasury | 2002-07-22 | 2006-02-06 |
| **TLT** | Fixed Income | 20+ Year US Treasury | 2002-07-22 | 2006-02-06 |
| **SHY** | Fixed Income | 1–3 Year US Treasury | 2002-07-22 | 2006-02-06 |
| **LQD** | Fixed Income | Investment Grade Corporate Bonds | 2002-07-22 | 2006-02-06 |
| **VNQ** | Real Estate | US Real Estate Investment Trusts | 2004-09-23 | 2006-02-06 |
| **GLD** | Commodities | Physical Gold | 2004-11-18 | 2006-02-06 |
| **DBC** | Commodities | Broad Commodity Index Tracking | 2006-02-03 | 2006-02-06 |

### 2.2 Risk-Free Cash Proxy
- **Source:** Federal Reserve Bank of St. Louis (FRED) series `TB3MS` (3-Month Treasury Bill Secondary Market Rate, Monthly, percent per annum).
- Cash yields are prorated to the rebalance frequency:
  - Monthly: $r_{\text{cash}, m} = \frac{\text{TB3MS}_m}{100 \times 12}$
  - Weekly: $r_{\text{cash}, w} = \left(1 + \frac{\text{TB3MS}_w}{100}\right)^{1/52} - 1$

### 2.3 Evaluation Windows
- **Warmup Period:** 2006-02-06 through 2007-02-28 (12 months required for the maximum 12-month lookback).
- **Primary Evaluation Period:** 2007-03-01 through 2025-12-31 (18.8 calendar years; 226 full monthly periods).
- **Sub-Period 1 (Crisis & Recovery):** 2007-03-01 to 2016-07-31 (113 months).
- **Sub-Period 2 (Late Expansion & Inflation Shock):** 2016-08-01 to 2025-12-31 (113 months).
- **Out-of-Sample / 2026 Data:** Strictly forbidden and left unloaded during historical search.

---

## 3. Systematic Strategy Families & Grid Space

The search explores 5 distinct rule families across 2 rebalancing frequencies, yielding exactly **294 variants**:

### 3.1 Family 1: Trend Filter (Faber SMA)
- **Concept:** Invest equally in all universe assets currently trading above their Simple Moving Average (SMA). Assets below SMA allocate their weight to cash.
- **Parameters:** Lookback $L \in \{6, 8, 10, 12\}$ months.
- **Weights:** $w_i = \frac{1}{N} \cdot \mathbf{1}_{\{P_{i, t} > \text{SMA}_i(L)\}}$ where $N=12$. Unallocated weight $\left(1 - \sum w_i\right)$ is held in cash.
- **Sub-total:** 4 variants per frequency.

### 3.2 Family 2: Momentum Rotation (Relative Strength)
- **Concept:** Rank all 12 assets by past $L$-month cumulative total return. Select the top $K$ assets and hold equal weight ($1/K$ each).
- **Parameters:**
  - Lookback $L \in \{3, 6, 9, 12\}$ months.
  - Top-$K \in \{1, 2, 3, 4\}$ assets.
  - Cash Filter $\in \{\text{ON}, \text{OFF}\}$.
    - When Cash Filter is ON: Asset in top-$K$ is only held if its $L$-month return $> 0$. If negative, that asset's $1/K$ share is placed in cash.
    - When Cash Filter is OFF: Top-$K$ assets are held unconditionally.
- **Sub-total:** $4 \times 4 \times 2 = 32$ variants per frequency.

### 3.3 Family 3: Volatility Weighting (Inverse Volatility, No Timing)
- **Concept:** Hold all 12 assets with weights inversely proportional to trailing historical volatility. 100% invested (no cash allocation).
- **Parameters:** Volatility lookback window $V \in \{3, 6, 12\}$ months.
- **Weights:** $w_i = \frac{1/\sigma_i(V)}{\sum_{j=1}^{12} 1/\sigma_j(V)}$.
- **Sub-total:** 3 variants per frequency.

### 3.4 Family 4: Trend + Volatility Weighting
- **Concept:** Filter assets by trend ($P_i > \text{SMA}_i(L)$). Among qualified assets, allocate weight inversely proportional to volatility $\sigma_i(V)$.
- **Parameters:** $L \in \{6, 8, 10, 12\}$ months $\times V \in \{3, 6, 12\}$ months.
- **Weights:** For qualified set $Q = \{i : P_{i,t} > \text{SMA}_i(L)\}$, $w_i = \frac{1/\sigma_i(V)}{\sum_{j \in U} 1/\sigma_j(V)}$ (where $U$ is the full 12-asset universe). Residual weight $\left(1 - \sum_{i \in Q} w_i\right)$ is held in cash.
- **Sub-total:** $4 \times 3 = 12$ variants per frequency.

### 3.5 Family 5: Momentum + Volatility Weighting
- **Concept:** Select top-$K$ assets by momentum lookback $L$. Weight selected assets inversely proportional to trailing volatility $\sigma_i(V)$.
- **Parameters:** $L \in \{3, 6, 9, 12\} \times K \in \{1, 2, 3, 4\} \times \text{Cash Filter} \in \{\text{ON}, \text{OFF}\} \times V \in \{3, 6, 12\}$.
- **Weights:** Among selected set $S$, $w_i = \frac{1/\sigma_i(V)}{\sum_{j \in S} 1/\sigma_j(V)}$. If Cash Filter is ON and asset return $\le 0$, that asset's share goes to cash.
- **Sub-total:** $4 \times 4 \times 2 \times 3 = 96$ variants per frequency.

### 3.6 Rebalance Frequencies
- **Monthly:** Evaluated on the final trading day of each calendar month. Trades executed at the close of the next trading day (first trading day of following month).
- **Weekly:** Evaluated on the final trading day of each week (typically Friday close). Trades executed at the close of the next trading day (typically Monday close).
- Weekly lookback mappings:
  - 3 months = 13 weeks (63 trading days)
  - 6 months = 26 weeks (126 trading days)
  - 8 months = 35 weeks (168 trading days)
  - 9 months = 39 weeks (189 trading days)
  - 10 months = 43 weeks (210 trading days)
  - 12 months = 52 weeks (252 trading days)

### 3.7 Grid Size Summary
$$\text{Total Variants} = (4 + 32 + 3 + 12 + 96) \times 2 = 147 \times 2 = \mathbf{294 \text{ variants}}$$

---

## 4. Execution Mechanics & Cost Modeling

### 4.1 Strict Lookahead Prevention
- **Signal Date ($t$):** Computed strictly using data available up to and including the close of day $t$.
- **Execution Date ($t+1$):** Rebalance trades occur at the closing price of day $t+1$.
- Intra-day rebalance drift between day $t$ close and day $t+1$ close is explicitly modeled.

### 4.2 Portfolio Constraints
- Long-only: $w_{i, t} \ge 0$ for all $i$.
- No leverage: $\sum_{i=1}^{12} w_{i, t} \le 1.0$. Cash weight $w_{\text{cash}, t} = 1.0 - \sum_{i=1}^{12} w_{i, t} \ge 0$.

### 4.3 Transaction Costs & Turnover
Turnover is defined as half the sum of absolute rebalance weight adjustments:
$$\text{Turnover}_t = \frac{1}{2} \left( \sum_{i=1}^{12} |w_{i, t} - w_{i, t}^-| + |w_{\text{cash}, t} - w_{\text{cash}, t}^-| \right)$$
where $w_{i, t}^-$ represents the drifted asset weight prior to rebalancing:
$$w_{i, t}^- = \frac{w_{i, t-1} (1 + R_{i, t-1 \to t})}{1 + R_{\text{portfolio}, t-1 \to t}}$$

**Cost Tiers:**
1. **Primary Model:** 0 commission + 5 basis points ($0.0005$) per side of one-way turnover.
2. **Stress Model:** 0 commission + 25 basis points ($0.0025$) per side of one-way turnover.
3. **Gross Baseline:** 0 basis points (for reporting gross vs net impact).

---

## 5. Benchmarks

1. **Benchmark (a) — Equal-Weight Buy-and-Hold:** Equal weight ($1/12 \approx 8.33\%$) in each of the 12 ETFs, rebalanced at the strategy's rebalancing frequency, paying identical transaction costs.
2. **Benchmark (b) — 60/40 Classic:** 60% SPY / 40% IEF, rebalanced at the strategy's frequency, paying identical transaction costs.
3. **Benchmark (c) — Risk-Free Cash:** 100% investment in rolling 3-Month US T-Bills (`TB3MS`).

---

## 6. Performance Metrics & Dual Sharpe Definitions

### 6.1 Compound Annual Growth Rate (CAGR)
$$\text{CAGR} = \left(\frac{V_{\text{final}}}{V_{\text{initial}}}\right)^{1 / Y} - 1$$
where $Y$ is the total fraction of calendar years.

### 6.2 Maximum Drawdown (MaxDD)
$$\text{MaxDD} = \max_{\tau \le t} \left( \frac{\text{Peak}_\tau - V_t}{\text{Peak}_\tau} \right)$$

### 6.3 Dual Sharpe Ratio Reporting
Both Sharpe definitions must be computed and reported across all strategy and benchmark comparisons:
1. **Pre-Registered CAGR Definition:**
   $$\text{Sharpe}_{\text{CAGR}} = \frac{\text{CAGR}_{\text{strategy}} - \text{CAGR}_{\text{cash}}}{\sigma_{\text{ann}}}$$
   where $\sigma_{\text{ann}} = \sigma_{\text{period}} \times \sqrt{12}$ (monthly) or $\sigma_{\text{period}} \times \sqrt{52}$ (weekly).
2. **Mean Periodic Excess Return Definition:**
   $$\text{Sharpe}_{\text{Mean}} = \frac{\text{Mean}(R_{\text{strategy}} - R_{\text{cash}}) \times F}{\sigma_{\text{period}} \times \sqrt{F}}$$
   where $F=12$ (monthly) or $F=52$ (weekly).

Both metrics are reported side-by-side. Primary evaluation gates utilize the Pre-Registered CAGR definition.

---

## 7. Candidate Selection Protocol

The candidate strategy will be selected from the 294 variants via a deterministic, pre-registered two-stage filter:

1. **Stage 1 (Viability Filter):** Retain only variants that simultaneously satisfy:
   - $\text{Net CAGR} > \text{Cash CAGR (TB3MS)}$
   - $\text{Net MaxDD} < \text{Benchmark (a) MaxDD}$
2. **Stage 2 (Objective Selection):** From the qualified subset, select the single variant with the **Highest Primary Net Sharpe Ratio** ($\text{Sharpe}_{\text{CAGR}}$).
3. If no variant passes Stage 1, the search halts immediately with verdict **FAIL (NO QUALIFIED CANDIDATE)**.

---

## 8. Evaluation Gates for Selected Candidate

The selected candidate must satisfy all seven pre-registered gates to achieve a passing status:

| Gate | Criterion Description | Threshold / Condition |
| :--- | :--- | :--- |
| **G1** | Absolute Return Hurdle | $\text{Net CAGR}_{\text{winner}} > \text{CAGR}_{\text{cash}}$ (Net of 5 bps costs) |
| **G2** | Risk-Adjusted Edge | $\text{Net Sharpe}_{\text{CAGR, winner}} > \text{Sharpe}_{\text{CAGR, Benchmark A}}$ |
| **G3** | Downside Protection | $\text{Net MaxDD}_{\text{winner}} < \text{MaxDD}_{\text{Benchmark A}}$ |
| **G4** | Multiple Testing Adjustment | **Deflated Sharpe Ratio (DSR) $\ge 0.95$**, evaluated across $N=294$ trials, taking into account the variance of tested Sharpes, sample length $T$, return skewness, and kurtosis (Bailey & López de Prado, 2014) |
| **G5** | Parameter Plateau Stability | At least **70% of immediate adjacent grid neighbours** must also pass G1, G2, and G3 |
| **G6** | Sub-Period Consistency | G1, G2, and G3 must independently hold in **both Sub-Period 1 (2007–2016)** and **Sub-Period 2 (2016–2025)** |
| **G7** | Forward Paper-Trading Verification | 6 consecutive months of forward paper trading executed with zero code changes, matching the frozen specification |

### Decision Ladder
- **PASS:** All gates G1 through G7 are satisfied.
- **HISTORICAL-ONLY:** Gates G1 through G6 pass; Gate G7 (forward paper trading) is pending execution.
- **FAIL:** Any gate G1 through G6 fails.

---

## 9. Sizing Feasibility Check ($1,000 and $10,000)

The candidate strategy must be simulated on:
- Portfolio A: \$1,000 initial equity
- Portfolio B: \$10,000 initial equity

Checks must verify:
- Maximum number of simultaneous positions.
- Minimum dollar allocation per asset.
- Impact of fractional share rounding (to 0.001 shares) versus ideal continuous weights.
- Flag any broker availability constraints for Cambodian residents as `UNVERIFIED`.

---

## 10. Research Integrity & Invariants

1. **No Retrospective Parameter Tweaking:** The 294 variants and gate thresholds are frozen upon commit. Any bug discovery requires bumping `run_counter` and re-running the complete suite.
2. **Complete Variant Accounting:** Every executed grid cell must be recorded in `variants_tried.csv`.
3. **Programmatic Reporting:** All summary reports and tables must be generated directly by Python scripts reading output files. Zero hand-typed numbers.
4. **No Financial Opinion:** Empirical findings will be stated neutrally. Any unexplained divergence will be recorded as "cause unknown (UNVERIFIED)".
5. **No Production Interference:** Files in `engine/`, `api/`, `config/`, and `journal.db` remain untouched.
