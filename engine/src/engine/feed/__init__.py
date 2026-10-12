"""Market data transport: Alpaca ETF quotes and historical daily bars.

Parses Alpaca payloads into `engine.market` primitives and knows nothing about strategy,
risk or journalling.
"""

from engine.feed.alpaca_feed import AlpacaFeed

__all__ = ["AlpacaFeed"]
