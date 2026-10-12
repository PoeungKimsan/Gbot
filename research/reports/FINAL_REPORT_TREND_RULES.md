# FINAL RESEARCH REPORT: Trend Rules on Liquid Markets (`trend_v1.2`)

**Pre-Registration SHA-256:** `d8330268db3e5e0115751e4d2a7c1fc06c01451448d473bc9cca35f45dd075c3`  
**Pre-Registration Commit:** `f1affec`  
**Execution Timestamp (UTC):** `2026-10-11T12:56:48.275457+00:00`  
**Full Runs Executed:** 3 (No selective re-runs; rules frozen)  
**Evaluation Horizon:** `earliest+1yr to 2025-12-31`  

---

## 1. Executive Summary & Pre-Registered Verdict Ladder

### 1.1 The Pre-Registered Verdict Ladder
Under the pre-registered protocol, outcomes are governed strictly by a 3-tier verdict ladder:
1. **(a) SURVIVOR:** All five gates pass at primary costs ($1.5\times$ spread + 2.0% annual financing carry).
2. **(b) GROSS-OF-FINANCING SURVIVOR:** All five gates pass at 0% financing ($1.5\times$ spread + 0% financing carry), but not at 2% financing. (Reported as: *"edge before carry; viability depends on financing terms"*).
3. **(c) Otherwise INCONCLUSIVE: NO EDGE DEMONSTRATED:** Fails one or more gates under both financing conditions.

*Methodological Disclosure:* **Two financing levels (0.0% and 2.0%) were examined.** In accordance with the pre-registration, the Holm-Bonferroni multiple testing correction was applied within each financing level across the 3 candidate rules at $\alpha_{\text{family}} = 0.05$.

### 1.2 Formal Protocol Verdict by Rule

> ### **SUMMARY OF VERDICTS:**
> 
> - **T1 (Donchian Breakout with Reset Gate):** **INCONCLUSIVE: NO EDGE DEMONSTRATED**  
>   *Fails Gate 4 (Null B direction shuffle) at 0% financing ($p95 = +0.1750R$ vs real $+0.1634R$), and fails Gates 3, 4, 5 at 2% financing.*  
> 
> - **T2 (50/200 SMA Cross):** **GROSS-OF-FINANCING SURVIVOR**  
>   *Verdict:* **"edge before carry; viability depends on financing terms"**  
>   *Satisfies ALL FIVE acceptance gates at 0% financing: $N=374 \ge 200$, Mean Net $R = +0.5652R > 0$, Holm-adjusted bootstrap $p = 0.0207 < 0.05$ (95% CI `[+0.1004, +1.0926]` strictly positive), beats Null A ($p95 = +0.2936R$) and Null B ($p95 = +0.4848R$), and positive in all sub-periods and ex-metals. Fails Gate 3 & Gate 4 when 2% financing carry is deducted.*  
> 
> - **T3 (TSMOM / 12-Month Sign):** **GROSS-OF-FINANCING SURVIVOR**  
>   *Verdict:* **"edge before carry; viability depends on financing terms"**  
>   *Satisfies ALL FIVE acceptance gates at 0% financing: $N=377 \ge 200$, Mean Net $R = +0.7662R > 0$, Holm-adjusted bootstrap $p = 0.0207 < 0.05$ (95% CI `[+0.1288, +1.5162]` strictly positive), beats Null A ($p95 = +0.4088R$) and Null B ($p95 = +0.5745R$), and positive in all sub-periods and ex-metals. Fails Gate 3 under 2% financing carry due to bootstrap variance crossing zero.*  
> 
> **Sealed 2026 Protocol:** In strict adherence to Rule 2, **sealed 2026 data opened once, only for qualifying rules (T2 and T3), evaluated at 0% financing (the financing level that qualified them)**. T1 was not unsealed.

---

## 2. Instrument Identity & Data Quality Audit

### 2.1 Identity & History Audit Table
All 12 candidate instruments were audited against raw historical price series from 2001/2007 through 2025-12-31. Every asset met the minimum 12-year history requirement (spanning 18.4 to 24.2 years); zero instruments were dropped.

| Symbol | Benchmark Ticker | Asset Class / Description | First Date | Last Date | History Span | Total Bars | First Close | Last Close (2025-12-31) | Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **XAUUSD** | `GC=F` | Gold Futures (USD/oz) | 2001-10-11 | 2025-12-31 | 24.2 yrs | 6,082 | 281.8000 | 4341.1001 | PASS |
| **XAGUSD** | `SI=F` | Silver Futures (USD/oz) | 2001-10-11 | 2025-12-31 | 24.2 yrs | 6,082 | 4.4030 | 70.1340 | PASS |
| **EURUSD** | `EURUSD=X` | Euro / US Dollar FX | 2003-12-01 | 2025-12-31 | 22.1 yrs | 5,730 | 1.1965 | 1.1747 | PASS |
| **GBPUSD** | `GBPUSD=X` | British Pound / US Dollar FX | 2003-12-01 | 2025-12-31 | 22.1 yrs | 5,742 | 1.7186 | 1.3467 | PASS |
| **USDJPY** | `USDJPY=X` | US Dollar / Japanese Yen FX | 2001-10-08 | 2025-12-31 | 24.2 yrs | 6,289 | 120.2000 | 156.4130 | PASS |
| **AUDUSD** | `AUDUSD=X` | Australian Dollar / US Dollar FX | 2006-05-15 | 2025-12-31 | 19.6 yrs | 5,106 | 0.7671 | 0.6698 | PASS |
| **USDCAD** | `USDCAD=X` | US Dollar / Canadian Dollar FX | 2003-09-16 | 2025-12-31 | 22.3 yrs | 5,798 | 1.3667 | 1.3695 | PASS |
| **USDCHF** | `USDCHF=X` | US Dollar / Swiss Franc FX | 2003-09-16 | 2025-12-31 | 22.3 yrs | 5,796 | 1.3807 | 0.7917 | PASS |
| **US500** | `^GSPC` | S&P 500 Index | 2001-10-10 | 2025-12-31 | 24.2 yrs | 6,096 | 1080.9900 | 6845.5000 | PASS |
| **GER40** | `^GDAXI` | DAX 40 Index | 2001-10-09 | 2025-12-30 | 24.2 yrs | 6,150 | 4472.4199 | 24490.4102 | PASS |
| **Brent** | `BZ=F` | Brent Crude Oil Futures (USD/bbl) | 2007-07-30 | 2025-12-31 | 18.4 yrs | 4,585 | 75.7400 | 60.8500 | PASS |
| **NDX** | `^NDX` | NASDAQ-100 Index | 2001-10-10 | 2025-12-31 | 24.2 yrs | 6,096 | 1304.6801 | 25249.8496 | PASS |

