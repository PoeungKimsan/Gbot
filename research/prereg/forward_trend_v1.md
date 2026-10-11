# PRE-REGISTRATION: Forward Paper-Trading Trend Test (`forward_trend_v1`)

**Registration Version:** `forward_trend_v1`  
**Registration Date:** 2026-10-11  
**Git Branch:** `trend-forward`  
**Baseline Research Protocol:** `trend_v1.2` (`f1affec`, SHA-256 `d8330268db3e5e0115751e4d2a7c1fc06c01451448d473bc9cca35f45dd075c3`)  
**Status:** FROZEN PRIOR TO FORWARD RUNNER EXECUTION

---

## 1. Objective & Hypothesis

Evaluate the forward, out-of-sample execution fidelity and performance of the two surviving trend rules from `trend_v1.2` (**T2 50/200 SMA Cross** and **T3 12-Month TSMOM**) across the identical 12-market liquid universe under a standardized forward paper-trading harness.

Hypothesis: Forward paper execution of T2 and T3 generates returns consistent with the gross-of-financing historical distributions, subject to realistic spread, slippage, and broker financing carry terms.

---

## 2. Frozen Strategy Rules & Source Citations

The forward signal engine imports the exact pre-registered signal functions from `research/trend/strategies.py` and `indicators.py` without modification or re-implementation:

1. **Rule T2: 50/200 SMA Cross (`run_t2_sma_cross` in `research/trend/strategies.py`):**
   - Lookback: 50-day Simple Moving Average vs 200-day Simple Moving Average.
   - Entry: Bullish cross ($\text{SMA}_{50}[i-1] > \text{SMA}_{200}[i-1]$ and $\text{SMA}_{50}[i-2] \le \text{SMA}_{200}[i-2]$) enters LONG on bar $i$ Open. Bearish cross enters SHORT on bar $i$ Open.
   - Initial Stop Loss: $3.0 \times \text{ATR}_{20}$ from entry price.
   - Stop Exit: If stopped out, position exits immediately at stop price (or Open if gapped). No re-entry permitted until the opposite cross occurs, resetting the regime.
   - Reversal Exit: If held until an opposite SMA cross, position closes at next bar Open and immediately reverses into the opposite direction.

2. **Rule T3: 12-Month Time-Series Momentum (`run_t3_tsmom` in `research/trend/strategies.py`):**
   - Lookback: 252-day price return evaluated on month-end closes.
   - Entry: On the first trading day of the new month (next bar Open), if 252-day return $> 0$, enter LONG; if $< 0$, enter SHORT.
   - Episode Invariant: Exactly 1 trade per multi-month episode. If the sign persists across subsequent month-ends, the existing position is maintained.
   - Initial Stop Loss: $3.0 \times \text{ATR}_{20}$ from original entry price.
   - Stop Exit: If stopped out, position remains flat until a month-end re-evaluation occurs.
   - Reversal Exit: When month-end return sign flips, close at next bar Open and reverse.

*Rule Source File SHA-256 Hashes:*
- `research/trend/strategies.py`: Frozen under commit `f1affec`
- `research/trend/indicators.py`: Frozen under commit `f1affec`
- `research/trend/costs.py`: Frozen under commit `f1affec`

---

## 3. Fixed Universe (12 Markets)

Identical to `trend_v1.2` candidate universe without narrowing:
1. **Metals:** `XAUUSD` (Gold), `XAGUSD` (Silver)
2. **Equities:** `US500` (S&P 500), `GER40` (DAX 40), `NDX` (NASDAQ-100)
3. **FX:** `EURUSD`, `GBPUSD`, `USDJPY`, `AUDUSD`, `USDCAD`, `USDCHF`
4. **Energy:** `Brent` (Brent Crude Oil)

---

## 4. Cost & Execution Model

1. **Spread:** Baseline instrument spread multiplied by $1.5\times$ conservatism buffer:
   - `XAUUSD`: $0.375 \times 1.5 = 0.5625$ pts
   - `XAGUSD`: $0.030 \times 1.5 = 0.0450$ pts
   - `EURUSD`, `GBPUSD`, `USDCHF`: $0.00015 \times 1.5 = 0.000225$ pts
   - `USDJPY`: $0.015 \times 1.5 = 0.0225$ pts
   - `AUDUSD`, `USDCAD`: $0.00020 \times 1.5 = 0.000300$ pts
   - `US500`: $0.50 \times 1.5 = 0.75$ pts
   - `GER40`: $1.50 \times 1.5 = 2.25$ pts
   - `Brent`: $0.040 \times 1.5 = 0.060$ pts
   - `NDX`: $1.50 \times 1.5 = 2.25$ pts
