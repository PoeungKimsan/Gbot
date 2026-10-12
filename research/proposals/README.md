# Safety Controls Patch Proposal

This directory contains a non-applied patch proposal addressing critical risk invariants discovered during the audit of `live_trader.py`:

1. **Minimum Stop Floor**:
   - `min_stop = max(broker_minimum, 3 * spread, min_stop_distance)` ($0.60 on XAUUSD at 0.20 spread).
   - Rejects any signal whose stop distance is below this realism floor, preventing catastrophic leverage spikes and unrealistic micro-stop fills.
2. **Leverage Cap by Equity**:
   - `max_leverage_ratio: "30.0"`
   - Clamps or refuses orders where notional exposure exceeds 30x equity (preventing the 95.2x–195.2x leverage exposure observed on $10k accounts with $0.21 stops).
3. **Risk % Recomputation upon Clamping**:
   - Recomputes effective dollar risk and effective risk % when lots are clamped by either the leverage cap or the venue 10.00-lot ceiling.
   - Emits structured warnings logging before/after risk % (e.g. from 1.00% down to 0.21%).
4. **Clean Signal Rejection & Logging**:
   - Unviable signals (below venue minimum 0.01 lot, or below minimum stop distance) are safely rejected with zero order placed.

## Artifacts

- `safety_controls_patch.diff`: Unified git diff proposal (do not apply to production).
- `test_safety_controls_patch.py`: Fully functional unit test suite validating all 4 safety controls. Run via:
  ```bash
  uv run pytest research/proposals/test_safety_controls_patch.py
  ```