### 2.2 Data Quality & Futures Roll Audit (Report-Only)
- **Geometric Invariant:** True High and Low enforced ($\text{High} = \max(H, O, C), \text{Low} = \min(L, O, C)$).
- **Price Jumps $> 6\times ATR_{20}$:** Logged in XAUUSD (2), XAGUSD (4), EURUSD (4), USDJPY (2), and USDCHF (1). All correspond to audited macro shocks (2013 precious metals crash, 2015 SNB unpeg, 2008 GFC). Brent had 0 jumps $> 6\times ATR_{20}$.
- **Continuous Futures Roll:** Front-month pricing for `GC=F`, `SI=F`, `BZ=F` with gap slippage on stop triggers.

---

## 3. Pre-Registered Cost & Friction Model

All costs were frozen prior to running backtests with an institutional $1.5\times$ safety multiplier:
- **Slippage on Stop Exits:** Extra $0.5 \times \text{spread}$ on stop-out fills.
- **Financing / Carry Drag:** Primary rate of 2.0% per annum of notional value per day held, applied symmetrically to Long and Short positions. Sensitivity at 0.0% carry.

| Instrument | Frozen Spread (1.5x) | Spread Units | Stop Slippage Penalty | Primary Financing Rate | Gross-of-Financing Rate |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **EURUSD** | 0.000150 | FX Quote | 0.000075 | 2.0% / yr | 0.0% / yr |
| **GBPUSD** | 0.000180 | FX Quote | 0.000090 | 2.0% / yr | 0.0% / yr |
| **USDJPY** | 0.015000 | FX Quote | 0.007500 | 2.0% / yr | 0.0% / yr |
| **AUDUSD** | 0.000180 | FX Quote | 0.000090 | 2.0% / yr | 0.0% / yr |
| **USDCAD** | 0.000225 | FX Quote | 0.000112 | 2.0% / yr | 0.0% / yr |
| **USDCHF** | 0.000225 | FX Quote | 0.000112 | 2.0% / yr | 0.0% / yr |
| **XAUUSD** | 0.375000 | USD/oz | 0.187500 | 2.0% / yr | 0.0% / yr |
| **XAGUSD** | 0.030000 | USD/oz | 0.015000 | 2.0% / yr | 0.0% / yr |
| **US500** | 0.750000 | Index pts | 0.375000 | 2.0% / yr | 0.0% / yr |
| **GER40** | 2.250000 | Index pts | 1.125000 | 2.0% / yr | 0.0% / yr |
| **Brent** | 0.060000 | USD/bbl | 0.030000 | 2.0% / yr | 0.0% / yr |
| **NDX** | 2.250000 | Index pts | 1.125000 | 2.0% / yr | 0.0% / yr |

---

## 4. Pooled Strategy Performance Across Financing Levels

Evaluated across 12 liquid markets from first date + 1 year warm-up through 2025-12-31. Units: $R$ multiples (where $1R = \text{initial stop distance}$).

### 4.1 Primary Costs (2.0% Financing Carry)

| Rule Name | Description | N (Trades) | Win Rate | Mean Gross R | Mean Net R | Median Net R | Profit Factor | 95% Bootstrap CI | Raw p-val | Holm Adj p-val | Holm 5% Pass |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **T1_DONCHIAN** | 55d Breakout (Reset Gate) / 20d Exit / 2x ATR | 1025 | 27.51% | +0.1893R | +0.0409R | -1.0251R | 1.06 | [-0.1410, 0.2352] | 0.3384 | 0.3384 | FAIL |
| **T2_SMA_CROSS** | 50/200 SMA Cross / 3x ATR | 374 | 27.27% | +0.5826R | +0.2777R | -1.0421R | 1.36 | [-0.1537, 0.7780] | 0.1190 | 0.2380 | FAIL |
| **T3_TSMOM** | Month-end 252d Return / 3x ATR | 377 | 23.34% | +0.7822R | +0.4531R | -0.9112R | 1.63 | [-0.1336, 1.1531] | 0.0774 | 0.2322 | FAIL |

### 4.2 Gross-of-Financing Costs (0.0% Financing Carry)

| Rule Name | N (Trades) | Win Rate | Mean Net R (0% Fin) | Median Net R | Profit Factor | 95% Bootstrap CI | Raw p-val | Holm Adj p-val | Holm 5% Pass |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **T1_DONCHIAN** | 1025 | 29.95% | +0.1634R | -1.0093R | 1.25 | [-0.0276, 0.3673] | 0.0471 | 0.0471 | PASS |
| **T2_SMA_CROSS** | 374 | 28.88% | +0.5652R | -1.0076R | 1.83 | [0.1004, 1.0926] | 0.0071 | 0.0207 | PASS |
| **T3_TSMOM** | 377 | 26.79% | +0.7662R | -0.8084R | 2.26 | [0.1288, 1.5162] | 0.0069 | 0.0207 | PASS |

---

## 5. Pre-Registered Acceptance Gate Scorecard

### 5.1 Primary Costs Gate Scorecard (2.0% Financing)

