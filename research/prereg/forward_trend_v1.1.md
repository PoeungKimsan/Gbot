# PRE-REGISTRATION: Forward Paper-Trading Trend Test Amendment (`forward_trend_v1.1`)

**Registration Version:** `forward_trend_v1.1`  
**Registration Date:** 2026-10-11  
**Git Branch:** `trend-forward`  
**Prior Pre-Registrations:**  
- `trend_v1.2`: `f1affec` (SHA-256 `d8330268db3e5e0115751e4d2a7c1fc06c01451448d473bc9cca35f45dd075c3`)  
- `forward_trend_v1`: `296ef4c` (SHA-256 `ca2b499e3848e7f45ddfce7e0d7854e18db1935a4e93d9fe7c4869987c2f6f33`)  
**Status:** DRAFT AMENDMENT — FROZEN PRIOR TO CLOCK START — PENDING USER APPROVAL  

---

## 1. Objective & Amendment Scope

This amendment updates the forward paper-trading protocol `forward_trend_v1` to incorporate empirical audit findings, calendar synchronization, and bar completeness invariants established as of 2026-10-11:

1. **Start Date Definition:** Forward paper trading commences on the **first complete trading bar after 2026-10-09** (Monday, 2026-10-12).
2. **Historical Bar Spending Invariant:** All market bars up through Friday, 2026-10-09, are formally designated as **spent historical data** (used for audit verification and baseline regime initialization). They are permanently excluded from forward out-of-sample scoring.
3. **Canonical Baseline Alignment:** Adopts the London-session fixed FX calendar (`output_fxfixed`, $N=374$ for T2, $N=373$ for T3) as the canonical research baseline.
4. **Execution Schedule & Incomplete Bar Rejection:** Proposes scheduled daily runner execution at **06:00 UTC, Tuesday through Saturday**. Rejects and drops any bar timestamped on or after the current calendar date in the underlying instrument's exchange timezone.
5. **Real Broker Swaps Prerequisite:** Mandates that real broker swap rates must be collected before any live `trend_v2` vehicle is deployed; forward logs continue at 0% and 2% carry with swap terms un-invented (`NaN`).

---

## 2. Frozen Strategy Rules & Universe

Identical to `forward_trend_v1`:
- **Strategies:** T2 50/200 SMA Cross and T3 12-Month TSMOM (`research/trend/strategies.py`).
- **Universe (12 Markets):** `XAUUSD`, `XAGUSD`, `US500`, `GER40`, `NDX`, `EURUSD`, `GBPUSD`, `USDJPY`, `AUDUSD`, `USDCAD`, `USDCHF`, `Brent`.
- **Costs:** $1.5\times$ baseline spread, $0.5\times \text{spread}$ stop slippage, logged concurrently at 0% and 2% p.a. financing carry.

---

## 3. Mandatory Flat-Start Policy & 15 Carried Positions

The forward paper test enforces a **strict flat start**. As of 2026-10-09 close, exactly **15 positions** are active across the two rules and are classified as **CARRIED POSITIONS**:

### 3.1 Rule T2_SMA_CROSS (7 Carried Positions, 5 Flat)
1. **EURUSD:** SHORT (Entry: 2026-04-14, Entry Px: 1.1763, Stop Loss: 1.2030, Stop Dist: 0.0267)
2. **USDJPY:** SHORT (Entry: 2026-10-01, Entry Px: 157.5870, Stop Loss: 161.8183, Stop Dist: 4.2313)
3. **AUDUSD:** LONG (Entry: 2025-06-24, Entry Px: 0.6480, Stop Loss: 0.6276, Stop Dist: 0.0204)
4. **USDCHF:** LONG (Entry: 2026-07-03, Entry Px: 0.8043, Stop Loss: 0.7881, Stop Dist: 0.0162)
5. **US500:** LONG (Entry: 2025-07-02, Entry Px: 6193.8799, Stop Loss: 5983.2845, Stop Dist: 210.5954)
6. **GER40:** LONG (Entry: 2026-06-08, Entry Px: 24447.3203, Stop Loss: 23325.3796, Stop Dist: 1121.9407)
7. **NDX:** LONG (Entry: 2025-06-25, Entry Px: 22279.6895, Stop Loss: 21248.8026, Stop Dist: 1030.8869)
*Flat Markets under T2:* `XAUUSD`, `XAGUSD`, `GBPUSD`, `USDCAD`, `Brent`.

### 3.2 Rule T3_TSMOM (8 Carried Positions, 4 Flat)
1. **XAGUSD:** LONG (Entry: 2024-03-01, Entry Px: 22.6550, Stop Loss: 21.6426, Stop Dist: 1.0125)
2. **GBPUSD:** SHORT (Entry: 2026-09-01, Entry Px: 1.3548, Stop Loss: 1.3763, Stop Dist: 0.0214)
3. **USDJPY:** LONG (Entry: 2026-02-02, Entry Px: 155.1350, Stop Loss: 150.9715, Stop Dist: 4.1635)
4. **AUDUSD:** LONG (Entry: 2025-12-01, Entry Px: 0.6552, Stop Loss: 0.6401, Stop Dist: 0.0151)
5. **USDCAD:** LONG (Entry: 2026-06-01, Entry Px: 1.3798, Stop Loss: 1.3623, Stop Dist: 0.0175)
6. **USDCHF:** LONG (Entry: 2026-09-01, Entry Px: 0.8084, Stop Loss: 0.7911, Stop Dist: 0.0173)
7. **US500:** LONG (Entry: 2023-06-01, Entry Px: 4183.0298, Stop Loss: 4047.3480, Stop Dist: 135.6818)
8. **NDX:** LONG (Entry: 2023-05-01, Entry Px: 13229.1104, Stop Loss: 12595.4589, Stop Dist: 633.6514)
*Flat Markets under T3:* `XAUUSD`, `EURUSD`, `GER40`, `Brent`.

### 3.3 Statistical Isolation Invariant
All 15 carried positions are routed directly to the `carried_trades` table in `forward/forward_trend.db`. They are **strictly excluded** from forward trade counts ($N$), win rates, mean $R$, and confidence intervals. Only fresh signals triggered on or after 2026-10-12 enter the forward test evaluation set.

---

## 4. Execution Timing & Bar Completeness

- **Scheduled Execution:** 06:00 UTC, Tuesday through Saturday.
  - London local time: 07:00 BST (session closed at 00:00 midnight).
  - New York local time: 02:00 EDT (session closed at 17:00 EDT previous day).
  - Berlin local time: 08:00 CEST (session closed at 22:00 CEST previous day).
- **Incomplete Bar Filter:** The runner enforces that any bar dated on or after the current local date in the instrument's exchange timezone is dropped. No forming bar is ever evaluated.

---

## 5. Statistical Power & Reporting

- **Power Threshold:** $N \ge 193$ trades required for positive lower bound of 95% CI.
- **Reporting Rule:** All reports prior to reaching $N = 193$ must carry the label `[LOW N / UNDERPOWERED]`.
- **Clock Status:** The forward runner is **not scheduled**, and no timer/cron has been started. Execution awaits explicit user authorization.
