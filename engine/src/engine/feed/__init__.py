"""Market data transport: OANDA v20 REST and streaming.

This package owns every byte that crosses the network.  It parses OANDA payloads into
`engine.market` primitives and knows nothing about strategy, risk or journalling.
"""