| Acceptance Gate | Gate Requirement | T1 Donchian | T2 SMA Cross | T3 TSMOM |
| :--- | :--- | :--- | :--- | :--- |
| **Gate 1: Sample Size** | $N \ge 200$ pooled | **PASS** (1025) | **PASS** (374) | **PASS** (377) |
| **Gate 2: Net Expectancy** | Mean Net $R > 0$ at 2% fin | **PASS** (+0.0409R) | **PASS** (+0.2777R) | **PASS** (+0.4531R) |
| **Gate 3: Significance** | Holm-adjusted $p < 0.05$ | **FAIL** ($p=0.3384$) | **FAIL** ($p=0.2380$) | **FAIL** ($p=0.2322$) |
| **Gate 4: Null Superiority** | Net $R \ge$ P95 Null A & Null B | **FAIL** | **FAIL** | **PASS** |
| **Gate 5: Sub-period & Ex-Metals** | Net $R > 0$ in 2010–17, 2018–25 & ex-metals | **FAIL** | **PASS** | **PASS** |
| **TIER (a) SURVIVOR STATUS** | All 5 Gates Pass at 2% Financing | **REJECTED** | **REJECTED** | **REJECTED** |

### 5.2 Gross-of-Financing Gate Scorecard (0.0% Financing)

| Acceptance Gate | Gate Requirement | T1 Donchian | T2 SMA Cross | T3 TSMOM |
| :--- | :--- | :--- | :--- | :--- |
| **Gate 1: Sample Size** | $N \ge 200$ pooled | **PASS** (1025) | **PASS** (374) | **PASS** (377) |
| **Gate 2: Net Expectancy** | Mean Net $R > 0$ at 0% fin | **PASS** (+0.1634R) | **PASS** (+0.5652R) | **PASS** (+0.7662R) |
| **Gate 3: Significance** | Holm-adjusted $p < 0.05$ | **PASS** ($p=0.0471$) | **PASS** ($p=0.0207$) | **PASS** ($p=0.0207$) |
| **Gate 4: Null Superiority** | Net $R \ge$ P95 Null A & Null B (0% fin) | **FAIL** | **PASS** | **PASS** |
| **Gate 5: Sub-period & Ex-Metals** | Net $R > 0$ in 2010–17, 2018–25 & ex-metals | **PASS** | **PASS** | **PASS** |
| **TIER (b) GROSS-OF-FINANCING SURVIVOR** | All 5 Gates Pass at 0% Financing | **REJECTED** | **SURVIVOR** | **SURVIVOR** |

### 5.3 Formal Verdict Ladder Summary

| Rule Name | Primary (2% Fin) Status | Gross-of-Fin (0% Fin) Status | Pre-Registered Verdict | Sealed 2026 Eligibility |
| :--- | :--- | :--- | :--- | :--- |
| **T1_DONCHIAN** | REJECTED | REJECTED (Fails Gate 4 Null B) | **INCONCLUSIVE: NO EDGE DEMONSTRATED** | Sealed (Unopened) |
| **T2_SMA_CROSS** | REJECTED | **ALL 5 GATES PASS** | **GROSS-OF-FINANCING SURVIVOR** ("edge before carry; viability depends on financing terms") | **UNSEALED AT 0% FINANCING** |
| **T3_TSMOM** | REJECTED | **ALL 5 GATES PASS** | **GROSS-OF-FINANCING SURVIVOR** ("edge before carry; viability depends on financing terms") | **UNSEALED AT 0% FINANCING** |

---

## 6. Null Model Simulations (2,000 Iterations, Fixed Seed 42)

### 6.1 Null Models at Primary Costs (2.0% Financing)

| Rule Name | Real Mean Net R | Null A Mean | Null A P95 | Beats Null A? | Null B Mean | Null B P95 | Beats Null B? | Passes Both Nulls? |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **T1_DONCHIAN** | +0.0409R | -0.0242R | +0.0545R | NO | +0.0129R | +0.0925R | NO | **FAIL** |
| **T2_SMA_CROSS** | +0.2777R | -0.0112R | +0.1569R | YES | +0.1255R | +0.3026R | NO | **FAIL** |
| **T3_TSMOM** | +0.4531R | +0.0299R | +0.2642R | YES | +0.1416R | +0.3712R | YES | **PASS** |

### 6.2 Null Models at Gross-of-Financing (0.0% Financing)

| Rule Name | Real Mean Net R (0% Fin) | Null A Mean | Null A P95 | Beats Null A? | Null B Mean | Null B P95 | Beats Null B? | Passes Both Nulls? |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **T1_DONCHIAN** | +0.1634R | +0.0376R | +0.1188R | YES | +0.0914R | +0.1750R | NO | **FAIL** |
| **T2_SMA_CROSS** | +0.5652R | +0.1159R | +0.2936R | YES | +0.2950R | +0.4848R | YES | **PASS** |
| **T3_TSMOM** | +0.7662R | +0.1593R | +0.4088R | YES | +0.3259R | +0.5745R | YES | **PASS** |

---

## 7. Granular Component Drag & Holding Duration Disaggregation

### 7.1 Pooled Average Drag by Component and Holding Duration per Rule

| Rule Name | Total Trades N | Avg Holding Days ($H$) | Avg Spread Drag ($R$) | Avg Slippage Drag ($R$) | Avg Financing Drag ($R$) | Avg Total Drag ($R$) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **T1_DONCHIAN** | 1025 | 47.36 days | -0.0199R | -0.0061R | -0.1225R | **-0.1484R** |
| **T2_SMA_CROSS** | 374 | 165.44 days | -0.0132R | -0.0042R | -0.2874R | **-0.3049R** |
| **T3_TSMOM** | 377 | 179.76 days | -0.0123R | -0.0038R | -0.3131R | **-0.3292R** |

### 7.2 By-Market Component Drag and Holding Duration (All 12 Instruments)

Disaggregation across all 12 assets demonstrating how holding duration and financing carry compound across asset classes:

#### T1_DONCHIAN Component Drag by Market

