"""Database initialization and persistence for forward paper-trading harness."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from forward.config import BARS_DB_PATH, TREND_DB_PATH


def get_bars_db(db_path: Path = BARS_DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn


def get_trend_db(db_path: Path = TREND_DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn


def init_bars_db(db_path: Path = BARS_DB_PATH) -> None:
    with get_bars_db(db_path) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS bars (
            symbol TEXT NOT NULL,
            date TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume INTEGER NOT NULL,
            fetch_timestamp TEXT NOT NULL,
            sanitized_hl INTEGER NOT NULL DEFAULT 0,
            roll_gap_warn INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (symbol, date)
        );

        CREATE TABLE IF NOT EXISTS sanitization_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            date TEXT NOT NULL,
            orig_high REAL NOT NULL,
            orig_low REAL NOT NULL,
            new_high REAL NOT NULL,
            new_low REAL NOT NULL,
            logged_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS roll_gap_warnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            date TEXT NOT NULL,
            close_prev REAL NOT NULL,
            close_curr REAL NOT NULL,
            diff_pts REAL NOT NULL,
            atr20 REAL NOT NULL,
            logged_at TEXT NOT NULL
        );
        """)


def init_trend_db(db_path: Path = TREND_DB_PATH) -> None:
    with get_trend_db(db_path) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS trades (
            trade_id TEXT PRIMARY KEY,
            rule TEXT NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry_date TEXT NOT NULL,
            entry_price REAL NOT NULL,
            stop_loss REAL NOT NULL,
            stop_distance REAL NOT NULL,
            exit_date TEXT NOT NULL,
            exit_price REAL NOT NULL,
            exit_reason TEXT NOT NULL,
            holding_days INTEGER NOT NULL,
            gross_r REAL NOT NULL,
            drag_spread_r REAL NOT NULL,
            drag_slippage_r REAL NOT NULL,
            drag_fin0_r REAL NOT NULL,
            drag_fin2_r REAL NOT NULL,
            drag_broker_swap_r REAL,
            net_r_fin0 REAL NOT NULL,
            net_r_fin2 REAL NOT NULL,
            net_r_broker REAL,
            is_stop INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS carried_trades (
            trade_id TEXT PRIMARY KEY,
            rule TEXT NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry_date TEXT NOT NULL,
            entry_price REAL NOT NULL,
            stop_loss REAL NOT NULL,
            stop_distance REAL NOT NULL,
            recorded_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS open_positions (
            position_id TEXT PRIMARY KEY,
            rule TEXT NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry_date TEXT NOT NULL,
            entry_price REAL NOT NULL,
            stop_loss REAL NOT NULL,
            stop_distance REAL NOT NULL,
            episode_id TEXT,
            last_updated TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS pending_signals (
            signal_id TEXT PRIMARY KEY,
            rule TEXT NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            signal_date TEXT NOT NULL,
            trigger_reason TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            as_of_date TEXT NOT NULL,
            executed_at TEXT NOT NULL,
            trades_count INTEGER NOT NULL,
            positions_count INTEGER NOT NULL,
            warnings_count INTEGER NOT NULL
        );
        """)