2. **Stop Slippage:** $0.5 \times \text{Spread}$ additional penalty on stop executions.
3. **Financing Carry Logging:**
   - Logged concurrently at **0% financing** and **2% p.a. financing**.
   - Broker swap rates logged once `forward/swaps.csv` exists. Until provided by the user, broker swap is recorded as `NaN` (no rates are ever invented).
4. **Notional R Units:**
   - All performance is logged purely in $R$ units ($1R \equiv \text{Initial Stop Distance} \times \text{Position Size}$, notionally 1.0% equity risk). No dollar/euro currency exposure claims.

---

## 5. Startup & Flat-Start Policy

- **Flat Start Invariant:** The forward paper test begins with zero active positions.
- **Carried Positions Handling:** Any position indicated as already active under historical simulation on the start date is routed to a separate `carried_trades` table in `forward/forward_trend.db`.
- **Exclusion from Statistics:** Carried positions are strictly excluded from all forward performance statistics, win rates, mean $R$, and confidence intervals. Only fresh trades initiated on or after the start date are evaluated.

---

## 6. Statistical Power Calculation & Success Criterion

### 6.1 Power Calculation (Derived from 2002–2025 Historical Data)

Sample size $N$ needed for the 95% confidence interval half-width ($1.96 \times \sigma / \sqrt{N}$) to fall below the net mean $R$ ($\mu$), ensuring the lower bound of the CI is strictly positive ($CI_{\text{lower}} > 0$):
$$N_{\text{needed}} \ge \left( 1.96 \times \frac{\sigma}{\mu} \right)^2$$

Historical parameters across 12 markets (23.2 years, ~16.2 trades/year):
1. **Rule T2 (SMA Cross):**
   - At 0% Financing: $\mu = +0.5652R, \sigma = 4.0076R \implies N_{\text{needed}} = \mathbf{193.2 \text{ trades}}$ ($\mathbf{11.98 \text{ years}}$)
   - At 2% Financing: $\mu = +0.2777R, \sigma = 3.7407R \implies N_{\text{needed}} = \mathbf{697.0 \text{ trades}}$ ($\mathbf{43.24 \text{ years}}$)
2. **Rule T3 (TSMOM):**
   - At 0% Financing: $\mu = +0.7662R, \sigma = 5.4832R \implies N_{\text{needed}} = \mathbf{196.7 \text{ trades}}$ ($\mathbf{12.11 \text{ years}}$)
   - At 2% Financing: $\mu = +0.4531R, \sigma = 5.0576R \implies N_{\text{needed}} = \mathbf{478.7 \text{ trades}}$ ($\mathbf{29.46 \text{ years}}$)

### 6.2 Power Mandate: `[LOW N / UNDERPOWERED]` Labeling
Because any 1-year or 2-year forward test will yield $N \approx 16 \text{ to } 32 \ll 193$ trades, quarterly reports **must explicitly label all forward samples as `[LOW N / UNDERPOWERED]`**. Low $N$ shall not be construed as evidence against edge, but rather insufficient statistical sample size to reject $H_0$.

### 6.3 Proposed Success Criteria (Subject to User Approval Before Start)
1. **Execution Fidelity Criterion:** 100% adherence to frozen signal rules, zero lookahead bias (verified by daily replay tests), zero missing stop losses, and complete bar completion auditing.
2. **Sequential Drift Envelope Criterion:** Cumulative Net $R$ gross-of-financing remains within the 95% empirical bootstrap envelope of historical trade $R$, with Mean Net $R \ge 0.0R$ once $N \ge 30$.
3. **Risk Containment Criterion:** Maximum forward portfolio drawdown does not exceed $15.0R$ (well above historical 0% max DD of $8.39R$ and 2% max DD of $11.05R$, protecting capital against regime breaks).

---

## 7. Change Control & Invariants

1. **No Mid-Test Alterations:** Parameters, lookback lengths, universe selection, and exit rules are permanently locked. Any change immediately terminates `forward_trend_v1` and requires a new pre-registration and a restarted clock.
2. **Reporting Cadence:** Quarterly descriptive reporting. No claims of statistical significance without passing the $N \ge 193$ power threshold.
3. **Safety & Isolation:** Runner code lives strictly in `forward/` and operates on `forward/forward_trend.db`. Live trading engine (`engine/`) and production journal (`journal.db`) remain untouched.