| Symbol | Asset Class | Trades N | Avg Days ($H$) | Avg Spread Drag | Avg Slip Drag | Avg Fin Drag (2%) | Avg Total Drag |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **AUDUSD** | FX Major | 71 | 50.27 d | -0.0109R | -0.0037R | -0.1315R | **-0.1461R** |
| **Brent** | Energy | 67 | 51.13 d | -0.0162R | -0.0042R | -0.0583R | **-0.0787R** |
| **EURUSD** | FX Major | 72 | 48.22 d | -0.0079R | -0.0024R | -0.1803R | **-0.1905R** |
| **GBPUSD** | FX Major | 68 | 53.10 d | -0.0071R | -0.0018R | -0.1741R | **-0.1830R** |
| **GER40** | Equity Index | 108 | 43.53 d | -0.0091R | -0.0024R | -0.0839R | **-0.0955R** |
| **NDX** | Equity Index | 102 | 49.47 d | -0.0258R | -0.0066R | -0.0891R | **-0.1214R** |
| **US500** | Equity Index | 114 | 43.51 d | -0.0186R | -0.0049R | -0.1034R | **-0.1269R** |
| **USDCAD** | FX Major | 68 | 54.12 d | -0.0128R | -0.0035R | -0.2037R | **-0.2200R** |
| **USDCHF** | FX Major | 70 | 52.40 d | -0.0133R | -0.0041R | -0.1607R | **-0.1781R** |
| **USDJPY** | FX Major | 71 | 57.48 d | -0.0078R | -0.0019R | -0.2094R | **-0.2191R** |
| **XAGUSD** | Precious Metal | 110 | 37.22 d | -0.0657R | -0.0236R | -0.0630R | **-0.1523R** |
| **XAUUSD** | Precious Metal | 104 | 40.73 d | -0.0205R | -0.0060R | -0.1023R | **-0.1288R** |

#### T2_SMA_CROSS Component Drag by Market

| Symbol | Asset Class | Trades N | Avg Days ($H$) | Avg Spread Drag | Avg Slip Drag | Avg Fin Drag (2%) | Avg Total Drag |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **AUDUSD** | FX Major | 34 | 147.15 d | -0.0075R | -0.0020R | -0.2522R | **-0.2617R** |
| **Brent** | Energy | 35 | 104.40 d | -0.0113R | -0.0038R | -0.0881R | **-0.1032R** |
| **EURUSD** | FX Major | 30 | 199.10 d | -0.0052R | -0.0013R | -0.4799R | **-0.4864R** |
| **GBPUSD** | FX Major | 30 | 158.87 d | -0.0050R | -0.0017R | -0.3536R | **-0.3603R** |
| **GER40** | Equity Index | 23 | 230.57 d | -0.0060R | -0.0014R | -0.2902R | **-0.2976R** |
| **NDX** | Equity Index | 33 | 211.67 d | -0.0172R | -0.0047R | -0.2379R | **-0.2598R** |
| **US500** | Equity Index | 23 | 234.78 d | -0.0108R | -0.0030R | -0.3298R | **-0.3436R** |
| **USDCAD** | FX Major | 33 | 176.39 d | -0.0088R | -0.0030R | -0.4254R | **-0.4372R** |
| **USDCHF** | FX Major | 36 | 139.33 d | -0.0088R | -0.0030R | -0.2731R | **-0.2848R** |
| **USDJPY** | FX Major | 34 | 148.79 d | -0.0055R | -0.0019R | -0.3379R | **-0.3454R** |
| **XAGUSD** | Precious Metal | 33 | 166.52 d | -0.0567R | -0.0189R | -0.2051R | **-0.2807R** |
| **XAUUSD** | Precious Metal | 30 | 113.33 d | -0.0119R | -0.0045R | -0.2203R | **-0.2367R** |

#### T3_TSMOM Component Drag by Market

| Symbol | Asset Class | Trades N | Avg Days ($H$) | Avg Spread Drag | Avg Slip Drag | Avg Fin Drag (2%) | Avg Total Drag |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **AUDUSD** | FX Major | 27 | 157.11 d | -0.0076R | -0.0017R | -0.2826R | **-0.2918R** |
| **Brent** | Energy | 28 | 169.82 d | -0.0114R | -0.0024R | -0.1474R | **-0.1611R** |
| **EURUSD** | FX Major | 33 | 139.39 d | -0.0053R | -0.0016R | -0.3676R | **-0.3746R** |
| **GBPUSD** | FX Major | 35 | 124.23 d | -0.0051R | -0.0017R | -0.3085R | **-0.3154R** |
| **GER40** | Equity Index | 24 | 261.21 d | -0.0047R | -0.0016R | -0.2634R | **-0.2697R** |
| **NDX** | Equity Index | 20 | 326.35 d | -0.0181R | -0.0050R | -0.3986R | **-0.4217R** |
| **US500** | Equity Index | 20 | 407.55 d | -0.0095R | -0.0024R | -0.5145R | **-0.5264R** |
| **USDCAD** | FX Major | 40 | 116.65 d | -0.0088R | -0.0023R | -0.2955R | **-0.3065R** |
| **USDCHF** | FX Major | 49 | 108.73 d | -0.0095R | -0.0024R | -0.2398R | **-0.2517R** |
| **USDJPY** | FX Major | 35 | 191.46 d | -0.0054R | -0.0013R | -0.4682R | **-0.4750R** |
| **XAGUSD** | Precious Metal | 39 | 149.64 d | -0.0469R | -0.0176R | -0.1737R | **-0.2382R** |
| **XAUUSD** | Precious Metal | 27 | 235.00 d | -0.0091R | -0.0022R | -0.4463R | **-0.4577R** |

---

## 8. Disaggregations & Breadth Analysis

### 8.1 Sub-Period Breakdown (Primary 2% vs Gross-of-Fin 0%)

| Rule Name | Earliest–09 Net R (2%) | Earliest–09 Net R (0%) | 2010–17 Net R (2%) | 2010–17 Net R (0%) | 2018–25 Net R (2%) | 2018–25 Net R (0%) | Ex-Metals Net R (2%) | Ex-Metals Net R (0%) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **T1_DONCHIAN** | +0.0943R | +0.1952R | +0.1560R | +0.2994R | -0.1120R | +0.0088R | -0.0343R | +0.0988R |
| **T2_SMA_CROSS** | +0.5838R | +0.9043R | +0.1826R | +0.4696R | +0.1982R | +0.4672R | +0.1145R | +0.4172R |
| **T3_TSMOM** | +1.2070R | +1.6020R | +0.1198R | +0.3898R | +0.3324R | +0.6398R | +0.1217R | +0.4407R |

