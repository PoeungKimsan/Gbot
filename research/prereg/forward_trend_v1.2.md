# PRE-REGISTRATION: Forward Paper-Trading Trend Test Amendment (`forward_trend_v1.2`)

**Registration Version:** `forward_trend_v1.2`  
**Registration Date:** 2026-10-11  
**Git Branch:** `trend-forward`  
**Prior Pre-Registrations:**  
- `trend_v1.2`: `f1affec` (SHA-256 `d8330268db3e5e0115751e4d2a7c1fc06c01451448d473bc9cca35f45dd075c3`)  
- `forward_trend_v1`: `296ef4c` (SHA-256 `ca2b499e3848e7f45ddfce7e0d7854e18db1935a4e93d9fe7c4869987c2f6f33`)  
- `forward_trend_v1.1`: `d428021` (SHA-256 `0d777ce840bc8db21512bd12840530a969b38d33c030edfdf5729e460a6db62e`)  
**Status:** FROZEN UPON BYTE HASH COMMITMENT  

---

## 1. Objective, Status & Superseded Rules

This document establishes the self-contained pre-registration for the forward paper-trading evaluation of trend-following strategies on out-of-sample data starting October 2026.

1. **Short Stop Bug Correction:** Fixes the implementation of short stop fills in `research/trend/strategies.py` line 612 from `bar.open <= stop_loss` to `bar.open >= stop_loss` (short: fill at open if open >= stop else stop; long mirrors with open <= stop). This eliminates false fills below the stop price on gap-opens.
2. **Dual Reporting Requirement:** Forward reports must present performance metrics for:
   - **Realized-Only Trades:** Closed trades with audited exits (`STOP`, `OPPOSITE_CROSS`, `SIGN_FLIP`).
   - **Mark-to-Market Total:** Closed trades combined with active open positions marked to the latest closing prices.
3. **Independent Multi-Market Verification:** Extended independent verification without importing `strategies.py` confirms 100% agreement (0 mismatches across entry/exit dates, prices, exit reasons, and net R) across all 12 markets for both T2 and T3.
4. **Pinned Code Integrity:**
   - Strategy logic: `research/trend/strategies.py` (SHA-256 `469e79bcde25907ee882f8b82c08ce1f67002148dc4633a40f212b353588d4e7`)
   - Forward runner: `forward/runner.py` (SHA-256 `b46fc7b54bc8b9f83736f2d8c6b222fa1f508f6a66c661f3036e1bb438a7b5ba`)
   - Forward feed: `forward/feed.py` (SHA-256 `f50e4f618c28a5881a0c09c554c9ae650d208f1b07443599ef8e9f2c27957c26`)
   - Forward config: `forward/config.py` (pinned in repository)

---

## 2. Frozen Strategy Rules & Universe

- **Strategies:**
  - **T2 SMA Cross:** 50-day / 200-day simple moving average golden/death cross on daily bar close, $3.0\times \text{ATR}_{20}$ fixed stop loss. No re-entry into same regime following stop-out until fresh opposite cross resets regime.
  - **T3 TSMOM:** 12-month (252-day) return sign evaluated exclusively on calendar month-end bar close ($+1$ for Long, $-1$ for Short), $3.0\times \text{ATR}_{20}$ fixed stop loss. Exactly 1 trade per multi-month episode; if stopped out, strategy remains flat until next opposite sign flip.
- **Universe (12 Markets):**
  - Equities: `US500` (^GSPC), `NDX` (^NDX), `GER40` (^GDAXI).
  - Commodities: `XAUUSD` (GC=F), `XAGUSD` (SI=F), `Brent` (BZ=F).
  - FX: `EURUSD=X`, `GBPUSD=X`, `USDJPY=X`, `AUDUSD=X`, `USDCAD=X`, `USDCHF=X`.
- **Cost Model:**
  - Fixed $1.5\times$ baseline spread, $0.5\times \text{spread}$ stop slippage.
  - Concurrently logged at **0% financing** and **2% p.a. financing carry**.

---

## 3. Start Boundary & Carried Positions

### 3.1 Start Boundary Rule
1. **First Bar After Commit:** The forward test starts on the first trading bar following the commit of this pre-registration.
2. **Carried / Carried-Pending Exclusion:** All signals computed from daily bars dated on or before **2026-10-09** (including pending orders queued to enter at the 2026-10-12 session open) are classified as **CARRIED** or **CARRIED_PENDING**. They are permanently excluded from forward trade counts ($N$), win rates, mean $R$, and test statistics.
3. **Fresh Forward Entry:** A trade is classified as **FRESH** if and only if its signal is computed from a daily bar dated **$\ge$ 2026-10-12** (entering at the market open on or after 2026-10-13).
4. **Reversal Entries:** If a carried position exits due to an opposite signal generated on or after 2026-10-12, the subsequent reverse entry is **FRESH**.

