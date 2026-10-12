# Pre-Registration: Long-Only Monthly ETF Trend Filter (Faber 10-Month SMA)

**Document:** `research/prereg/etf_trend_v1.md`  
**Registration ID:** `etf_trend_v1`  
**Date & Timestamp:** 2026-10-12T15:55:00+07:00  
**Target Universe:** SPY, EFA, IEF, VNQ, GLD, DBC  
**Cash Proxy:** FRED TB3MS (3-Month Treasury Bill Secondary Market Rate, Monthly)  
**Sample Window:** First common date (~2006/2007) through 2025-12-31  
**Status:** FROZEN BEFORE DATA DOWNLOAD & BACKTEST EXECUTION  

---

## 1. Hypothesis

A 10-month simple moving average (SMA) trend filter (Faber, 2007) applied to a diversified asset-class ETF universe reduces maximum drawdown and does not lower risk-adjusted return (Sharpe ratio) versus a monthly rebalanced equal-weight buy-and-hold benchmark, net of transaction costs.

---

## 2. Universe & Instrument Identification

### 2.1 Target Asset-Class ETFs
The universe is fixed to six broad asset-class ETFs:
1. **SPY** — SPDR S&P 500 ETF Trust (US Large-Cap Equities)
2. **EFA** — iShares MSCI EAFE ETF (International Developed Equities)
3. **IEF** — iShares 7-10 Year Treasury Bond ETF (Intermediate US Sovereign Debt)
4. **VNQ** — Vanguard Real Estate ETF (US Real Estate / REITs)
5. **GLD** — SPDR Gold Shares (Precious Metals / Physical Gold)
6. **DBC** — Invesco DB Commodity Index Tracking Fund (Broad Diversified Commodities)

### 2.2 Cash Proxy
- **FRED TB3MS**: 3-Month Treasury Bill Secondary Market Rate (monthly average yield, discount basis / annual percentage rate).
- Monthly return earned by cash sleeves:
  $$r_{\text{cash}, t} = \frac{\text{TB3MS}_t}{100 \times 12}$$
- Sensitivity analysis includes 0% cash yield.

### 2.3 Data Source & Ingestion Protocol
- **Primary Data Source:** Yahoo Finance / Stooq free daily dividend- and split-adjusted close prices (`Adj Close`).
- **Sample Range:** Inception of the newest fund (DBC inception February 2006, leading to first 10-month SMA signal late 2006 / early 2007) through **2025-12-31**.
- **Hard-Stop Rule:** The data loader strictly truncates at **2025-12-31 23:59:59 UTC**. All data from 2026-01-01 onward remains unloaded to avoid lookahead contamination.
- **Instrument Identity Audit:** For every ticker, print the first and last trading price per year, full fund name, and issuer to prevent ticker ambiguity (e.g., verifying SPY is the S&P 500 ETF and not an unrelated issue).
- **Data Quality Scan:** Audit every series for:
  - Non-positive or zero prices
  - NaN / missing values
  - Stale repeated closes across multiple trading days
  - Missing trading calendar days
  - Dividend and split consistency between unadjusted and adjusted price series

---

## 3. Core Trading Rule & Execution Mechanics

Only ONE primary rule is registered. No parameter optimization is permitted:

1. **Signal Generation (Month-End Close):**
   - At the final trading day's close of month $t$, determine the adjusted close $P_t$ for each ETF sleeve.
   - Compute the 10-month SMA of month-end adjusted closes:
     $$\text{SMA}_{10}(t) = \frac{1}{10} \sum_{i=0}^{9} P_{t-i}$$
   - **Signal Logic:**
     - If $P_t > \text{SMA}_{10}(t)$: Invested (Long ETF).
     - If $P_t \le \text{SMA}_{10}(t)$: Cash (Invested in TB3MS cash yield).

2. **Execution Timing & One-Day Delay:**
   - The signal is evaluated at the close of month-end trading day $T_{\text{signal}}$.
   - **Execution Delay:** Portfolio adjustments (entries, exits, rebalances) take place at the close of the **NEXT trading day** $T_{\text{exec}} = T_{\text{signal}} + 1$.
   - The return of the trade across month $t+1$ is computed from $P_{T_{\text{exec}}}$ to the execution close following the next month-end.