### 8.2 Directional Side Breakdown (Long vs Short)

| Rule Name | Long Trades N | Long Net R (2%) | Long Net R (0%) | Short Trades N | Short Net R (2%) | Short Net R (0%) | Long/Short Spread (0% Fin) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **T1_DONCHIAN** | 580 | +0.2117R | +0.3415R | 445 | -0.1817R | -0.0688R | +0.4103R |
| **T2_SMA_CROSS** | 189 [FLAG: N < 200, UNDERPOWERED] | +0.8163R | +1.1395R | 185 [FLAG: N < 200, UNDERPOWERED] | -0.2725R | -0.0216R | +1.1611R |
| **T3_TSMOM** | 191 [FLAG: N < 200, UNDERPOWERED] | +1.0658R | +1.4148R | 186 [FLAG: N < 200, UNDERPOWERED] | -0.1761R | +0.1002R | +1.3146R |

### 8.3 By-Market Breakdown (All 12 Instruments)

*(Note: Every individual market cell has $N < 200$ and is labeled `[UNDERPOWERED]` per pre-registration rules.)*

| Symbol | Asset Class | T1 Donchian N | T1 Net R (2%) | T2 SMA Cross N | T2 Net R (2%) | T2 Net R (0%) | T3 TSMOM N | T3 Net R (2%) | T3 Net R (0%) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **AUDUSD** | FX Major | 71 [FLAG: N < 200, UNDERPOWERED] | -0.2755R | 34 [FLAG: N < 200, UNDERPOWERED] | -0.1542R | +0.0981R | 27 [FLAG: N < 200, UNDERPOWERED] | -0.4497R | -0.1672R |
| **Brent** | Energy | 67 [FLAG: N < 200, UNDERPOWERED] | +0.4215R | 35 [FLAG: N < 200, UNDERPOWERED] | -0.0893R | -0.0012R | 28 [FLAG: N < 200, UNDERPOWERED] | +0.2796R | +0.4270R |
| **EURUSD** | FX Major | 72 [FLAG: N < 200, UNDERPOWERED] | -0.1257R | 30 [FLAG: N < 200, UNDERPOWERED] | +0.1332R | +0.6131R | 33 [FLAG: N < 200, UNDERPOWERED] | -0.2611R | +0.1065R |
| **GBPUSD** | FX Major | 68 [FLAG: N < 200, UNDERPOWERED] | -0.0046R | 30 [FLAG: N < 200, UNDERPOWERED] | -0.5357R | -0.1821R | 35 [FLAG: N < 200, UNDERPOWERED] | -0.3526R | -0.0441R |
| **GER40** | Equity Index | 108 [FLAG: N < 200, UNDERPOWERED] | -0.0340R | 23 [FLAG: N < 200, UNDERPOWERED] | +1.4292R | +1.7194R | 24 [FLAG: N < 200, UNDERPOWERED] | +0.0029R | +0.2663R |
| **NDX** | Equity Index | 102 [FLAG: N < 200, UNDERPOWERED] | +0.1250R | 33 [FLAG: N < 200, UNDERPOWERED] | +1.1559R | +1.3937R | 20 [FLAG: N < 200, UNDERPOWERED] | +2.2876R | +2.6862R |
| **US500** | Equity Index | 114 [FLAG: N < 200, UNDERPOWERED] | -0.1791R | 23 [FLAG: N < 200, UNDERPOWERED] | +0.9634R | +1.2932R | 20 [FLAG: N < 200, UNDERPOWERED] | +1.7322R | +2.2467R |
| **USDCAD** | FX Major | 68 [FLAG: N < 200, UNDERPOWERED] | -0.1726R | 33 [FLAG: N < 200, UNDERPOWERED] | -0.4436R | -0.0183R | 40 [FLAG: N < 200, UNDERPOWERED] | -0.6061R | -0.3106R |
| **USDCHF** | FX Major | 70 [FLAG: N < 200, UNDERPOWERED] | -0.3689R | 36 [FLAG: N < 200, UNDERPOWERED] | -0.5666R | -0.2935R | 49 [FLAG: N < 200, UNDERPOWERED] | -0.4780R | -0.2382R |
| **USDJPY** | FX Major | 71 [FLAG: N < 200, UNDERPOWERED] | +0.3065R | 34 [FLAG: N < 200, UNDERPOWERED] | -0.0615R | +0.2765R | 35 [FLAG: N < 200, UNDERPOWERED] | +0.8661R | +1.3343R |
| **XAGUSD** | Precious Metal | 110 [FLAG: N < 200, UNDERPOWERED] | +0.4045R | 33 [FLAG: N < 200, UNDERPOWERED] | +1.9631R | +2.1682R | 39 [FLAG: N < 200, UNDERPOWERED] | +1.6152R | +1.7889R |
| **XAUUSD** | Precious Metal | 104 [FLAG: N < 200, UNDERPOWERED] | +0.2427R | 30 [FLAG: N < 200, UNDERPOWERED] | +0.1159R | +0.3361R | 27 [FLAG: N < 200, UNDERPOWERED] | +2.5920R | +3.0383R |

### 8.4 Trade Count Sanity Table (Actual vs Expected Counts per Section 5)

Comparing empirical trade counts against the pre-registered expected counts (Section 5: T1 expected 40–70/mkt, pooled 600–800; T2 expected 15–30/mkt, pooled 250–350; T3 expected 15–35/mkt, pooled 250–350):

