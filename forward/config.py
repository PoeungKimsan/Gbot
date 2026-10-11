"""Configuration for forward paper-trading runner."""

from __future__ import annotations

from pathlib import Path

PREREG_VERSION = "forward_trend_v1.2"
PREREG_COMMIT = "trend-forward"  # To be pinned upon commit
PREREG_SHA256 = "85570a4e69fcc86f7a3d21589f977180787bc622eada2ee7b07ef324fb785f6c"

FORWARD_DIR = Path("forward")
DATA_DIR = FORWARD_DIR / "data"
BARS_DB_PATH = DATA_DIR / "forward_bars.sqlite"
TREND_DB_PATH = FORWARD_DIR / "forward_trend.db"
SWAPS_CSV_PATH = FORWARD_DIR / "swaps.csv"

SYMBOLS = [
    "XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "USDJPY",
    "AUDUSD", "USDCAD", "USDCHF", "US500", "GER40", "Brent", "NDX",
]
FX_SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF"]

TICKERS_MAP = {
    "XAUUSD": {"ticker": "GC=F", "exchange_tz": "America/New_York"},
    "XAGUSD": {"ticker": "SI=F", "exchange_tz": "America/New_York"},
    "EURUSD": {"ticker": "EURUSD=X", "exchange_tz": "Europe/London"},
    "GBPUSD": {"ticker": "GBPUSD=X", "exchange_tz": "Europe/London"},
    "USDJPY": {"ticker": "USDJPY=X", "exchange_tz": "Europe/London"},
    "AUDUSD": {"ticker": "AUDUSD=X", "exchange_tz": "Europe/London"},
    "USDCAD": {"ticker": "USDCAD=X", "exchange_tz": "Europe/London"},
    "USDCHF": {"ticker": "USDCHF=X", "exchange_tz": "Europe/London"},
    "US500": {"ticker": "^GSPC", "exchange_tz": "America/New_York"},
    "GER40": {"ticker": "^GDAXI", "exchange_tz": "Europe/Berlin"},
    "Brent": {"ticker": "BZ=F", "exchange_tz": "America/New_York"},
    "NDX": {"ticker": "^NDX", "exchange_tz": "America/New_York"},
}

RULES = ["T2_SMA_CROSS", "T3_TSMOM"]
SPREAD_MULTIPLIER = 1.0  # Uses frozen 1.5x research baseline table
SLIPPAGE_STOP_FACTOR = 0.5
LOW_N_THRESHOLD = 200

# Boundary dates
START_BOUNDARY_SIGNAL_DATE = "2026-10-12"
CARRIED_PENDING_CUTOFF_DATE = "2026-10-09"
