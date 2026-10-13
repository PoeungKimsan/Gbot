"""CLI entrypoint for scheduled VAR_117 one-shot execution.

Provides deterministic separated two-phase execution:
1. Signal phase (after close on t): refresh, validate, compute, persist, journal.
2. Execute phase (next session ~15:50-15:58 ET): validate guards, submit delta rebalancing.

Exit codes:
- 0: Successful completion
- 1: Runtime/data error
- 2: Execution refused by calendar, window, or risk guards
- 3: Incomplete rebalance / partial broker failure
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import logging
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from engine.execution.alpaca_broker import AlpacaBroker, AlpacaBrokerError
from engine.feed.alpaca_feed import AlpacaFeed
from engine.feed.refresh_data import refresh_etf_universe_data
from engine.market.nyse_calendar import last_completed_session
from engine.runtime.live_trader import (
    ExecutionRefused,
    LiveTrader,
    SignalRefused,
)

logger = logging.getLogger("engine.runtime.oneshot")
NEW_YORK = ZoneInfo("America/New_York")


def _drain_journal(trader: LiveTrader | None) -> None:
    if trader is not None and trader.journal_writer is not None and trader.journal_writer.started:
        trader.journal_writer.stop()
        trader.journal_writer.drain(timeout=dt.timedelta(seconds=2))


def run_signal_phase(
    signal_file: Path | str | None = None,
    data_dir: Path | str | None = None,
    refresh: bool = True,
    allow_non_month_end: bool = False,
    now_ny: dt.datetime | None = None,
    journal_path: Path | str = "journal.db",
) -> int:
    """Execute the signal generation phase."""
    if now_ny is None:
        now_ny = dt.datetime.now(NEW_YORK)
    elif now_ny.tzinfo is None:
        now_ny = now_ny.replace(tzinfo=NEW_YORK)
    else:
        now_ny = now_ny.astimezone(NEW_YORK)

    target_data_dir = (
        Path(data_dir)
        if data_dir
        else Path(os.getenv("ETF_DATA_DIR", "engine/data/etf_cache"))
    )

    # 1. Optionally refresh historical daily bars up to the last completed session
    if refresh:
        as_of = last_completed_session(now_ny)
        logger.info("Refreshing ETF price cache up to completed session %s...", as_of)
        try:
            feed_type = os.getenv("ALPACA_DATA_FEED")
            if not feed_type:
                raise RuntimeError("ALPACA_DATA_FEED environment variable is required for refresh.")
            asyncio.run(
                refresh_etf_universe_data(
                    feed=feed_type,
                    expected_last_date=as_of,
                    target_dir=target_data_dir,
                )
            )
        except Exception as exc:
            logger.error("Data refresh failed: %s", exc)
            return 1

    # 2. Compute and persist signal
    trader: LiveTrader | None = None
    try:
        feed = AlpacaFeed(data_dir=target_data_dir)
        trader = LiveTrader(
            feed=feed,
            broker=AlpacaBroker(mock=True),
            journal_path=journal_path,
            current_time=now_ny,
        )
        if trader.is_degraded:
            logger.error("LiveTrader is degraded: %s", trader.degraded_reason)
            return 1

        sig = trader.generate_and_persist_signal(
            now_ny=now_ny,
            signal_file=signal_file,
            allow_non_month_end=allow_non_month_end,
        )
        logger.info(
            "Signal phase complete for as_of=%s (Top Assets=%s, Cash Weight=%s)",
            sig.as_of_date,
            sig.top_assets,
            sig.cash_weight,
        )
        return 0
    except SignalRefused as exc:
        logger.warning("Signal generation refused: %s", exc)
        return 2
    except Exception as exc:
        logger.error("Signal phase failed: %s", exc)
        return 1
    finally:
        _drain_journal(trader)


def run_execute_phase(
    signal_file: Path | str | None = None,
    data_dir: Path | str | None = None,
    bypass_window: bool = False,
    allow_non_month_end: bool = False,
    now_ny: dt.datetime | None = None,
    journal_path: Path | str = "journal.db",
    broker: AlpacaBroker | None = None,
) -> int:
    """Execute the persisted signal phase."""
    if now_ny is None:
        now_ny = dt.datetime.now(NEW_YORK)
    elif now_ny.tzinfo is None:
        now_ny = now_ny.replace(tzinfo=NEW_YORK)
    else:
        now_ny = now_ny.astimezone(NEW_YORK)

    target_data_dir = (
        Path(data_dir)
        if data_dir
        else Path(os.getenv("ETF_DATA_DIR", "engine/data/etf_cache"))
    )

    trader: LiveTrader | None = None
    try:
        feed = AlpacaFeed(data_dir=target_data_dir)
        trader = LiveTrader(
            feed=feed,
            broker=broker,
            journal_path=journal_path,
            current_time=now_ny,
        )
        if trader.is_degraded:
            logger.error("LiveTrader is degraded: %s", trader.degraded_reason)
            return 1

        orders = asyncio.run(
            trader.execute_persisted_signal(
                now_ny=now_ny,
                signal_file=signal_file,
                bypass_window=bypass_window,
                allow_non_month_end=allow_non_month_end,
            )
        )
        logger.info("Execute phase complete: %d orders submitted.", len(orders))
        return 0
    except ExecutionRefused as exc:
        logger.warning("Execute phase refused: %s", exc)
        return 2
    except AlpacaBrokerError as exc:
        logger.error("Broker execution failure: %s", exc)
        return 3
    except Exception as exc:
        logger.error("Execute phase failed: %s", exc)
        return 1
    finally:
        _drain_journal(trader)


def main() -> int:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="VAR_117 Multi-Asset ETF Momentum Rotation One-Shot Executor"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Phase: signal generation and persistence
    sig_parser = subparsers.add_parser("signal", help="Generate and persist portfolio signal")
    sig_parser.add_argument("--signal-file", help="Custom output path for latest_signal.json")
    sig_parser.add_argument("--data-dir", help="Path to ETF historical price directory")
    sig_parser.add_argument(
        "--no-refresh", action="store_true", help="Skip Alpaca historical bar refresh"
    )
    sig_parser.add_argument(
        "--allow-non-month-end",
        action="store_true",
        help="Allow running on non-month-end session",
    )
    sig_parser.add_argument("--journal-path", default="journal.db", help="Path to SQLite journal")

    # Phase: order execution
    exec_parser = subparsers.add_parser("execute", help="Execute persisted portfolio signal")
    exec_parser.add_argument("--signal-file", help="Custom path to latest_signal.json")
    exec_parser.add_argument("--data-dir", help="Path to ETF historical price directory")
    exec_parser.add_argument(
        "--bypass-window",
        action="store_true",
        help="Bypass [15:50, 15:58) ET execution window",
    )
    exec_parser.add_argument(
        "--allow-non-month-end",
        action="store_true",
        help="Allow running on non-month-end session",
    )
    exec_parser.add_argument("--journal-path", default="journal.db", help="Path to SQLite journal")

    args = parser.parse_args()

    if args.command == "signal":
        return run_signal_phase(
            signal_file=args.signal_file,
            data_dir=args.data_dir,
            refresh=not args.no_refresh,
            allow_non_month_end=args.allow_non_month_end,
            journal_path=args.journal_path,
        )
    if args.command == "execute":
        return run_execute_phase(
            signal_file=args.signal_file,
            data_dir=args.data_dir,
            bypass_window=args.bypass_window,
            allow_non_month_end=args.allow_non_month_end,
            journal_path=args.journal_path,
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