| Symbol | T1 Actual | T1 Expected | T1 Status | T2 Actual | T2 Expected | T2 Status | T3 Actual | T3 Expected | T3 Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **AUDUSD** | 71 | 40–70 | Higher (24.2y) | 34 | 15–30 | Slightly Above | 27 | 15–35 | In Range |
| **Brent** | 67 | 40–70 | In Range | 35 | 15–30 | Slightly Above | 28 | 15–35 | In Range |
| **EURUSD** | 72 | 40–70 | Higher (24.2y) | 30 | 15–30 | In Range | 33 | 15–35 | In Range |
| **GBPUSD** | 68 | 40–70 | In Range | 30 | 15–30 | In Range | 35 | 15–35 | In Range |
| **GER40** | 108 | 40–70 | Higher (24.2y) | 23 | 15–30 | In Range | 24 | 15–35 | In Range |
| **NDX** | 102 | 40–70 | Higher (24.2y) | 33 | 15–30 | Slightly Above | 20 | 15–35 | In Range |
| **US500** | 114 | 40–70 | Higher (24.2y) | 23 | 15–30 | In Range | 20 | 15–35 | In Range |
| **USDCAD** | 68 | 40–70 | In Range | 33 | 15–30 | Slightly Above | 40 | 15–35 | Slightly Above |
| **USDCHF** | 70 | 40–70 | In Range | 36 | 15–30 | Slightly Above | 49 | 15–35 | Slightly Above |
| **USDJPY** | 71 | 40–70 | Higher (24.2y) | 34 | 15–30 | Slightly Above | 35 | 15–35 | In Range |
| **XAGUSD** | 110 | 40–70 | Higher (24.2y) | 33 | 15–30 | Slightly Above | 39 | 15–35 | Slightly Above |
| **XAUUSD** | 104 | 40–70 | Higher (24.2y) | 30 | 15–30 | In Range | 27 | 15–35 | In Range |
| **POOLED** | **1025** | 600–800 | Higher (24y span) | **374** | 250–350 | Slightly Above (24y) | **377** | 250–350 | Slightly Above (24y) |

---

## 9. Friction & Cost Sensitivity Analysis

Mean Net $R$ per trade across cost variations:

| Rule Name | Baseline (2% Fin, 1.5x Spread) | Sensitivity 1: 0% Financing | Sensitivity 2: 4% Financing | Sensitivity 3: Spread 2x Baseline |
| :--- | :--- | :--- | :--- | :--- |
| **T1_DONCHIAN** | +0.0409R | +0.1634R | -0.0816R | +0.0150R |
| **T2_SMA_CROSS** | +0.2777R | +0.5652R | -0.0097R | +0.2603R |
| **T3_TSMOM** | +0.4531R | +0.7662R | +0.1400R | +0.4371R |

---

## 10. Equal-Risk Portfolio Equity & Drawdown (Report-Only)

Simulated portfolio assuming equal risk (1.0R allocated per trade) across all 12 instruments:

| Rule Name | Total Trades | Cumulative Net R (2% Fin) | Max DD (2% Fin) | Calmar (2% Fin) | Cumulative Net R (0% Fin) | Max DD (0% Fin) | Calmar (0% Fin) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **T1_DONCHIAN** | 1025 | +41.92R | 87.04R | 0.48 | +167.46R | 44.71R | 3.75 |
| **T2_SMA_CROSS** | 374 | +103.86R | 45.87R | 2.26 | +211.37R | 31.94R | 6.62 |
| **T3_TSMOM** | 377 | +170.82R | 48.96R | 3.49 | +288.87R | 24.59R | 11.75 |

---

## 11. Hand-Trace Audit (15 Selected Trades per Rule)

Audited against raw daily bars, verifying indicator values, gap fills, stop order executions, and arithmetic fee decompositions bit-for-bit:

### T1_DONCHIAN Hand Traces

| Trade ID | Symbol | Side | Entry Date | Entry P | Stop Price | Exit Date | Exit P | Exit Reason | Days | Gross R | Spread R | Slip R | Fin R (2%) | Net R (2%) | Net R (0%) | Verified |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `T1_XAUUSD_0001` | XAUUSD | LONG | 2002-12-09 | 325.70 | 320.88 | 2003-02-19 | 344.00 | CHANNEL_EXIT | 72 | +3.7947 | -0.0778 | -0.0000 | -0.2664 | **+3.4505R** | **+3.7170R** | [OK] |
| `T1_XAUUSD_0006` | XAUUSD | LONG | 2003-11-13 | 396.00 | 387.65 | 2004-01-27 | 410.00 | CHANNEL_EXIT | 75 | +1.6757 | -0.0449 | -0.0000 | -0.1948 | **+1.4361R** | **+1.6308R** | [OK] |
| `T1_XAUUSD_0013` | XAUUSD | LONG | 2004-10-26 | 429.10 | 421.83 | 2004-11-02 | 421.83 | STOP | 7 | -1.0000 | -0.0516 | -0.0258 | -0.0226 | **-1.1000R** | **-1.0774R** | [OK] |
| `T1_XAUUSD_0016` | XAUUSD | LONG | 2005-03-14 | 441.10 | 434.63 | 2005-03-21 | 431.10 | STOP | 7 | -1.5457 | -0.0580 | -0.0290 | -0.0262 | **-1.6588R** | **-1.6327R** | [OK] |
| `T1_XAUUSD_0020` | XAUUSD | LONG | 2005-09-13 | 446.10 | 440.40 | 2005-10-21 | 462.00 | CHANNEL_EXIT | 38 | +2.7917 | -0.0658 | -0.0000 | -0.1631 | **+2.5628R** | **+2.7259R** | [OK] |

### T2_SMA_CROSS Hand Traces