3. **Portfolio Construction & Rebalancing:**
   - Equal sleeve weighting: Fixed at $\frac{1}{6}$ (16.6667%) per sleeve.
   - No leverage ($0.0 \le w_i \le \frac{1}{6}$, $\sum w_i = 1.0$).
   - No shorting.
   - Monthly rebalancing: Each active sleeve is rebalanced back to $\frac{1}{6}$ target weight at each execution date.

---

## 4. Cost Model & Tax Limitations

### 4.1 Transaction Costs
- **Base Cost (Primary):** 10 bps (0.10%) per trade per side on traded notional.
  - Turnover from allocation switches (Cash $\leftrightarrow$ ETF): $\text{Cost} = 0.0010 \times \text{traded value}$.
  - Turnover from monthly rebalancing: $\text{Cost} = 0.0010 \times |\Delta w_i| \times \text{Portfolio Value}$.
- **Cost Sensitivities (Report-Only):**
  - 0 bps (Gross)
  - 25 bps per trade per side

### 4.2 Cash Yield & Sensitivities
- **Primary:** Cash sleeves earn the contemporaneous FRED TB3MS monthly yield.
- **Sensitivity:** 0% cash return (cash earns nothing).

### 4.3 Tax Model
- **Limitation:** All returns are gross of taxes (assumes tax-exempt or retirement account wrapper such as IRA/401k). Tax friction is acknowledged as a real-world limitation.

---

## 5. Benchmarks

1. **Benchmark (a) [Primary]:** Equal-weight buy-and-hold of the identical six ETFs (SPY, EFA, IEF, VNQ, GLD, DBC), rebalanced monthly to $\frac{1}{6}$ weight, incurring 10 bps transaction costs on rebalance turnover.
2. **Benchmark (b) [Context]:** SPY buy-and-hold (100% S&P 500 equity), incurring initial buy cost.

---

## 6. Evaluation Metrics

All metrics reported net of transaction costs (unless explicitly labeled gross):
1. **CAGR**: Compound Annual Growth Rate over the evaluation period.
2. **Annualized Volatility**: $\sigma_{\text{ann}} = \sigma_{\text{monthly}} \times \sqrt{12}$.
3. **Sharpe Ratio**: Annualized excess return over TB3MS divided by annualized volatility:
   $$\text{Sharpe} = \frac{R_{\text{CAGR}} - R_{f, \text{CAGR}}}{\sigma_{\text{ann}}}$$
4. **Maximum Drawdown (MaxDD)**: Peak-to-trough maximum decline on the monthly equity curve.
5. **Calmar Ratio**: $\text{Calmar} = \frac{\text{CAGR}}{|\text{MaxDD}|}$.
6. **Time in Market per Sleeve**: Percentage of months invested in the ETF vs cash.
7. **Switches per Year**: Total state transitions (Cash $\leftrightarrow$ ETF) divided by total years.
8. **Worst Calendar Year Return**: Minimum annual calendar year return.
9. **Annual & Sleeve Breakdown Tables**: Tabulated calendar year returns and individual sleeve metrics.

---

## 7. Placebo Null Model

To verify that the 10-month SMA timing rule possesses genuine timing skill rather than an artifact of reduced market exposure:
- **Draws:** 2,000 synthetic portfolios, fixed random seed (seed = 42).
- **Constrained Randomization:** For each sleeve independently, randomly distribute cash/invested months while strictly preserving:
  1. The exact empirical fraction of time spent in cash.
  2. The exact empirical number of switch transitions (Cash $\leftrightarrow$ ETF).
- **Execution:** Forward simulate all 2,000 placebo portfolios with 10 bps transaction costs and TB3MS cash yield.
- **Output:** Report the empirical percentile of the real strategy's Calmar ratio and Sharpe ratio within the 2,000-draw placebo distribution.

---

