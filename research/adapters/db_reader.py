"""Read-only SQLite adapter for live trade journal and database.

Hard Safety Invariants:
1. Connects ONLY via URI format: file:{path}?mode=ro
2. Explicitly executes PRAGMA query_only = ON
3. Never executes write, update, insert, or create statements.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)


def connect_readonly(path: str | Path) -> sqlite3.Connection:
    """Open a SQLite database strictly in read-only mode."""
    p = Path(path).resolve()
    if not p.is_file():
        raise FileNotFoundError(f"Database file not found: {p}")

    # Use URI format to guarantee read-only connection at OS level
    uri = f"{p.as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON;")
    return conn


def get_journal_schema(conn: sqlite3.Connection) -> dict[str, str]:
    """Retrieve all table definitions from sqlite_master without modification."""
    cursor = conn.cursor()
    cursor.execute("SELECT name, sql FROM sqlite_master WHERE type IN ('table', 'view');")
    return {row["name"]: row["sql"] for row in cursor.fetchall()}


def load_live_journal_events(db_path: str | Path) -> list[dict[str, Any]]:
    """Load all journal events in strictly read-only mode."""
    conn = connect_readonly(db_path)
    try:
        cursor = conn.cursor()
        # Verify journal table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='journal';")
        if not cursor.fetchone():
            return []

        cursor.execute(
            """
            SELECT sequence, event_id, event_type, occurred_at,
                   instrument, amount, price_ticks, price_precision, payload
            FROM journal
            ORDER BY sequence ASC;
            """
        )
        events: list[dict[str, Any]] = []
        for row in cursor.fetchall():
            payload_raw = row["payload"]
            payload = json.loads(payload_raw) if payload_raw else {}
            events.append(
                {
                    "sequence": row["sequence"],
                    "event_id": row["event_id"],
                    "event_type": row["event_type"],
                    "occurred_at": row["occurred_at"],
                    "instrument": row["instrument"],
                    "amount": row["amount"],
                    "price_ticks": row["price_ticks"],
                    "price_precision": row["price_precision"],
                    "payload": payload,
                }
            )
        return events
    finally:
        conn.close()


def extract_live_trades_from_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Parse domain events into closed trade records.

    Matches order entries with fills and closing deals/events.
    """
    trades: list[dict[str, Any]] = []
    # If events contain closed trades directly in payloads (or trade records)
    for ev in events:
        etype = ev.get("event_type", "")
        payload = ev.get("payload", {})
        if etype == "TRADE_CLOSED" or "profit" in payload and "exit_price" in payload:
            trades.append(
                {
                    "trade_id": ev.get("event_id"),
                    "occurred_at": ev.get("occurred_at"),
                    "strategy": payload.get("strategy", "Unknown"),
                    "side": payload.get("side", "BUY"),
                    "entry_price": payload.get("entry_price"),
                    "exit_price": payload.get("exit_price"),
                    "stop_loss": payload.get("stop_loss"),
                    "take_profit": payload.get("take_profit"),
                    "profit": payload.get("profit", "0.00"),
                    "r_multiple": payload.get("r_multiple"),
                    "status": "CLOSED",
                }
            )
    return trades
