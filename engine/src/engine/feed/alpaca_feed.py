"""Market data feed for 12 US ETFs via Alpaca Market Data and local historical cache.

Provides 252-day historical price history for momentum & volatility estimation
and real-time quote updates for portfolio NAV calculation.

Strictly zero-float: all prices and quotes are Decimals.
"""

from __future__ import annotations

import csv
import dataclasses
import datetime as dt
import hashlib
import json
import logging
import os
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_ETF_UNIVERSE",
    "AlpacaFeed",
    "AlpacaFeedError",
    "Quote",
]

NEW_YORK = ZoneInfo("America/New_York")

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
    """Raised when market data cannot be retrieved or fails validation."""


@dataclasses.dataclass(frozen=True, slots=True)
class Quote:
    """Represents a validated, real-time market quote."""

    price: Decimal
    timestamp: dt.datetime
    feed: str
    source: str

    def __init__(
        self,
        price: Decimal,
        timestamp: dt.datetime | None = None,
        feed: str | None = None,
        source: str = "alpaca",
        *,
        ts: dt.datetime | None = None,
    ) -> None:
        effective_ts = timestamp if timestamp is not None else ts
        if effective_ts is None:
            raise ValueError("Quote requires timestamp or ts")
        if not feed:
            raise ValueError("Quote requires feed; no default allowed.")
        object.__setattr__(self, "price", price)
        object.__setattr__(self, "timestamp", effective_ts)
        object.__setattr__(self, "feed", feed)
        object.__setattr__(self, "source", source)

    @property
    def ts(self) -> dt.datetime:
        return self.timestamp


