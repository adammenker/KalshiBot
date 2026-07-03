from __future__ import annotations

import sqlite3
from pathlib import Path

from kalshibot.paper_storage import create_paper_tables
from kalshibot.strategies.storage import create_strategy_signals_table


def connect_database(path: Path, *, check_same_thread: bool = True) -> sqlite3.Connection:
    connection = sqlite3.connect(
        path,
        timeout=30,
        check_same_thread=check_same_thread,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA synchronous = NORMAL")
    return connection


def initialize_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect_database(path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                comparison_started_at TEXT NOT NULL,
                comparison_completed_at TEXT NOT NULL,
                label TEXT NOT NULL,
                outcome TEXT NOT NULL,
                kalshi_ticker TEXT NOT NULL,
                kalshi_url TEXT,
                polymarket_token_id TEXT NOT NULL,
                polymarket_url TEXT,
                polymarket_condition_id TEXT,
                polymarket_open_interest TEXT,
                polymarket_open_interest_previous TEXT,
                polymarket_open_interest_delta TEXT,
                polymarket_open_interest_delta_pct TEXT,
                kalshi_request_started_at TEXT NOT NULL,
                kalshi_response_received_at TEXT NOT NULL,
                kalshi_latency_ms TEXT NOT NULL,
                polymarket_request_started_at TEXT NOT NULL,
                polymarket_response_received_at TEXT NOT NULL,
                polymarket_latency_ms TEXT NOT NULL,
                response_skew_ms TEXT NOT NULL,
                first_response_venue TEXT NOT NULL,
                kalshi_buy_price TEXT NOT NULL,
                kalshi_sell_price TEXT,
                kalshi_buy_size TEXT,
                kalshi_buy_depth TEXT NOT NULL,
                kalshi_spread TEXT,
                polymarket_buy_price TEXT NOT NULL,
                polymarket_sell_price TEXT,
                polymarket_buy_size TEXT,
                polymarket_buy_depth TEXT NOT NULL,
                polymarket_spread TEXT,
                depth_window TEXT NOT NULL,
                polymarket_minus_kalshi TEXT NOT NULL,
                kalshi_entry_fee TEXT,
                kalshi_exit_fee TEXT,
                kalshi_round_trip_fee TEXT,
                fee_mode TEXT,
                fee_adjustment TEXT,
                fee_adjusted_edge TEXT,
                kalshi_lower INTEGER NOT NULL,
                passes_filters INTEGER NOT NULL,
                filter_reasons TEXT NOT NULL,
                raw_json TEXT NOT NULL
            )
            """
        )
        ensure_column(
            connection,
            "observations",
            "first_response_venue",
            "TEXT NOT NULL DEFAULT 'unknown'",
        )
        ensure_column(connection, "observations", "kalshi_url", "TEXT")
        ensure_column(connection, "observations", "polymarket_url", "TEXT")
        ensure_column(connection, "observations", "polymarket_condition_id", "TEXT")
        ensure_column(connection, "observations", "polymarket_open_interest", "TEXT")
        ensure_column(connection, "observations", "polymarket_open_interest_previous", "TEXT")
        ensure_column(connection, "observations", "polymarket_open_interest_delta", "TEXT")
        ensure_column(connection, "observations", "polymarket_open_interest_delta_pct", "TEXT")
        ensure_column(connection, "observations", "polymarket_volume", "TEXT")
        ensure_column(connection, "observations", "polymarket_volume_previous", "TEXT")
        ensure_column(connection, "observations", "polymarket_volume_delta", "TEXT")
        ensure_column(connection, "observations", "kalshi_mid_price", "TEXT")
        ensure_column(connection, "observations", "kalshi_mid_previous", "TEXT")
        ensure_column(connection, "observations", "kalshi_mid_delta", "TEXT")
        ensure_column(connection, "observations", "polymarket_mid_price", "TEXT")
        ensure_column(connection, "observations", "polymarket_mid_previous", "TEXT")
        ensure_column(connection, "observations", "polymarket_mid_delta", "TEXT")
        ensure_column(connection, "observations", "polymarket_mid_minus_kalshi_mid", "TEXT")
        ensure_column(connection, "observations", "signal_lookback_minutes", "TEXT")
        ensure_column(connection, "observations", "kalshi_entry_fee", "TEXT")
        ensure_column(connection, "observations", "kalshi_exit_fee", "TEXT")
        ensure_column(connection, "observations", "kalshi_round_trip_fee", "TEXT")
        ensure_column(connection, "observations", "fee_mode", "TEXT")
        ensure_column(connection, "observations", "fee_adjustment", "TEXT")
        ensure_column(connection, "observations", "fee_adjusted_edge", "TEXT")
        connection.execute(
            """
            UPDATE observations
            SET first_response_venue = CASE
                WHEN kalshi_response_received_at < polymarket_response_received_at THEN 'kalshi'
                WHEN polymarket_response_received_at < kalshi_response_received_at THEN 'polymarket'
                ELSE 'tie'
            END
            WHERE first_response_venue = 'unknown'
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                observation_id INTEGER NOT NULL,
                run_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                label TEXT NOT NULL,
                outcome TEXT NOT NULL,
                simulated_entry_venue TEXT NOT NULL,
                simulated_entry_price TEXT NOT NULL,
                comparison_price TEXT NOT NULL,
                edge TEXT NOT NULL,
                FOREIGN KEY(observation_id) REFERENCES observations(id)
            )
            """
        )
        create_strategy_signals_table(connection)
        create_paper_tables(connection)
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_observations_condition_id_id
            ON observations(polymarket_condition_id, id DESC)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_observations_market_observed_at_id
            ON observations(kalshi_ticker, polymarket_token_id, observed_at DESC, id DESC)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_observations_market_outcome_id
            ON observations(kalshi_ticker, polymarket_token_id, outcome, id DESC)
            """
        )


def ensure_column(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
    column_definition: str,
) -> None:
    columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()}
    if column_name not in columns:
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}")
