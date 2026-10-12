"""The quantitative ETF momentum strategy layer.

* :mod:`engine.strategy.etf_momentum` -- VAR_117 Multi-Asset ETF Momentum Rotation
  with inverse-volatility weighting and Cash Filter.
"""

from engine.strategy.etf_momentum import (
    DEFAULT_UNIVERSE,
    ETF_METADATA,
    PortfolioAllocationSignal,
    TickerScore,
    VAR117Strategy,
)

__all__ = [
    "DEFAULT_UNIVERSE",
    "ETF_METADATA",
    "PortfolioAllocationSignal",
    "TickerScore",
    "VAR117Strategy",
]
