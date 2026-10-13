"""Atomic refresh utility for ETF historical price caches and manifest generation.

Downloads total-return adjusted daily bars for the 12-ETF universe,
writes files atomically to a target directory, and commits a cryptographic
manifest.json detailing provenance, date ranges, and SHA-256 hashes.

Strictly zero-float: all prices and statistics are Decimals.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import datetime as dt
import hashlib
import json
import logging
import os
import shutil
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from engine.feed.alpaca_feed import DEFAULT_ETF_UNIVERSE
from engine.market.nyse_calendar import last_completed_session, validate_nyse_sequence

NEW_YORK = ZoneInfo("America/New_York")

logger = logging.getLogger(__name__)

__all__ = [
    "ETFDataManifest",
    "atomic_write_csv",
    "generate_manifest",
    "get_manifest",
    "refresh_etf_universe_data",
]


class ETFDataManifest:
    """Represents a validated cryptographic manifest for an ETF dataset."""

    def __init__(
        self,
        source: str,
        adjustment: str,
        requested_feed: str,
        fetched_at: str,
        symbols: dict[str, dict[str, Any]],
    ) -> None:
        self.source = source
        self.adjustment = adjustment
        self.requested_feed = requested_feed
        self.fetched_at = fetched_at
        self.symbols = symbols

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "adjustment": self.adjustment,
            "requested_feed": self.requested_feed,
            "fetched_at": self.fetched_at,
            "symbols_count": len(self.symbols),
            "symbols": self.symbols,
        }

    def save(self, path: Path) -> None:
        """Write manifest JSON atomically."""
        tmp_path = path.with_suffix(".tmp")
        content = json.dumps(self.to_dict(), indent=2, sort_keys=True)
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(path)


def compute_file_sha256(path: Path) -> str:
    """Calculate SHA-256 hash of a file."""
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def atomic_write_csv(
    target_path: Path,
    rows: list[dict[str, str]],
    fieldnames: list[str] | None = None,
) -> str:
    """Write rows to a CSV file atomically via a temporary file.

    Returns the SHA-256 hash of the written file.
    """
    if fieldnames is None:
        fieldnames = ["Date", "Close", "Adj Close"]

    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.with_name(f"{target_path.name}.tmp")

    with tmp_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        f.flush()
        os.fsync(f.fileno())

    tmp_path.replace(target_path)
    return compute_file_sha256(target_path)


def generate_manifest(
    target_dir: Path,
    source: str = "Alpaca Market Data v2",
    adjustment: str = "split_and_dividend_adjusted",
    requested_feed: str = "sip",
    universe: tuple[str, ...] = DEFAULT_ETF_UNIVERSE,
) -> ETFDataManifest:
    """Inspect all ETF CSVs in target_dir and build a cryptographic manifest."""
    symbols_meta: dict[str, dict[str, Any]] = {}
    now_utc = dt.datetime.now(dt.UTC).isoformat()

    for sym in universe:
        csv_file = target_dir / f"{sym}_daily.csv"
        if not csv_file.is_file():
            continue

        file_hash = compute_file_sha256(csv_file)
        rows_count = 0
        start_date: str | None = None
        last_date: str | None = None

        with csv_file.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                d = r.get("Date")
                if d:
                    if start_date is None:
                        start_date = d
                    last_date = d
                    rows_count += 1

        symbols_meta[sym] = {
            "filename": csv_file.name,
            "source": source,
            "adjustment": adjustment,
            "requested_feed": requested_feed,
            "fetched_at": now_utc,
            "first_date": start_date or "",
            "last_date": last_date or "",
            "rows": rows_count,
            "sha256": file_hash,
        }

    manifest = ETFDataManifest(
        source=source,
        adjustment=adjustment,
        requested_feed=requested_feed,
        fetched_at=now_utc,
        symbols=symbols_meta,
    )
    manifest.save(target_dir / "manifest.json")
    return manifest


def get_manifest(target_dir: Path | str | None = None) -> dict[str, Any] | None:
    """Read and return the latest dataset manifest dictionary for signal journaling."""
    if target_dir is None:
        env_dir = os.getenv("ETF_DATA_DIR")
        if not env_dir:
            return None
        target_dir = Path(env_dir)
    else:
        target_dir = Path(target_dir)

    manifest_file = target_dir / "manifest.json"
    if not manifest_file.is_file():
        return None

    try:
        return json.loads(manifest_file.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Could not read manifest at %s: %s", manifest_file, exc)
        return None


async def fetch_bars_from_alpaca(
    symbol: str,
    api_key: str,
    api_secret: str,
    feed: str,
    start_date: str = "2024-01-01",
    end_date: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, str]]:
    """Fetch total-return adjusted daily bars for a symbol from Alpaca API v2."""
    headers = {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": api_secret,
        "Accept": "application/json",
    }
    url = f"https://data.alpaca.markets/v2/stocks/{symbol}/bars"
    params: dict[str, str] = {
        "timeframe": "1Day",
        "start": start_date,
        "adjustment": "all",  # Split and dividend adjusted
        "feed": feed,
        "limit": "1000",
    }
    if end_date:
        params["end"] = end_date

    logger.info("Requesting daily bars for %s (feed=%s, adjustment=all)...", symbol, feed)

    owns_client = client is None
    c = client or httpx.AsyncClient(timeout=30)
    try:
        resp = await c.get(url, headers=headers, params=params)
        if resp.status_code != 200:
            logger.warning(
                "Alpaca bars request for %s returned %s: %s",
                symbol,
                resp.status_code,
                resp.text,
            )
            return []

        data = resp.json()
        raw_bars = data.get("bars") or []
        rows: list[dict[str, str]] = []
        for b in raw_bars:
            t_str = str(b.get("t", ""))[:10]  # Extract YYYY-MM-DD
            val = b.get("c")
            if val is None:
                continue
            val_str = str(val).strip()
            rows.append({
                "Date": t_str,
                "Close": val_str,
                "Adj Close": val_str,
            })
        return rows
    finally:
        if owns_client:
            await c.aclose()


def _validate_bar_rows(
    symbol: str,
    rows: list[dict[str, str]],
    expected_last_date: str | dt.date,
    min_bars: int = 253,
    now_ny: dt.datetime | None = None,
) -> None:
    """Perform fail-loud integrity validations on raw fetched bar rows."""
    if not rows:
        raise ValueError(f"No bars fetched for {symbol}.")

    exp_last_str = (
        expected_last_date.isoformat()
        if isinstance(expected_last_date, dt.date)
        else expected_last_date
    )
    exp_date_obj = (
        expected_last_date
        if isinstance(expected_last_date, dt.date)
        else dt.date.fromisoformat(expected_last_date)
    )

    if now_ny is not None:
        last_completed = last_completed_session(now_ny)
        if exp_date_obj > last_completed:
            raise ValueError(
                f"Cannot refresh data ending {exp_date_obj}: "
                f"last completed session is {last_completed}. "
                "Refusing partial or in-progress bar."
            )

    dates: list[str] = []
    seen_dates: set[str] = set()

    for idx, r in enumerate(rows):
        d = r.get("Date", "").strip()
        if not d:
            raise ValueError(f"Row {idx} for {symbol} has missing or empty Date.")

        if now_ny is not None:
            last_completed = last_completed_session(now_ny)
            if dt.date.fromisoformat(d) > last_completed:
                raise ValueError(
                    f"Bar on date {d} for {symbol} is after last completed session "
                    f"{last_completed}; refusing partial or in-progress bar."
                )

        if d in seen_dates:
            raise ValueError(f"Duplicate date {d} found in {symbol} bars.")
        seen_dates.add(d)
        dates.append(d)

        # Validate price: reject blank, NaN, or non-positive
        val_str = r.get("Adj Close", "").strip()
        if not val_str or val_str.lower() in ("nan", "none", "null"):
            raise ValueError(f"Blank or NaN price on date {d} for {symbol}.")

        try:
            p = Decimal(val_str)
        except Exception as exc:
            raise ValueError(f"Non-numeric price '{val_str}' on date {d} for {symbol}.") from exc

        if p <= Decimal("0"):
            raise ValueError(f"Non-positive price {p} on date {d} for {symbol}.")

    # Validate sorting and sequence against NYSE calendar
    validate_nyse_sequence(dates)

    # Check unique dates count
    if len(seen_dates) < min_bars:
        raise ValueError(
            f"Insufficient unique dates for {symbol}: got {len(seen_dates)}, need >= {min_bars}."
        )

    # Check expected last date
    if dates[-1] != exp_last_str:
        raise ValueError(
            f"Last date {dates[-1]} for {symbol} does not match expected_last_date {exp_last_str}."
        )


async def refresh_etf_universe_data(
    feed: str | None = None,
    expected_last_date: str | dt.date | None = None,
    target_dir: Path | str | None = None,
    universe: tuple[str, ...] = DEFAULT_ETF_UNIVERSE,
    api_key: str | None = None,
    api_secret: str | None = None,
    start_date: str = "2024-01-01",
    min_bars: int = 253,
    now_ny: dt.datetime | None = None,
) -> ETFDataManifest:
    """Atomically refresh ETF daily bars and produce manifest.json in target_dir.

    Raises ValueError if target directory is unset or resolves to tracked seed data.
    Validates feed in {'iex', 'sip'}.
    Validates expected_last_date is provided and matches fetched data.
    Validates no duplicate dates, unsorted dates, non-positive/NaN prices, or NYSE gaps.
    Stages downloads in a temporary directory so a failed fetch leaves target_dir intact.
    """
    # 1. Validate feed
    feed_resolved = feed or os.getenv("ALPACA_DATA_FEED")
    if not feed_resolved:
        raise ValueError(
            "feed is required (pass explicitly or set ALPACA_DATA_FEED). No silent default allowed."
        )
    feed_resolved = feed_resolved.strip().lower()
    if feed_resolved not in {"iex", "sip"}:
        raise ValueError(f"Invalid feed '{feed_resolved}'. Must be one of ('iex', 'sip').")

    # 2. Validate expected_last_date
    if expected_last_date is None:
        raise ValueError("expected_last_date is required for refresh verification.")

    # 3. Validate target directory
    if target_dir is None:
        env_dir = os.getenv("ETF_DATA_DIR")
        if not env_dir:
            raise ValueError(
                "ETF_DATA_DIR environment variable is unset. Target directory is required."
            )
        out_dir = Path(env_dir)
    else:
        out_dir = Path(target_dir)

    # Refuse to overwrite tracked engine seed cache
    if "engine/data/etf_cache" in str(out_dir.resolve()).replace("\\", "/"):
        raise ValueError(
            "Refused to refresh directly into engine/data/etf_cache. "
            "Target directory must be a dedicated, git-ignored location (e.g. data/etf_cache)."
        )

    key = api_key or os.getenv("APCA_API_KEY_ID")
    secret = api_secret or os.getenv("APCA_API_SECRET_KEY")

    if not key or not secret:
        raise RuntimeError(
            "Alpaca API credentials (APCA_API_KEY_ID, APCA_API_SECRET_KEY) are not configured."
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    staging_dir = out_dir / ".refresh_staging"
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)

    fetched_at = dt.datetime.now(dt.UTC).isoformat()
    symbols_meta: dict[str, dict[str, Any]] = {}
    source = "Alpaca Market Data v2"
    adjustment = "all"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            for sym in universe:
                rows = await fetch_bars_from_alpaca(
                    symbol=sym,
                    api_key=key,
                    api_secret=secret,
                    feed=feed_resolved,
                    start_date=start_date,
                    client=client,
                )
                if len(rows) < min_bars:
                    raise ValueError(
                        f"Insufficient bars fetched for {sym}: got {len(rows)}, need >= {min_bars} "
                        f"for full lookback window. Refusing partial update."
                    )

                # Validate bar integrity (dates, prices, calendar gaps, expected_last_date)
                _validate_bar_rows(
                    symbol=sym,
                    rows=rows,
                    expected_last_date=expected_last_date,
                    min_bars=min_bars,
                    now_ny=now_ny,
                )

                csv_file = staging_dir / f"{sym}_daily.csv"
                sha256_hash = atomic_write_csv(csv_file, rows)
                symbols_meta[sym] = {
                    "filename": f"{sym}_daily.csv",
                    "source": source,
                    "adjustment": adjustment,
                    "requested_feed": feed_resolved,
                    "fetched_at": fetched_at,
                    "first_date": rows[0]["Date"],
                    "last_date": rows[-1]["Date"],
                    "rows": len(rows),
                    "sha256": sha256_hash,
                }
                logger.info(
                    "Staged %d bars for %s (sha256=%s...)",
                    len(rows),
                    sym,
                    sha256_hash[:8],
                )

        manifest = ETFDataManifest(
            source=source,
            adjustment=adjustment,
            requested_feed=feed_resolved,
            fetched_at=fetched_at,
            symbols=symbols_meta,
        )
        manifest.save(staging_dir / "manifest.json")

        # Atomically promote staged files to out_dir
        for staged_file in staging_dir.iterdir():
            target_file = out_dir / staged_file.name
            staged_file.replace(target_file)

        shutil.rmtree(staging_dir, ignore_errors=True)
        logger.info("Saved dataset manifest to %s/manifest.json", out_dir)
        return manifest
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise


def _cli_main() -> None:
    """CLI entrypoint for executing ETF universe data refresh."""
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    parser = argparse.ArgumentParser(
        description="Refresh 12-ETF universe historical daily prices into ETF_DATA_DIR."
    )
    parser.add_argument(
        "--feed",
        default=os.getenv("ALPACA_DATA_FEED"),
        choices=["iex", "sip"],
        help="Alpaca market data feed (iex or sip). Defaults to ALPACA_DATA_FEED env var.",
    )
    parser.add_argument(
        "--expected-last-date",
        required=True,
        help="Expected latest trading day (YYYY-MM-DD), e.g. 2026-10-30.",
    )
    parser.add_argument(
        "--target-dir",
        default=os.getenv("ETF_DATA_DIR"),
        help="Target directory. Defaults to ETF_DATA_DIR env var.",
    )
    parser.add_argument(
        "--start-date",
        default="2024-01-01",
        help="Historical bar start date (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--min-bars",
        type=int,
        default=253,
        help="Minimum required unique trading sessions (default: 253).",
    )

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    try:
        manifest = asyncio.run(
            refresh_etf_universe_data(
                feed=args.feed,
                expected_last_date=args.expected_last_date,
                target_dir=args.target_dir,
                start_date=args.start_date,
                min_bars=args.min_bars,
            )
        )
        print(f"SUCCESS: Refreshed {manifest.to_dict()['symbols_count']} ETF price series.")
    except Exception as exc:
        logger.error("Refresh failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    _cli_main()
