# Proposal: `trend_v2` Vehicle-Aware Financing Model

**Status:** PROPOSAL ONLY (RESEARCH SPECIFICATION — NOT RUN)  
**Author:** AI Research Assistant  
**Date:** 2026-10-11  
**Target:** Extension of `trend_v1.2` for realistic, vehicle-accurate cost of carry modeling.

---

## 1. Executive Summary & Flaws in `trend_v1.2` Financing

In `trend_v1.0` through `trend_v1.2`, a static flat rate of 2.0% p.a. (with sensitivities at 0.0% and 4.0%) was deducted uniformly across all trades based on holding days:
$$\text{Drag}_{\text{fin}} = \text{Notional} \times \frac{0.02 \times \text{Days}}{365}$$

While serving as a conservative first-pass stress test, this flat model embeds three serious financial distortions:
1. **Sign and Direction Inversion in FX:** In foreign exchange, interest is not a one-way borrowing cost; it is an interest rate differential. Holding a high-yielding currency against a low-yielding currency yields positive carry (e.g. Long USDJPY during 2022–2025 earned ~5% interest differential).
2. **Vehicle Mismatch (Futures vs Cash CFDs):** Futures prices already embed the cost of carry in the forward curve ($F = S e^{(r-q)T}$). Charging a cash borrowing fee on top of a futures price series double-charges for financing.
3. **Regime Blindness:** A flat 2% charge across 2002–2025 ignores the historic zero-interest-rate policy (ZIRP) regime (2008–2021) where global benchmark rates were 0.0%–0.25%, alongside negative policy rates in EUR and CHF.

---

## 2. Corrected Vehicle Financing Models

### 2.1 Spot FX (Cash Rollover / Tom-Next Convention)

In spot FX, an open overnight position represents holding the base currency and borrowing the quote currency (for Longs), or borrowing base and holding quote (for Shorts).

#### Correct Sign Convention:
- **Long Position (Hold Base, Borrow Quote):**
  $$R_{\text{long}} = (r_{\text{base}} - r_{\text{quote}}) - \text{markup}$$
  - If $r_{\text{base}} - r_{\text{quote}} > \text{markup}$, the position **earns** positive swap.
  - If $r_{\text{base}} - r_{\text{quote}} < \text{markup}$, the position **pays** a negative swap charge.
- **Short Position (Borrow Base, Hold Quote):**
  $$R_{\text{short}} = (r_{\text{quote}} - r_{\text{base}}) - \text{markup}$$
  - If $r_{\text{quote}} - r_{\text{base}} > \text{markup}$, the position **earns** positive swap.
  - If $r_{\text{quote}} - r_{\text{base}} < \text{markup}$, the position **pays** a negative swap charge.

*Note on Broker Markup:* The broker applies a financing haircut ($\text{markup} \approx 0.5\% \text{ to } 1.5\%$ p.a.) in both directions, ensuring the broker always takes an asymmetric spread.

#### Settlement & Triple-Swap Day:
Spot FX settles on a $T+2$ basis (except USDCAD on $T+1$). Wednesday overnight rollover carries the position over the weekend (Wednesday to Monday settlement = 3 days). Triple-swap interest is charged/credited on Wednesday 17:00 NY close.

---

### 2.2 Cash CFDs (Metals, Equity Indices, Brent Energy)

For non-FX Contracts for Difference (CFDs), the trader does not buy the physical asset or an interest-bearing foreign deposit. The broker finances the full notional exposure of the underlying instrument against a benchmark rate (e.g. SOFR, EURIBOR, SONIA).

#### CFD Cost Convention:
- **Long CFD (Financed by Broker):**
  $$\text{Swap}_{\text{long}} = -(r_{\text{benchmark}} + \text{markup}) \times \text{Notional} \times \frac{\text{Days}}{360 \text{ or } 365}$$
  *Long CFDs always pay a borrowing cost (negative carry).*