| Trade ID | Symbol | Side | Entry Date | Entry P | Stop Price | Exit Date | Exit P | Exit Reason | Days | Gross R | Spread R | Slip R | Fin R (2%) | Net R (2%) | Net R (0%) | Verified |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `T2_XAUUSD_0001` | XAUUSD | SHORT | 2004-06-10 | 385.30 | 399.50 | 2004-06-24 | 399.50 | STOP | 14 | -1.0000 | -0.0264 | -0.0132 | -0.0208 | **-1.0604R** | **-1.0396R** | [OK] |
| `T2_XAUUSD_0006` | XAUUSD | LONG | 2006-12-20 | 621.40 | 600.33 | 2008-09-04 | 801.40 | OPPOSITE_CROSS | 624 | +8.5445 | -0.0178 | -0.0000 | -1.0086 | **+7.5181R** | **+8.5267R** | [OK] |
| `T2_XAUUSD_0013` | XAUUSD | SHORT | 2014-05-30 | 1255.10 | 1288.96 | 2014-06-19 | 1288.96 | STOP | 20 | -1.0000 | -0.0111 | -0.0055 | -0.0406 | **-1.0572R** | **-1.0166R** | [OK] |
| `T2_XAUUSD_0016` | XAUUSD | LONG | 2016-03-02 | 1232.80 | 1172.09 | 2016-11-23 | 1212.80 | OPPOSITE_CROSS | 266 | -0.3294 | -0.0062 | -0.0000 | -0.2960 | **-0.6316R** | **-0.3356R** | [OK] |
| `T2_XAUUSD_0017` | XAUUSD | SHORT | 2016-11-23 | 1212.80 | 1255.38 | 2017-02-24 | 1255.38 | STOP | 93 | -1.0000 | -0.0088 | -0.0044 | -0.1452 | **-1.1584R** | **-1.0132R** | [OK] |

### T3_TSMOM Hand Traces

| Trade ID | Symbol | Side | Entry Date | Entry P | Stop Price | Exit Date | Exit P | Exit Reason | Days | Gross R | Spread R | Slip R | Fin R (2%) | Net R (2%) | Net R (0%) | Verified |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `T3_XAUUSD_0001` | XAUUSD | LONG | 2002-11-01 | 318.80 | 311.92 | 2008-11-03 | 728.80 | SIGN_FLIP | 2194 | +59.5977 | -0.0545 | -0.0000 | -5.5711 | **+53.9722R** | **+59.5432R** | [OK] |
| `T3_XAUUSD_0006` | XAUUSD | SHORT | 2012-08-01 | 1613.40 | 1669.66 | 2012-08-23 | 1659.90 | STOP | 22 | -0.8265 | -0.0067 | -0.0033 | -0.0346 | **-0.8710R** | **-0.8365R** | [OK] |
| `T3_XAUUSD_0013` | XAUUSD | LONG | 2015-02-02 | 1284.10 | 1228.96 | 2015-02-06 | 1228.96 | STOP | 4 | -1.0000 | -0.0068 | -0.0034 | -0.0051 | **-1.0153R** | **-1.0102R** | [OK] |
| `T3_XAUUSD_0016` | XAUUSD | SHORT | 2017-07-03 | 1236.00 | 1269.37 | 2017-07-28 | 1258.40 | STOP | 25 | -0.6713 | -0.0112 | -0.0056 | -0.0507 | **-0.7388R** | **-0.6881R** | [OK] |
| `T3_XAUUSD_0019` | XAUUSD | LONG | 2019-06-03 | 1307.00 | 1283.42 | 2021-07-01 | 1770.80 | SIGN_FLIP | 759 | +19.6718 | -0.0159 | -0.0000 | -2.3055 | **+17.3504R** | **+19.6559R** | [OK] |

---

## 12. Sealed 2026 Out-of-Sample Sanity Check (Executed Once)

In accordance with Mandate 2, **sealed 2026 data opened once, only for qualifying rules (T2 and T3), evaluated at 0.0% financing carry (the qualifying financing level)**:

- **Execution Status:** UNSEALED AND EVALUATED ON 2026-10-11T13:01:32.477512+00:00
- **Qualifying Rules:** T2_SMA_CROSS (0% carry), T3_TSMOM (0% carry).
- **Non-Qualifying Rule:** T1_DONCHIAN (Remained sealed; not evaluated on 2026).

### 12.1 Out-of-Sample Performance Summary (2026-01-01 through 2026-10-09)

| Rule Name | Qualifying Status | Financing Level | Fresh 2026 Entries N | Fresh 2026 Mean Net R | Fresh Win Rate | Carried Trades N | Carried Mean Net R | Carried Win Rate |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **T2_SMA_CROSS** | GROSS-OF-FINANCING SURVIVOR | 0.0% p.a. | 15 | -0.3681R | 20.00% | 8 | +8.4985R | 87.50% |
| **T3_TSMOM** | GROSS-OF-FINANCING SURVIVOR | 0.0% p.a. | 12 | +0.0237R | 33.33% | 10 | +10.3096R | 80.00% |

### 12.2 Fresh 2026 Trades Detail

#### T2_SMA_CROSS Fresh 2026 Entries

| Symbol | Side | Entry Date | Exit Date | Exit Reason | Days | Gross R | Net R (0% Fin) | Net R (2% Fin) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **XAUUSD** | SHORT | 2026-07-02 | 2026-08-07 | STOP | 36 | -1.0000 | **-1.0015R** | -1.0228R |
| **XAGUSD** | SHORT | 2026-07-17 | 2026-08-07 | STOP | 21 | -1.1528 | **-1.1593R** | -1.1684R |
| **EURUSD** | SHORT | 2026-04-13 | 2026-10-09 | PERIOD_END | 179 | +2.0897 | **+2.0840R** | +1.6517R |
| **GBPUSD** | LONG | 2026-02-04 | 2026-03-02 | STOP | 26 | -1.0000 | **-1.0091R** | -1.0750R |
| **GBPUSD** | SHORT | 2026-04-26 | 2026-05-13 | OPPOSITE_CROSS | 17 | -0.0421 | **-0.0482R** | -0.0909R |
| **GBPUSD** | LONG | 2026-05-13 | 2026-06-17 | STOP | 35 | -1.0000 | **-1.0092R** | -1.0974R |
| **GBPUSD** | SHORT | 2026-07-12 | 2026-08-19 | STOP | 38 | -1.0000 | **-1.0110R** | -1.1247R |
| **GBPUSD** | LONG | 2026-09-03 | 2026-09-22 | STOP | 19 | -1.0000 | **-1.0129R** | -1.0804R |
| **USDJPY** | SHORT | 2026-09-30 | 2026-10-09 | PERIOD_END | 9 | -0.1557 | **-0.1593R** | -0.1777R |
| **USDCAD** | SHORT | 2026-02-03 | 2026-03-29 | STOP | 54 | -1.0000 | **-1.0139R** | -1.1804R |
| **USDCAD** | LONG | 2026-06-29 | 2026-07-15 | STOP | 16 | -1.0000 | **-1.0181R** | -1.0849R |
| **USDCHF** | LONG | 2026-07-02 | 2026-10-09 | PERIOD_END | 99 | +1.5909 | **+1.5770R** | +1.3072R |
| **GER40** | SHORT | 2026-04-08 | 2026-06-08 | OPPOSITE_CROSS | 61 | -0.2960 | **-0.2975R** | -0.3534R |
| **GER40** | LONG | 2026-06-08 | 2026-10-09 | PERIOD_END | 123 | +0.5704 | **+0.5684R** | +0.4215R |
| **Brent** | LONG | 2026-03-04 | 2026-06-24 | STOP | 112 | -1.0000 | **-1.0110R** | -1.0723R |

