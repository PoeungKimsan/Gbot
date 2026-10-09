"""Market data primitives: instrument specs, quotes and bars.

Everything in this package is pure: no I/O, no clock reads, no global state.  It is the
innermost layer that `feed/`, `news/` and `strategy/` depend on.
"""
