# Forward Paper-Trading Harness (`forward_trend_v1`)

**Pre-Registration:** `research/prereg/forward_trend_v1.md`  
**Git Commit:** `296ef4c`  
**Prereg SHA-256:** `ca2b499e3848e7f45ddfce7e0d7854e18db1935a4e93d9fe7c4869987c2f6f33`  
**Harness Status:** ARMED — STOPPED AWAITING USER APPROVAL (Clock Not Started)

---

## 1. Overview & Isolation

This harness runs forward paper-trading for the surviving trend strategies (**T2 50/200 SMA Cross** and **T3 12-Month TSMOM**) across the pre-registered 12-market universe.

- **Zero modification to live engine:** Does not touch `engine/` or `journal.db`.
- **Zero live broker connection:** No API keys, no order placement, pure paper execution.
- **Dedicated SQLite databases:**
  - `forward/data/forward_bars.sqlite`: Raw bar storage, fetch timestamps, geometric sanitization log, roll-gap warnings.
  - `forward/forward_trend.db`: Executed trades, carried trades, open positions, pending orders.

---

## 2. Windows Task Scheduler Setup

### Recommended Run Time: `22:30 UTC` (17:30 New York / 05:30 Indochina Time ICT)

#### Justification for Run Time:
1. **Market Settlement Finality:** US equity index futures (CME: US500, NDX), European index futures (Eurex: GER40), energy (ICE: Brent), and metals (CME: XAUUSD, XAGUSD) settle between 16:00 and 17:00 New York time.
2. **Global FX Rollover:** Spot FX 5:00 PM NY rollover (Tom-Next swap cutoff) occurs at 17:00 NY (21:00 UTC during EDT, 22:00 UTC during EST).
3. **Completeness Invariant:** Running at **22:30 UTC** guarantees that:
   - All 12 markets have definitively concluded their daily session.
   - Official settlement prices are published.
   - The bar timestamped for the completed day is 100% frozen, preventing partial/intraday bar corruption.

### Task Scheduler Configuration:
1. Open Windows Task Scheduler (`taskschd.msc`).
2. Click **Create Basic Task**:
   - **Name:** `Gbot Forward Trend Runner`
   - **Trigger:** Daily at `05:30 AM` (local time, UTC+7) / `22:30 UTC`.
   - **Action:** Start a Program
   - **Program/script:** `C:\Users\Admin\Documents\code\Gbot\forward\run_daily.bat`
   - **Start in:** `C:\Users\Admin\Documents\code\Gbot`

---

## 3. Manual CLI Commands

```bash
# Execute daily bar fetch and forward runner
uv run python -m forward.run_daily

# Generate descriptive Markdown report (outputs to forward/FORWARD_REPORT.md)
uv run python -m forward.report

# Run without re-fetching bars (offline run on existing SQLite bars)
uv run python -m forward.run_daily --no-fetch

# Execute as-of a historical checkpoint date
uv run python -m forward.run_daily --as-of 2025-12-31

# Run with explicit flat start date (positions open prior are sent to carried_trades)
uv run python -m forward.run_daily --flat-start 2026-01-01
```

---

## 4. Test Suite

Run full forward validation suite:
```bash
uv run pytest forward/tests/
```
Included test coverage:
1. `test_regression_research.py`: Proves forward signal engine reproduces research T2/T3 trade lists bit-for-bit across 2002–2025.
2. `test_signals_synthetic.py`: Synthetic test of 50/200 crossover, stop-out, and month-end return sign flip.
3. `test_idempotency.py`: Proves running twice on same date produces identical database rows with zero duplicates.
4. `test_catchup.py`: Proves skipping days and catching up produces identical state as sequential daily runs.
5. `test_weekends_holidays.py`: Proves weekend execution produces zero false signals or crashes.
6. `test_as_of_replay.py`: Proves zero look-ahead bias across 2 years of daily truncation checkpoints.