- **Short CFD (Broker Holds Sale Proceeds):**
  $$\text{Swap}_{\text{short}} = +(r_{\text{benchmark}} - \text{markup}) \times \text{Notional} \times \frac{\text{Days}}{360 \text{ or } 365}$$
  - When $r_{\text{benchmark}} > \text{markup}$ (e.g. 2022–2025 at 5% SOFR), short seller earns positive interest on collateral credit.
  - When $r_{\text{benchmark}} \le \text{markup}$ (e.g. 2009–2021 ZIRP), short position receives 0% or pays a net borrow fee.
  - *Hard Broker Floor:* Most retail brokers floor short swap at $\le 0.0$, never crediting interest to retail accounts.

---

### 2.3 Exchange-Traded Futures (CME, ICE, Eurex)

In exchange-traded futures, positions are held on margin ($5\% \text{ to } 15\%$ initial margin). There is **no daily cash borrowing charge**.

#### Critical Rule on Futures Zero-Carry:
> **Zero daily financing is ONLY valid on a continuous back-adjusted price series that explicitly reflects the term structure of the curve (contango or backwardation).**

- If the series is unadjusted or ratio-adjusted without roll yield, assuming 0% carry simply re-states the "0% financing scenario" and hides the true cost of rolling.
- **Roll Mechanics:** When holding long-horizon trends in futures, positions must be rolled before First Notice / Expiration Date:
  $$\text{Cost}_{\text{roll}} = \text{Basis}_{\text{new} - \text{old}} + \text{Execution Spread} + \text{Exchange Fee}$$
  - In **contango** ($F_{\text{next}} > F_{\text{front}}$), longs suffer negative roll drag; shorts earn roll yield.
  - In **backwardation** ($F_{\text{next}} < F_{\text{front}}$, common in Brent), longs earn roll yield; shorts suffer drag.
  - Active rolls per year: Brent ($\sim 12$ rolls/yr), Gold/Silver ($\sim 6$ rolls/yr), Indices ($\sim 4$ rolls/yr).

---

## 3. 12-Market Universe $\times$ Vehicle Implementation Matrix

| Market Symbol | Asset Class | Primary Vehicle Option | Underlying Currency | Benchmark Rate Source | Roll Frequency (if Futures) | Triple Swap Day (if CFD/Spot) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **XAUUSD** | Metal | Spot / CFD or CME Futures (`GC`) | USD | Fed Funds / SOFR | Bi-monthly (Feb/Apr/Jun/Aug/Dec) | Wednesday |
| **XAGUSD** | Metal | Spot / CFD or CME Futures (`SI`) | USD | Fed Funds / SOFR | Bi-monthly (Mar/May/Jul/Sep/Dec) | Wednesday |
| **EURUSD** | FX Pair | Spot FX / CFD | EUR / USD | €STR vs SOFR Differential | N/A (Daily Tom-Next) | Wednesday |
| **GBPUSD** | FX Pair | Spot FX / CFD | GBP / USD | SONIA vs SOFR Differential | N/A (Daily Tom-Next) | Wednesday |
| **USDJPY** | FX Pair | Spot FX / CFD | USD / JPY | SOFR vs TONAR Differential | N/A (Daily Tom-Next) | Wednesday |
| **AUDUSD** | FX Pair | Spot FX / CFD | AUD / USD | AONIA vs SOFR Differential | N/A (Daily Tom-Next) | Wednesday |
| **USDCAD** | FX Pair | Spot FX / CFD | USD / CAD | SOFR vs CORRA Differential | N/A (Daily Tom-Next) | Thursday ($T+1$) |
| **USDCHF** | FX Pair | Spot FX / CFD | USD / CHF | SOFR vs SARON Differential | N/A (Daily Tom-Next) | Wednesday |
| **US500** | Equity Index | CME Futures (`ES`) or CFD | USD | SOFR / 3M US T-Bill | Quarterly (Mar/Jun/Sep/Dec) | Friday / Wednesday |
| **GER40** | Equity Index | Eurex Futures (`FDAX`) or CFD | EUR | €STR / Euribor 3M | Quarterly (Mar/Jun/Sep/Dec) | Friday / Wednesday |
| **Brent** | Energy | ICE Futures (`BZ`) or CFD | USD | SOFR / US T-Bill | Monthly (12 rolls/yr) | Wednesday / Friday |
| **NDX** | Equity Index | CME Futures (`NQ`) or CFD | USD | SOFR / 3M US T-Bill | Quarterly (Mar/Jun/Sep/Dec) | Friday / Wednesday |