### 3.2 15 Carried Positions as of 2026-10-09
- **T2_SMA_CROSS (7 positions):** EURUSD (Short), USDJPY (Short), AUDUSD (Long), USDCHF (Long), US500 (Long), GER40 (Long), NDX (Long). *(Flat: XAUUSD, XAGUSD, GBPUSD, USDCAD, Brent).*
- **T3_TSMOM (8 positions):** XAGUSD (Long), GBPUSD (Short), USDJPY (Long), AUDUSD (Long), USDCAD (Long), USDCHF (Long), US500 (Long), NDX (Long). *(Flat: XAUUSD, EURUSD, GER40, Brent).*

---

## 4. Zoneinfo Date Rule, Bar Completeness & Schedule

### 4.1 Zoneinfo Date Rule
All calendar date and session boundaries are resolved using Python's deterministic `zoneinfo.ZoneInfo` engine with pinned `tzdata`:
- `America/New_York`: US equities, gold, silver, Brent.
- `Europe/Berlin`: German DAX (`^GDAXI`).
- `Europe/London`: FX pairs (`EURUSD=X`, `GBPUSD=X`, etc.).

### 4.2 Bar Close & Settlement Times
- `^GDAXI` (XETRA): Official close/settlement auction at 17:30 Berlin (15:30 UTC in CEST; 16:30 UTC in CET). Late trading ends 22:00 Berlin (20:00 UTC in CEST; 21:00 UTC in CET).
- Yahoo FX: Timestamped 00:00 London / 00:00 UTC (23:00 UTC during BST; 00:00 UTC during GMT).
- US Equities & Metals: Closes 16:00 / 17:00 New York (20:00 / 21:00 UTC in EDT; 21:00 / 22:00 UTC in EST).

### 4.3 Incomplete Bar Filter
The feed enforces that any daily bar dated on or after the current calendar date in the underlying instrument's exchange timezone is dropped. No forming bar is ever evaluated.

### 4.4 Execution Schedule
The runner is scheduled for **06:00 UTC, Tuesday through Saturday**.

---

## 5. Statistical Power & Review Protocol

### 5.1 Power Requirements at Historical Means
*Computed from canonical historical backtest datasets (1998–2025, ~17.2 trades/year pace over 21.69 years average span):*

| Rule | Carry | Model | Hist Mean R | Hist Sigma | 50% Power (Trades) | 80% Power (Trades) | 50% Power (Years) | 80% Power (Years) | Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **T2_SMA_CROSS** | 0% | IID | +0.5652 | 4.0076 | 193 | 395 | 11.2 | 22.9 | `[LOW N / UNDERPOWERED]` |
| **T2_SMA_CROSS** | 0% | Cluster-Adj | +0.5652 | 4.0076 | 280 | 571 | 16.2 | 33.1 | `[LOW N / UNDERPOWERED]` |
| **T2_SMA_CROSS** | 2% | IID | +0.2777 | 3.7407 | 697 | 1424 | 40.4 | 82.6 | `[LOW N / UNDERPOWERED]` |
| **T2_SMA_CROSS** | 2% | Cluster-Adj | +0.2777 | 3.7407 | 1042 | 2128 | 60.4 | 123.4 | `[LOW N / UNDERPOWERED]` |
| **T3_TSMOM** | 0% | IID | +0.7380 | 5.5237 | 215 | 440 | 12.5 | 25.6 | `[LOW N / UNDERPOWERED]` |
| **T3_TSMOM** | 0% | Cluster-Adj | +0.7380 | 5.5237 | 340 | 695 | 19.8 | 40.4 | `[LOW N / UNDERPOWERED]` |
| **T3_TSMOM** | 2% | IID | +0.4199 | 5.0951 | 566 | 1156 | 32.9 | 67.2 | `[LOW N / UNDERPOWERED]` |
| **T3_TSMOM** | 2% | Cluster-Adj | +0.4199 | 5.0951 | 926 | 1891 | 53.8 | 110.0 | `[LOW N / UNDERPOWERED]` |

All reporting while $N < 200$ must explicitly display `[LOW N / UNDERPOWERED]`.

### 5.2 Interim Review Milestones
At $N = 16, 32, \text{and } 64$ closed forward trades:
1. **Implementation Fidelity:** Check 100% agreement between feed, broker quotes, and signal logic.
2. **Cost Attribution:** Decompose realized drag into spread, slippage, and financing.
3. **Outlier Check:** Audit any trade with $|R| > 10.0$ or stop distance $< 1.0\%$ of price.

---

## 6. Risk Logging & Persistence

The runner persists and logs:
1. **Per-Trade Risk:** `stop_pct_price = (stop_distance / entry_price) * 100.0` logged and recorded in `trades` table.
2. **Daily Portfolio Risk:** Active open positions recorded in `open_positions` table; `open_positions_count` and `summed_open_risk_r` ($1.0R$ per open trade) logged daily and recorded in `runs` table.

---

## 7. Baseline Regression Results

Regression verification on canonical fixed datasets:
- **T2_SMA_CROSS:** $N = 374$ trades across 12 markets (PASS).
- **T3_TSMOM:** $N = 373$ trades across 12 markets (PASS).
- **Independent Multi-Market Check:** Minimal non-import script across all 12 markets verifies 0 mismatches across dates, exit reasons, prices, and net R (PASS).

*Status: Pre-registration finalized and frozen upon commit to `trend-forward` branch.*
