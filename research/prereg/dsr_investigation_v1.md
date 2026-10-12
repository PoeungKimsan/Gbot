# Pre-Registration Document: Deconstruction of Deflated Sharpe Ratio (Gate G4) & Systematic Profitability Investigation for VAR_117

- **Document ID:** `research/prereg/dsr_investigation_v1.md`
- **Target Worktree:** `.wt/etf-search`
- **Target Branch:** `research/etf-search`
- **Date:** 2026-10-12
- **Author:** PoeungKimsan
- **Status:** FROZEN PRE-REGISTRATION

---

## 1. Research Question & Objective

In the primary 294-variant grid search ([FINAL_REPORT.md](file:///c:/Users/Admin/Documents/code/Gbot/.wt/etf-search/research/etf_search/FINAL_REPORT.md)), Candidate `VAR_117` (Monthly Momentum Rotation with Inverse-Volatility Weighting & Cash Filter, $L=9\text{M}, K=3, V=12\text{M}$) achieved:
- Net CAGR (5 bps): **11.15%** (vs Cash: 1.45%, Equal-Weight Benchmark A: 6.86%, 60/40 Benchmark B: 8.26%)
- Net Sharpe (CAGR): **0.9166** (vs Benchmark A: 0.4760, Benchmark B: 0.6629)
- Net Max Drawdown: **16.71%** (vs Benchmark A: 32.48%, Benchmark B: 30.27%)
- Passed Gates G1 (Return > Cash), G2 (Sharpe > Bench A), G3 (MaxDD < Bench A), G5 (Plateau Stability: 83.3%), and G6 (Sub-Period Consistency in both halves).

However, it **FAILED Gate G4** (Deflated Sharpe Ratio, DSR = 0.3057 < 0.9500) under the Bailey & López de Prado (2014) framework because testing $N=294$ variants yields an expected maximum Sharpe of $E[\max S] = 1.0386$ under the null hypothesis of pure selection bias.

The objective of this research is to resolve whether `VAR_117`'s historical outperformance is:
1. **An artifact of multiple testing / data-mining / grid selection**, or
2. **A structural market premium** whose DSR was artificially depressed by the assumption of trial independence and static in-sample parameter selection.

---

## 2. Experimental Batteries & Mathematical Specifications

### Battery 1: Effective Number of Independent Trials ($N_{\text{eff}}$) via Eigenvalue Decomposition
The standard Bailey & López de Prado (2014) formulation assumes independent trials. However, the 294 variants in our grid share identical underlying assets, with adjacent lookbacks having return correlations $> 0.90$.
- **Method:**
  1. Extract monthly return vectors for all $N=294$ variants over the 227-month evaluation period ($227 \times 294$ matrix $\mathbf{R}$).
  2. Compute the Pearson correlation matrix $\mathbf{C} \in \mathbb{R}^{294 \times 294}$.
  3. Perform spectral decomposition: $\mathbf{C} = \mathbf{V} \mathbf{\Lambda} \mathbf{V}^T$, obtaining eigenvalues $\lambda_1 \ge \lambda_2 \ge \dots \ge \lambda_{294}$.
  4. Compute the Effective Number of Independent Trials using three distinct definitions:
     - **Definition A (Kaiser Criterion):** Count of eigenvalues $\lambda_i \ge 1.0$.
     - **Definition B (Participation Ratio / Entropy Dimension):**
       $$N_{\text{eff, PR}} = \frac{(\sum_{i=1}^{294} \lambda_i)^2}{\sum_{i=1}^{294} \lambda_i^2} = \frac{294^2}{\sum_{i=1}^{294} \lambda_i^2}$$
     - **Definition C (95% Variance Explained):** Minimum $k$ such that $\sum_{i=1}^k \lambda_i / \sum_{i=1}^{294} \lambda_i \ge 0.95$.
  5. Re-evaluate $E[\max S]$ and the Deflated Sharpe Ratio under each $N_{\text{eff}}$.

### Battery 2: Walk-Forward Out-of-Sample (WFO) Optimization
To test whether freezing $L=9\text{M}$ over the full 18.8 years was an in-sample cherry-pick:
- **Method:**
  1. Setup an expanding training window starting with 5 years (60 months: 2007–2011).
  2. At the end of each year $Y$ (December close):
     - Evaluate all 294 variants strictly on the historical training set ($t \le Y$).
     - Select the optimal variant using the pre-registered Stage 1 & Stage 2 rules (highest net Sharpe among variants passing CAGR > Cash and MaxDD < Benchmark A).
     - Lock the selected variant for the entirety of year $Y+1$.
  3. Execute that variant out-of-sample for all 12 months of year $Y+1$.
  4. Stitch the out-of-sample monthly returns from 2012-01-01 through 2025-12-31 (14 full out-of-sample years; 168 months).
  5. Compute Out-of-Sample CAGR, Volatility, Sharpe, and MaxDD.
  - **Hypothesis:** If the WFO Sharpe ratio matches or exceeds Benchmark A on purely out-of-sample data, the strategy family possess dynamic predictive validity independent of full-sample lookback selection.

### Battery 3: Monte Carlo Reality Check / Permutation Null Model
Analytical DSR relies on asymptotic extreme value theory. We construct an empirical non-parametric permutation distribution:
- **Method:**
  1. Generate 500 synthetic universes where cross-sectional asset return vectors are permuted across time, destroying serial momentum and trend persistence while preserving contemporaneous cross-asset covariance and volatility.
  2. On each synthetic universe, execute the complete 294-variant grid search.
  3. Record the maximum Sharpe achieved by any variant in that synthetic run: $S_{\max}^{(b)}$ for $b \in \{1, \dots, 500\}$.
  4. Construct the empirical distribution of $S_{\max}$ and compute the empirical p-value of `VAR_117`:
     $$p_{\text{empirical}} = \frac{1}{500} \sum_{b=1}^{500} \mathbf{1}_{\{S_{\max}^{(b)} \ge S_{\text{winner}}\}}$$
  - **Threshold:** Empirical $p < 0.05$ confirms the winner's Sharpe exceeds the 95th percentile of pure data-mining noise.

### Battery 4: Multi-Factor Return Attribution & Component Ablation
Deconstruct the return of `VAR_117` to isolate its structural driver:
- **Part A (Factor Regression):**
  Regress `VAR_117` monthly excess returns against:
  1. Market Equity (SPY excess return)
  2. Fixed Income Duration (IEF excess return)
  3. Equal-Weight Universe (Benchmark A excess return)
  Estimate $\alpha$, $\beta_{\text{SPY}}$, $\beta_{\text{IEF}}$, and $R^2$.
- **Part B (Component Ablation):**
  Simulate 4 variants isolating each mechanical rule:
  1. Full Model: Momentum 9M + Vol Weighting 12M + Cash Filter ON (`VAR_117`)
  2. Ablation 1 (No Cash Filter): Momentum 9M + Vol Weighting 12M + Cash Filter OFF (`VAR_114`)
  3. Ablation 2 (Equal Weighting): Momentum 9M + Equal Weight 1/3 + Cash Filter ON
  4. Ablation 3 (Pure Trend): Equal-weight Trend SMA 10M (`VAR_003`)
  5. Ablation 4 (Pure Buy & Hold): Equal-weight Buy & Hold (Benchmark A)

### Battery 5: Asset Dropout Robustness (Jackknife Sensitivity)
Verify whether the performance relies entirely on a single asset:
- **Method:**
  Run `VAR_117` twelve separate times, dropping one asset from the 12-ETF universe on each run (11-ETF universes: No SPY, No QQQ, No GLD, No DBC, etc.).
  Record CAGR, Sharpe, and MaxDD for each run.
  - **Pass Threshold:** No single asset dropout causes CAGR to fall below Cash or Sharpe to fall below Benchmark A.

### Battery 6: Break-Even Cost & Practical Friction Sweep
Determine the friction tolerance of the strategy:
- **Method:**
  Simulate `VAR_117` under transaction costs ranging from 0 bps to 50 bps per side (in 5 bps increments).
  Identify:
  1. Cost level where Net Sharpe drops below Benchmark A (0.4760).
  2. Cost level where Net CAGR drops below Cash (1.45%).

---

## 3. Decision Framework & Success Criteria

| Test Battery | Diagnostic Question | Quantitative Pass Hurdle |
| :--- | :--- | :--- |
| **Battery 1** | Does correlation correction reduce effective trials ($N_{\text{eff}} \ll 294$)? | $N_{\text{eff}} < 50$; recomputed $\text{DSR}(N_{\text{eff}}) \ge 0.95$ |
| **Battery 2** | Does dynamic walk-forward optimization beat Benchmark A out-of-sample? | OOS Sharpe (2012–2025) > Benchmark A OOS Sharpe |
| **Battery 3** | Does `VAR_117` beat empirical Monte Carlo data-snooping noise? | Empirical permutation $p < 0.05$ |
| **Battery 4** | Does `VAR_117` generate positive statistically significant alpha ($\alpha > 0$)? | $\alpha > 0$ with $t_{\alpha} > 2.0$ against Benchmark A |
| **Battery 5** | Does `VAR_117` survive dropping any single ETF (Jackknife)? | All 12 single-asset dropouts maintain Net Sharpe > Benchmark A |
| **Battery 6** | What is the break-even cost tolerance? | Break-even cost $\ge 25$ bps per side |

### Overall Verdict Criteria:
- **STRUCTURAL EDGE CONFIRMED:** If Batteries 1, 2, 4, and 5 pass, the failure of G4 is confirmed to be an artifact of assuming independent trials in an uncorrected analytical formula, and the strategy framework possesses genuine structural predictive power.
- **SELECTION BIAS CONFIRMED:** If Battery 2 fails (WFO out-of-sample collapses) or Battery 3 fails ($p \ge 0.05$), the strategy's historical performance is confirmed to be an in-sample optimization artifact.

---

## 4. Execution & Operating Rules

1. Zero modifications to live engine (`engine/`, `api/`, `config/`, `journal.db`, PID 10060).
2. All figures produced programmatically by Python scripts reading CSV files; zero hand-typed numbers.
3. Mark unverified assumptions `UNVERIFIED`.
4. Commit pre-registration before running tests.