---

## 4. Inputs Required from Broker for Real-World Precision

To transition from synthetic policy rate differentials to audited broker execution, the live runner must ingest actual broker swap terms:

```csv
symbol,vehicle,units,swap_long_pts,swap_short_pts,swap_calc_mode,triple_swap_day,broker_markup_pct
XAUUSD,CFD,oz,-18.25,+6.10,POINTS_PER_DAY,Wednesday,1.75
EURUSD,SPOT,currency_pair,-0.62,+0.18,POINTS_PER_DAY,Wednesday,1.25
USDJPY,SPOT,currency_pair,+1.24,-1.85,POINTS_PER_DAY,Wednesday,1.25
US500,CFD,index_pts,-2.45,-1.15,PERCENTAGE_P_A,Friday,2.25
Brent,CFD,bbl,-1.10,-0.85,POINTS_PER_DAY,Wednesday,2.00
```

Required parameters:
1. `swap_long`: Exact points or percentage credited/debited per lot per day for long positions.
2. `swap_short`: Exact points or percentage credited/debited per lot per day for short positions.
3. `swap_calc_mode`: How the broker computes swap (`POINTS_PER_DAY`, `PERCENTAGE_P_A`, or `INTEREST_DIFFERENTIAL`).
4. `units`: Contract lot sizing (e.g. 100 oz Gold, 5,000 oz Silver, 1,000 bbl Oil, 100,000 FX units).
5. `triple_swap_day`: Day of week when 3-day rollover is charged (Wednesday for FX/Metals, Friday for select Cash Indices).

---

## 5. Free Historical Rate Data Sources for Research Backtesting

If historical empirical CFD/FX swap simulation is desired without direct broker history, the following free historical series can be merged:

1. **United States (USD):**
   - Source: Federal Reserve Bank of St. Louis (FRED API)
   - Series: `SOFR` (Secured Overnight Financing Rate, daily from 2018), `DFF` (Effective Federal Funds Rate, daily from 1954), `DTB3` (3-Month Treasury Bill Secondary Market Rate).
2. **Eurozone (EUR):**
   - Source: European Central Bank (ECB Data Portal API)
   - Series: `ESTR` (Euro Short-Term Rate, daily from 2019), `EONIA` (historical daily overnight rate 1999–2019).
3. **United Kingdom (GBP):**
   - Source: Bank of England (BoE Database)
   - Series: `IUDSOIA` (SONIA - Sterling Overnight Index Average, daily from 1997).
4. **Japan (JPY):**
   - Source: Bank of Japan Time-Series Data Search
   - Series: `TONAR` (Tokyo Overnight Average Rate, daily from 2000).
5. **Switzerland (CHF):**
   - Source: Swiss National Bank (SNB Data Portal)
   - Series: `SARON` (Swiss Average Rate Overnight, daily from 1999).
6. **Australia (AUD):**
   - Source: Reserve Bank of Australia (RBA Statistics Table F1)
   - Series: `AONIA` / Interbank Cash Rate Target (daily from 1990).
7. **Canada (CAD):**
   - Source: Bank of Canada (Valet API)
   - Series: `CORRA` (Canadian Overnight Repo Rate Average, daily from 1990).

---

## 6. Pre-Registration & Research Protocol Note

This proposal is strictly a forward-looking design document.
Per the core research invariants of `trend_v1.2`, **zero retroactive changes to the frozen test battery or acceptance gates are permitted**.
Any evaluation incorporating this dynamic vehicle financing model must be codified in a new protocol (`trend_v2.0`) with its own pre-registration hash, frozen before execution.
