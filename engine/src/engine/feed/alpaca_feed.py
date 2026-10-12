"""Market data feed for 12 US ETFs via Alpaca Market Data and local historical cache.

Provides 252-day historical price history for momentum & volatility estimation
and real-time quote updates for portfolio NAV calculation.

Strictly zero-float: all prices and quotes are Decimals.
"""

from __future__ import annotations

import csv
import logging
import os
from decimal import Decimal
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_ETF_UNIVERSE",
    "AlpacaFeed",
    "AlpacaFeedError",
]

DEFAULT_ETF_UNIVERSE: tuple[str, ...] = (
    "SPY",
    "QQQ",
    "IWM",
    "EFA",
    "EEM",
    "IEF",
    "TLT",
    "SHY",
    "LQD",
    "VNQ",
    "GLD",
    "DBC",
)


class AlpacaFeedError(RuntimeError):
    """Raised when market data cannot be retrieved."""


class AlpacaFeed:
    """Manages ETF daily price history and real-time quotes."""

    def __init__(
        self,
        universe: tuple[str, ...] = DEFAULT_ETF_UNIVERSE,
        api_key: str | None = None,
        api_secret: str | None = None,
        data_dir: Path | str | None = None,
    ) -> None:
        self.universe = list(universe)
        self.api_key = api_key or os.getenv("APCA_API_KEY_ID")
        self.api_secret = api_secret or os.getenv("APCA_API_SECRET_KEY")
        self.is_connected = bool(self.api_key and self.api_secret)

        if data_dir is not None:
            self.data_dir = Path(data_dir)
        else:
            # Check default locations
            repo_root = Path(__file__).resolve().parents[4]
            self.data_dir = repo_root / "engine" / "data" / "etf_cache"

        self._cached_daily_prices: dict[str, list[Decimal]] = {}
        self._latest_quotes: dict[str, Decimal] = {}

    def _get_headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.api_key or "",
            "APCA-API-SECRET-KEY": self.api_secret or "",
            "Accept": "application/json",
        }

    def load_historical_prices(self, min_bars: int = 253) -> dict[str, list[Decimal]]:
        """Load trailing daily close prices for all universe assets.

        Reads from cached CSVs to prime the 252-day lookback window immediately.
        """
        prices_by_ticker: dict[str, list[Decimal]] = {}

        for ticker in self.universe:
            # Check for various naming patterns in data_dir
            candidates = [
                self.data_dir / f"{ticker}_daily.csv",
                self.data_dir / f"{ticker}_daily_raw.csv",
                self.data_dir / f"{ticker}.csv",
            ]
            csv_path = next((p for p in candidates if p.is_file()), None)

            if csv_path is None:
                raise AlpacaFeedError(
                    f"No historical price file found for {ticker} in {self.data_dir}"
                )

            closes: list[Decimal] = []
            with csv_path.open(encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    val_str = row.get("Adj Close") or row.get("Close")
                    if val_str:
                        closes.append(Decimal(str(val_str)))

            if len(closes) < min_bars:
                raise AlpacaFeedError(
                    f"Insufficient bars for {ticker} in {csv_path}: got {len(closes)}, "
                    f"need {min_bars}"
                )

            prices_by_ticker[ticker] = closes
            self._latest_quotes[ticker] = closes[-1]

        self._cached_daily_prices = prices_by_ticker
        logger.info(
            "AlpacaFeed primed %d universe ETFs with historical daily bars.",
            len(prices_by_ticker),
        )
        return prices_by_ticker

    async def fetch_latest_quotes(self) -> dict[str, Decimal]:
        """Fetch latest quotes for universe assets (online API or cache fallback)."""
        # Prime from cache first if not yet populated
        if not self._latest_quotes and self.data_dir.exists():
            self.load_historical_prices()

        if not self.is_connected:
            return dict(self._latest_quotes)

        # Connected mode: query Alpaca market data v2
        symbols_str = ",".join(self.universe)
        url = f"https://data.alpaca.markets/v2/stocks/bars/latest?symbols={symbols_str}"

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, headers=self._get_headers())
                if resp.status_code == 200:
                    data = resp.json()
                    bars = data.get("bars", {})
                    for sym, bar in bars.items():
                        if "c" in bar:
                            self._latest_quotes[sym] = Decimal(str(bar["c"]))
                else:
                    logger.warning(
                        "Alpaca data request returned %s; using cache.", resp.status_code
                    )
        except Exception as exc:
            logger.warning("Error fetching live quotes from Alpaca: %s; using cache.", exc)

        return dict(self._latest_quotes)