class AlpacaFeed:
    """Manages ETF daily price history and real-time quotes."""

    def __init__(
        self,
        universe: tuple[str, ...] = DEFAULT_ETF_UNIVERSE,
        api_key: str | None = None,
        api_secret: str | None = None,
        data_dir: Path | str | None = None,
        feed: str | None = None,
    ) -> None:
        self.universe = list(universe)
        self.api_key = api_key or os.getenv("APCA_API_KEY_ID")
        self.api_secret = api_secret or os.getenv("APCA_API_SECRET_KEY")
        self.is_connected = bool(self.api_key and self.api_secret)

        feed_resolved = feed or os.getenv("ALPACA_DATA_FEED")
        if not feed_resolved:
            raise AlpacaFeedError(
                "ALPACA_DATA_FEED environment variable is unset and no feed parameter provided. "
                "feed is required and must be 'iex' or 'sip'."
            )
        feed_clean = feed_resolved.strip().lower()
        if feed_clean not in {"iex", "sip"}:
            raise AlpacaFeedError(
                f"Invalid feed '{feed_clean}'. Feed must be 'iex' or 'sip'."
            )
        self.feed_name = feed_clean

        if data_dir is not None:
            self.data_dir = Path(data_dir)
        elif os.getenv("ETF_DATA_DIR"):
            self.data_dir = Path(os.getenv("ETF_DATA_DIR"))
        else:
            raise AlpacaFeedError(
                "ETF_DATA_DIR environment variable is unset. "
                "AlpacaFeed requires an explicit data_dir or ETF_DATA_DIR environment variable."
            )

        self._cached_daily_prices: dict[str, list[tuple[dt.date, Decimal]]] = {}
        self._latest_quotes: dict[str, Quote] = {}

    @staticmethod
    def _compute_file_sha256(path: Path) -> str:
        """Calculate SHA-256 hash of a file normalized to LF line endings."""
        hasher = hashlib.sha256()
        with path.open("rb") as f:
            content = f.read()
        canonical = content.replace(b"\r\n", b"\n")
        hasher.update(canonical)
        return hasher.hexdigest()

    def _get_headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.api_key or "",
            "APCA-API-SECRET-KEY": self.api_secret or "",
            "Accept": "application/json",
        }

    def load_historical_prices(
        self,
        min_bars: int = 253,
        expected_as_of_date: dt.date | str | None = None,
    ) -> dict[str, list[tuple[dt.date, Decimal]]]:
        """Load trailing daily close prices (date, adj_close) for all universe assets.

        Reads from cached CSVs to prime lookback windows.
        Verifies cryptographic integrity against manifest.json.
        Raises AlpacaFeedError if manifest is missing, stale, or hash mismatched.
        Requires 'Adj Close' column strictly (no fallback to Close).
        Rejects duplicate dates, unsorted dates, non-positive/NaN prices, and daily returns > 25%.
        Never seeds live quotes from historical adjusted closes.
        """
        manifest_path = self.data_dir / "manifest.json"
        if not manifest_path.is_file():
            raise AlpacaFeedError(
                f"Missing manifest.json in {self.data_dir}. Cannot verify dataset integrity."
            )

        try:
            manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
            symbols_meta = manifest_data.get("symbols", {})
        except Exception as exc:
            raise AlpacaFeedError(
                f"Invalid or corrupted manifest.json in {self.data_dir}: {exc}"
            ) from exc

        exp_str = (
            expected_as_of_date.isoformat()
            if isinstance(expected_as_of_date, dt.date)
            else (str(expected_as_of_date) if expected_as_of_date else None)
        )

        prices_by_ticker: dict[str, list[tuple[dt.date, Decimal]]] = {}

        for ticker in self.universe:
            csv_path = self.data_dir / f"{ticker}_daily.csv"
            if not csv_path.is_file():
                raise AlpacaFeedError(
                    f"No historical price file found for {ticker} in {self.data_dir}"
                )

            meta = symbols_meta.get(ticker)
            if not meta or "sha256" not in meta:
                raise AlpacaFeedError(
                    f"Ticker {ticker} is missing from manifest.json in {self.data_dir}"
                )

            # Check manifest freshness against expected signal date
            if exp_str is not None:
                meta_last_date = meta.get("last_date", "")
                if meta_last_date != exp_str:
                    raise AlpacaFeedError(
                        f"Dataset is stale: manifest last_date '{meta_last_date}' for {ticker} "
                        f"does not match expected signal date '{exp_str}'."
                    )

            actual_sha = self._compute_file_sha256(csv_path)
            expected_sha = meta["sha256"]
            if actual_sha != expected_sha:
                raise AlpacaFeedError(
                    f"SHA-256 hash mismatch for {ticker} in {csv_path}: "
                    f"expected {expected_sha}, got {actual_sha}"
                )

            bars: list[tuple[dt.date, Decimal]] = []
            seen_dates: set[dt.date] = set()
            prev_date: dt.date | None = None

            with csv_path.open(encoding="utf-8") as f:
                reader = csv.DictReader(f)
                if not reader.fieldnames or "Adj Close" not in reader.fieldnames:
                    raise AlpacaFeedError(
                        f"Missing required 'Adj Close' column for {ticker} in {csv_path}."
                    )

                for row_idx, row in enumerate(reader):
                    d_raw = row.get("Date", "").strip()
                    if not d_raw:
                        raise AlpacaFeedError(
                            f"Missing Date in row {row_idx} for {ticker} in {csv_path}."
                        )
                    try:
                        bar_date = dt.date.fromisoformat(d_raw)
                    except Exception as exc:
                        raise AlpacaFeedError(
                            f"Invalid Date format '{d_raw}' in {csv_path}: {exc}"
                        ) from exc

                    if bar_date in seen_dates:
                        raise AlpacaFeedError(
                            f"Duplicate date {bar_date.isoformat()} found for {ticker} "
                            f"in {csv_path}."
                        )
                    if prev_date is not None and bar_date <= prev_date:
                        raise AlpacaFeedError(
                            f"Unsorted date sequence for {ticker} in {csv_path}: "
                            f"{bar_date.isoformat()} follows {prev_date.isoformat()}."
                        )
                    seen_dates.add(bar_date)
                    prev_date = bar_date

                    val_str = row.get("Adj Close", "").strip()
                    if not val_str or val_str.lower() in ("nan", "none", "null"):
                        raise AlpacaFeedError(
                            f"Blank or NaN price for {ticker} on date {bar_date.isoformat()} "
                            f"in {csv_path}."
                        )
                    try:
                        p = Decimal(val_str)
                    except Exception as exc:
                        raise AlpacaFeedError(
                            f"Non-numeric price '{val_str}' for {ticker} on "
                            f"{bar_date.isoformat()}: {exc}"
                        ) from exc

                    if p <= Decimal("0"):
                        raise AlpacaFeedError(
                            f"Non-positive price {p} for {ticker} on date {bar_date.isoformat()} "
                            f"in {csv_path}."
                        )

                    bars.append((bar_date, p))

            if len(bars) < min_bars:
                raise AlpacaFeedError(
                    f"Insufficient bars for {ticker} in {csv_path}: got {len(bars)}, "
                    f"need {min_bars}"
                )

            # Check for excessive daily returns > 25% (potential split/dividend defect)
            for i in range(1, len(bars)):
                p_prev = bars[i - 1][1]
                p_curr = bars[i][1]
                ret = abs((p_curr - p_prev) / p_prev)
                if ret > Decimal("0.25"):
                    raise AlpacaFeedError(
                        f"Daily return {ret * Decimal('100'):.2f}% exceeds 25% threshold "
                        f"for {ticker} on {bars[i][0].isoformat()} (requires operator review)."
                    )

            prices_by_ticker[ticker] = bars

        self._cached_daily_prices = prices_by_ticker
        logger.info(
            "AlpacaFeed primed %d universe ETFs with historical daily bars.",
            len(prices_by_ticker),
        )
        return prices_by_ticker

    async def fetch_latest_quotes(
        self,
        current_time: dt.datetime | None = None,
        max_age_seconds: int = 900,  # 15 minutes
    ) -> dict[str, Quote]:
        """Fetch latest quotes for universe assets from Alpaca Market Data v2.

        Logs the feed per quote.
        Refuses stale or prior-session quotes.
        Never seeds quotes from adjusted closes.
        """
        now_ny = (current_time or dt.datetime.now(NEW_YORK)).astimezone(NEW_YORK)

        if not self.is_connected:
            # Offline path: return Quote objects with source="cache"
            quotes: dict[str, Quote] = {}
            for sym in self.universe:
                if sym in self._latest_quotes:
                    existing = self._latest_quotes[sym]
                    quotes[sym] = Quote(
                        price=existing.price,
                        timestamp=existing.timestamp,
                        feed=existing.feed or self.feed_name,
                        source="cache",
                    )
                elif self._cached_daily_prices.get(sym):
                    last_bar = self._cached_daily_prices[sym][-1]
                    d, p = last_bar[0], last_bar[1]
                    quotes[sym] = Quote(
                        price=p,
                        timestamp=dt.datetime.combine(d, dt.time(16, 0), tzinfo=NEW_YORK),
                        feed=self.feed_name,
                        source="cache",
                    )
            return quotes

        symbols_str = ",".join(self.universe)
        url = f"https://data.alpaca.markets/v2/stocks/bars/latest?symbols={symbols_str}&feed={self.feed_name}"

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, headers=self._get_headers())
                if resp.status_code == 200:
                    data = resp.json()
                    bars = data.get("bars", {})
                    for sym, bar in bars.items():
                        if "c" not in bar or "t" not in bar:
                            continue
                        price_dec = Decimal(str(bar["c"]))
                        t_str = bar["t"]
                        # Parse ISO timestamp
                        q_ts = dt.datetime.fromisoformat(t_str.replace("Z", "+00:00"))
                        q_ts_ny = q_ts.astimezone(NEW_YORK)

                        # Check staleness: refuse prior-session quotes or quotes older than max_age
                        if q_ts_ny.date() < now_ny.date():
                            raise AlpacaFeedError(
                                f"Stale quote for {sym}: timestamp {q_ts.isoformat()} is from "
                                f"prior session {q_ts_ny.date().isoformat()} "
                                f"(current session: {now_ny.date().isoformat()})."
                            )

                        age_seconds = (now_ny - q_ts_ny).total_seconds()
                        if age_seconds > max_age_seconds:
                            raise AlpacaFeedError(
                                f"Stale quote for {sym}: quote age {age_seconds:.0f}s exceeds "
                                f"{max_age_seconds}s limit."
                            )

                        quote_obj = Quote(
                            price=price_dec,
                            timestamp=q_ts,
                            feed=self.feed_name,
                            source="Alpaca Market Data v2",
                        )
                        self._latest_quotes[sym] = quote_obj
                        logger.info(
                            "Quote for %s: price=%s, ts=%s, feed=%s, source=%s",
                            sym,
                            quote_obj.price,
                            quote_obj.timestamp.isoformat(),
                            quote_obj.feed,
                            quote_obj.source,
                        )
                else:
                    raise AlpacaFeedError(
                        f"Alpaca market data request returned status {resp.status_code}: "
                        f"{resp.text}"
                    )
        except AlpacaFeedError:
            raise
        except Exception as exc:
            raise AlpacaFeedError(f"Error fetching live quotes from Alpaca: {exc}") from exc

        return dict(self._latest_quotes)

    def set_mock_quotes(self, quotes: dict[str, Quote | Decimal]) -> None:
        """Manually supply mock quotes for simulated execution and testing."""
        now_utc = dt.datetime.now(dt.UTC)
        for sym, val in quotes.items():
            if isinstance(val, Quote):
                self._latest_quotes[sym] = val
            else:
                self._latest_quotes[sym] = Quote(
                    price=val,
                    timestamp=now_utc,
                    feed=self.feed_name,
                    source="mock",
                )