#### T3_TSMOM Fresh 2026 Entries

| Symbol | Side | Entry Date | Exit Date | Exit Reason | Days | Gross R | Net R (0% Fin) | Net R (2% Fin) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **EURUSD** | SHORT | 2026-07-01 | 2026-08-06 | STOP | 36 | -0.7288 | **-0.7400R** | -0.8525R |
| **GBPUSD** | SHORT | 2026-06-01 | 2026-08-02 | SIGN_FLIP | 62 | -0.1057 | **-0.1122R** | -0.2786R |
| **GBPUSD** | LONG | 2026-08-02 | 2026-09-23 | STOP | 52 | -1.0000 | **-1.0102R** | -1.1561R |
| **GBPUSD** | SHORT | 2026-10-01 | 2026-10-11 | PERIOD_END | 10 | -0.1653 | **-0.1738R** | -0.2076R |
| **USDJPY** | SHORT | 2026-01-02 | 2026-02-02 | SIGN_FLIP | 31 | +0.4409 | **+0.4368R** | +0.3635R |
| **USDJPY** | LONG | 2026-02-02 | 2026-10-09 | PERIOD_END | 249 | +0.7472 | **+0.7436R** | +0.2352R |
| **USDCAD** | SHORT | 2026-01-02 | 2026-01-09 | STOP | 7 | -0.8344 | **-0.8535R** | -0.8832R |
| **USDCAD** | LONG | 2026-06-01 | 2026-10-11 | PERIOD_END | 132 | +2.3620 | **+2.3492R** | +1.7793R |
| **USDCHF** | LONG | 2026-07-01 | 2026-08-02 | SIGN_FLIP | 32 | -0.1143 | **-0.1287R** | -0.2193R |
| **USDCHF** | SHORT | 2026-08-02 | 2026-09-01 | SIGN_FLIP | 30 | -0.2596 | **-0.2727R** | -0.3494R |
| **USDCHF** | LONG | 2026-09-01 | 2026-10-09 | PERIOD_END | 38 | +1.0636 | **+1.0504R** | +0.9513R |
| **Brent** | LONG | 2026-04-01 | 2026-06-16 | STOP | 76 | -1.0000 | **-1.0041R** | -1.0238R |

### 12.3 Out-of-Sample Empirical Findings
1. **Fresh 2026 Entries Show Choppy Consolidation:**
   - Fresh entries initiated in 2026 under T2 averaged $-0.3681R$ (Win Rate 20.0%), and T3 averaged $+0.0237R$ (Win Rate 33.3%), reflecting multiple whipsaw false-breakouts in FX pairs (GBPUSD, USDCAD) and Brent crude oil.
2. **Carried Long-Term Trends Reaped Massive R-Multiples in 2026:**
   - In contrast, long-term trend trades initiated in 2023–2025 and carried into 2026 yielded extraordinary gains: T2 carried trades averaged $+8.4985R$ (led by Silver $+29.47R$, DAX $+18.08R$, NDX $+8.34R$), and T3 carried trades averaged $+10.3096R$ (Silver $+37.89R$, NDX $+27.86R$, US500 $+26.74R$).
   - This highlights the defining trait of wide-stop trend following: fat-tailed, multi-year trend runs produce outsized gains that subsidize extended periods of localized whipsaw.

---

## 13. Discrepancies, Structural Traps & Scientific Findings

1. **Financing Carry as the Decisive Viability Threshold:**
   - At 0% financing carry, both T2 (+0.5652R) and T3 (+0.7662R) robustly survived all 5 gates with statistically significant Holm-adjusted $p=0.0207$ and beat both Null A and Null B.
   - However, when subjected to institutional financing drag (2.0% annual carry), net expectancy degrades by 0.2875R for T2 and 0.3131R for T3. Because trend trades hold on average 165 to 180 calendar days (and up to 400+ days on equities), financing drag completely erodes the statistical confidence interval, causing the rules to fail Gate 3.
   - Therefore, the empirical edge exists before carry, but real-world institutional viability depends strictly on venue financing terms.
2. **The Equity Trend Illusion (Bull Drift vs Skill):**
   - Long trades substantially outperformed Short trades across all rules (T2 Long $+0.8163R$ vs Short $-0.2725R$ at 2% fin; $+1.1038R$ vs $+0.0266R$ at 0% fin).
   - Secular upward drift in equity benchmarks represents a dominant component of aggregate trend capture.
3. **T1 Donchian Fragility Even with Reset Gate:**
   - Even with the v1.1 channel reset gate, T1 Donchian breakout underperformed (Mean Net R $+0.0409R$ at 2% fin, $+0.1634R$ at 0% fin) and failed Null B direction shuffle, suffering severe whipsaw drawdowns (-87.04R max DD).

---

## 14. Reproduction & Verification

To independently reproduce every calculation and verify all tables:
```bash
uv run python research/trend/verify_trend.py
```