"""Risk policy: sizing, per-day drawdown, and the kill-switch.

Every number in this package is a :class:`~decimal.Decimal`. Risk is where money is
promised, so the float-free rule from ``AGENTS.md`` section 2.1 applies here in its
strictest form: a float that survived ingestion is refused at this boundary rather
than rounded into a position size.
"""
