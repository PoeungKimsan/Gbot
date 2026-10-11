"""Entrypoint for daily forward paper-trading execution: python -m forward.run_daily."""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys

from forward.feed import fetch_and_store_bars
from forward.runner import run_forward_day

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("forward.run_daily")


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily forward paper-trading execution.")
    parser.add_argument(
        "--fetch",
        action="store_true",
        default=True,
        help="Fetch latest daily bars from Yahoo Finance prior to execution (default: True)",
    )
    parser.add_argument(
        "--no-fetch",
        dest="fetch",
        action="store_false",
        help="Skip fetching bars, run on existing database bars",
    )
    parser.add_argument(
        "--as-of",
        type=str,
        default=None,
        help="Execute as of a specific date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--flat-start",
        type=str,
        default=None,
        help="Set flat start date (YYYY-MM-DD). Positions open before this date are moved to carried_trades.",
    )

    args = parser.parse_args()

    as_of_date = dt.date.fromisoformat(args.as_of) if args.as_of else None
    flat_start_date = dt.date.fromisoformat(args.flat_start) if args.flat_start else None

    logger.info("Starting Daily Forward Paper-Runner...")
    if args.fetch:
        logger.info("Fetching latest market bars...")
        fetch_and_store_bars(as_of_date=as_of_date)

    result = run_forward_day(as_of_date=as_of_date, flat_start_date=flat_start_date)
    logger.info("Daily run completed successfully: %s", result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
