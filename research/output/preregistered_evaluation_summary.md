# Pre-Registered Strategy Variants Evaluation (Fresh Validation Set 2020–2022)

**Dataset Evaluated:** 2020-01-01 to 2022-12-31 (212,492 M5 Bars) | **Total Variants Tried:** 14 (<= 15)

### Results Table
| ID | Variant Name | N | Win Rate | Avg R | 95% CI | PF | MC 95% DD | Null A/B p-val | Verdict |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **V0** | Baseline Live Logic | 525 | 20.4% | -0.0631R | [-0.2844, 0.174] | 0.93 | 70.5% | 0.724/0.97 | **REJECTED / INCONCLUSIVE** |
| **V1** | ATR-Normalized Sweep | 488 | 22.8% | +0.0492R | [-0.1745, 0.2982] | 1.06 | 57.2% | 0.184/0.208 | **REJECTED / INCONCLUSIVE** |
| **V2** | Range-Normalized Sweep | 404 | 25.7% | -0.0868R | [-0.2666, 0.0943] | 0.89 | 55.9% | 0.858/0.954 | **REJECTED / INCONCLUSIVE** |
| **V3** | ATR-Normalized Buffer | 525 | 21.7% | -0.0111R | [-0.2264, 0.222] | 0.99 | 64.3% | 0.452/0.728 | **REJECTED / INCONCLUSIVE** |
| **V4** | Asian Range Width Gate | 284 | 24.3% | -0.1986R | [-0.4188, 0.045] | 0.76 | 59.6% | 0.978/1.0 | **REJECTED / INCONCLUSIVE** |
| **V5** | Equilibrium Target | 528 | 34.3% | -0.0498R | [-0.2035, 0.114] | 0.93 | 57.9% | 0.664/0.924 | **REJECTED / INCONCLUSIVE** |
| **V6** | Fixed 1.5R Target | 527 | 39.5% | -0.1220R | [-0.225, -0.0121] | 0.82 | 59.4% | 0.934/0.996 | **REJECTED / INCONCLUSIVE** |
| **V7** | Fixed 2.0R Target | 524 | 33.8% | -0.0972R | [-0.2204, 0.0249] | 0.87 | 58.1% | 0.888/0.996 | **REJECTED / INCONCLUSIVE** |
| **V8** | Displacement Reclaim Gate | 486 | 26.3% | -0.0008R | [-0.1856, 0.1988] | 1.00 | 54.2% | 0.442/0.712 | **REJECTED / INCONCLUSIVE** |
| **V9** | Breakeven Trigger at +1.0R | 528 | 9.3% | -0.3705R | [-0.4727, -0.2581] | 0.39 | 87.9% | 1.0/1.0 | **REJECTED / INCONCLUSIVE** |
| **V10** | Flipped Contrarian Breakout | 203 | 7.4% | -6.2043R | [-7.3497, -5.1785] | 0.01 | 100.0% | 1.0/1.0 | **REJECTED / INCONCLUSIVE** |
| **V11** | Session Open Auction Bias | 525 | 20.4% | -0.0631R | [-0.2844, 0.174] | 0.93 | 70.5% | 0.724/0.97 | **REJECTED / INCONCLUSIVE** |
| **V12** | Composite Thesis A (ATR + Fixed 1.5R) | 490 | 39.4% | -0.0923R | [-0.2011, 0.0184] | 0.86 | 52.0% | 0.864/0.912 | **REJECTED / INCONCLUSIVE** |
| **V13** | Composite Thesis B (ATR + Equilibrium) | 492 | 38.4% | -0.0177R | [-0.1581, 0.1284] | 0.97 | 47.2% | 0.554/0.55 | **REJECTED / INCONCLUSIVE** |

---

### Final Conclusion Against Strict Acceptance Criteria
> [!WARNING]
> **OUTCOME: INCONCLUSIVE — NO STATISTICAL EDGE DEMONSTRATED ON XAUUSD.**
>
> None of the 14 pre-registered variants satisfied all five acceptance criteria on the independent 2020–2022 validation dataset. Reclaiming the Asian session extreme during London Open without higher-timeframe confirmation does not produce a statistically verifiable edge after realistic execution friction.