## 8. Pre-Registered Decision Gates

The strategy must satisfy ALL FOUR gates simultaneously to achieve a **PASS**:

| Gate | Condition | Description |
| :--- | :--- | :--- |
| **G1** | $\text{MaxDD}_{\text{filtered}} \le 0.75 \times \text{MaxDD}_{\text{Benchmark(a)}}$ | Max drawdown reduced by at least 25% relative to equal-weight B&H. |
| **G2** | $\text{Sharpe}_{\text{filtered}} \ge \text{Sharpe}_{\text{Benchmark(a)}}$ | Net Sharpe ratio equal to or exceeding equal-weight B&H (point estimate). Also report 12-month block-bootstrap 95% CI of the difference $\Delta \text{Sharpe}$. |
| **G3** | $\text{Calmar}_{\text{real}} \ge \text{95th percentile}(\text{Placebo})$ | Timing skill statistically superior to 95% of random matched-exposure placebos ($p \le 0.05$). |
| **G4** | G1 and G2 hold in sub-periods | G1 and G2 must hold independently in both:<br>• **Period 1:** 2007–2015<br>• **Period 2:** 2016–2025 |

### Decision Ladder
- **PASS**: G1, G2, G3, and G4 all pass.
- **PARTIAL**: G1 and G2 pass, but G3 or G4 fails.
- **FAIL**: G1 or G2 fails.

### Epistemic Qualification
The evaluation sample spans approximately 3–5 distinct macroeconomic bear/drawdown episodes (2008 GFC, 2011 US debt downgrade/Euro crisis, 2015–2016 commodity collapse, 2020 Covid crash, 2022 inflation/rates bear market). Consequently, any PASS constitutes descriptive historical evidence on a small sample of independent crises, not mathematical proof of permanent structural edge.

---

## 9. Report-Only Explorations (Never Decision Gates)

All secondary analyses are strictly report-only and cannot alter the primary PASS/PARTIAL/FAIL verdict:
1. **Alternative Lookbacks:** 8-month SMA and 12-month SMA.
2. **Absolute Momentum:** 12-month return sign rule ($P_t > P_{t-12}$).
3. **Daily SMA:** 200-day daily SMA evaluated at month-end, executed at next day's close.
4. **Cost & Cash Sensitivities:** 0 bps costs, 25 bps costs, and 0% cash yield.
5. **Asset-Class Subsets:** Ex-Gold and Ex-Commodities (SPY, EFA, IEF, VNQ equal-weight $\frac{1}{4}$ each).
6. **Extended History Pre-2007:** If free historical data exists back before 2007 (VFINX for S&P 500, VBMFX for US Aggregate Bonds, TB3MS for cash), evaluate the 10-month SMA on that subset across 1987–2025.

---

## 10. Execution Venue Check (Report-Only)

Audit available symbols in the Exness MT5 demo terminal to identify matching or proxy instruments for live execution (e.g., US500 for SPY, XAUUSD for GLD, USOIL/XTIUSD for DBC, bond/REIT CFDs). Document exact contract specifications and write `UNVERIFIED` for any instrument without a direct 1:1 match.

---

## 11. Audit & Verification Protocol

1. **Hand-Trace Audit:** Explicitly hand-trace 5 distinct switch events per sleeve (30 total events) directly against raw price data:
   - Signal month-end date and close price
   - Calculated 10-month SMA value
   - Execution date and execution price
   - Subsequent holding return
2. **Recomputation Script (`verify.py`):** Standalone verification script that loads ONLY the saved monthly return files and independently recomputes all summary metrics and tables.
3. **Automated Final Report Generator:** All tables, metrics, and markdown outputs in `FINAL_REPORT.md` are compiled programmatically by script with zero hand-typed values. An automated consistency assertion fails if any discrepancy is detected.
4. **Unverified Register:** Any metric, venue property, or data point not independently verified is explicitly cataloged as `UNVERIFIED`.
5. **Bug & Re-Run Policy:** Any bug fix requires re-running the entire evaluation battery with an incremented run counter logged in `variants_tried.csv`.
