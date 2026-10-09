"""The ICT strategy layer: structure detectors, the silver-bullet windows, replay.

Phase 4 owns the whole package, and the boundary it keeps is the one that makes a
backtest a replay rather than a re-implementation: **the strategy is a pure
function of the closed bars it is given.** Nothing here opens a database, talks to
a broker, or reads a clock. The runtime supervisor (a later phase) is what feeds
bars in and acts on the orders the strategy asks for.

Three modules, in the order a trade is discovered:

* :mod:`engine.strategy.detectors` -- swings, liquidity sweeps, market structure
  shifts and fair value gaps, all computed on closed mid bars.
* :mod:`engine.strategy.silver_bullet` -- the two New York windows, the reference
  levels each window is allowed to know about, and the order lifecycle a confirmed
  setup produces.
* :mod:`engine.strategy.backtest` -- the replay engine, and the checks that prove
  the replay does not repaint.
"""
