# PRE-REGISTRATION: Trend-Following Strategy Validation on MT5 Broker Data (`trend_v1.3`)

**Registration Version:** `trend_v1.3`  
**Registration Date:** 2026-10-12  
**Git Branch:** `trend-forward`  
**Prior Pre-Registrations:**  
- `trend_v1`: `0ed2400` (SHA-256 `6bc1c9c9ae10...`)  
- `trend_v1.1`: `b14b541` (SHA-256 `766bfbbe85d1...`)  
- `trend_v1.2`: `f1affec` (SHA-256 `d8330268db3e5e0115751e4d2a7c1fc06c01451448d473bc9cca35f45dd075c3`)  
- `forward_trend_v1.2`: `4f68a3d`  
**Status:** FROZEN BEFORE EVALUATION  

---

## 1. Motivation & Scope

Prior evaluations relied on synthetic/Yahoo historical daily bars which exhibited severe data anomalies (specifically, 67% to 91% `Close == Open` flat bars in FX pairs during 2022–2025) and generic 2.0% annual financing approximations.

This pre-registration amends the research framework to evaluate **T2 (50/200 SMA Cross)** and **T3 (12-Month TSMOM)** directly on **MetaTrader 5 broker historical D1 bars** (2018–2026, 8-year span) with broker-audited contract specifications and point-based financing swaps (`SWAP_BY_POINTS`).

---

## 2. Universe & Data Source

- **Data Source:** MetaTrader 5 terminal (`C:\Program Files\MetaTrader 5 EXNESS`), stored in `research/trend/data_mt5/`.
- **Instruments (12 Markets):**
  - Metals: `XAUUSD` (XAUUSDm), `XAGUSD` (XAGUSDm)
  - Commodities: `Brent` (UKOILm)
  - Equities: `US500` (US500m), `NDX` (USTECm), `GER40` (DE30m)
  - Forex: `EURUSD` (EURUSDm), `GBPUSD` (GBPUSDm), `USDJPY` (USDJPYm), `AUDUSD` (AUDUSDm), `USDCAD` (USDCADm), `USDCHF` (USDCHFm)
- **Bar Invariant:** Only closed D1 bars are processed. Forming intraday bars are strictly dropped.

---

## 3. Cost & Execution Model

1. **Broker Point Swaps:**
   Exact financing drag is computed per side per day using the broker's point swap schedule:
   $$\text{Swap Drag (Points)} = |\text{swap\_points}| \times \text{point\_size} \times \text{holding\_days}$$
2. **Spread & Slippage:**
   Frozen baseline spread table with $1.5\times$ safety buffer, plus $0.5\times \text{spread}$ slippage on stop-loss fills.
3. **Execution Fills:**
   - Entry on the Open of the bar following signal confirmation.
   - Fixed Stop-Loss at $3.0\times \text{ATR}_{20}$ from entry price.
   - Short stop fill rule: fill at Open if $\text{Open} \ge \text{Stop}$, else at Stop.
   - Long stop fill rule: fill at Open if $\text{Open} \le \text{Stop}$, else at Stop.

---

## 4. Pre-Registered Decision Gate (Go / No-Go)

To approve T2 or T3 for engine implementation and live deployment, the strategy must pass all three criteria:

1. **Criterion 1 (Statistically Positive Realized Edge):**
   Realized-only mean net $R > 0$ with 95% Month-Block bootstrap confidence interval lower bound $> 0$ under broker real swaps.
2. **Criterion 2 (Temporal Consistency):**
   Positive mean net $R$ across both sample sub-periods (2018–2021 and 2022–2026).
3. **Criterion 3 (Concentration Robustness):**
   95% Month-Block bootstrap lower bound remains $> 0$ after dropping the single largest winning trade (Leave-out top 1).

**Stopping Invariant:** If T2 or T3 fails any of Criteria 1–3, the strategy is declared **NO-GO**. No engine integration or live deployment will occur.
