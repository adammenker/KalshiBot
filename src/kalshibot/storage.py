from __future__ import annotations

import sqlite3
from pathlib import Path


def initialize_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
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
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id INTEGER NOT NULL,
                observation_id INTEGER NOT NULL,
                run_id TEXT NOT NULL,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                status TEXT NOT NULL,
                label TEXT NOT NULL,
                outcome TEXT NOT NULL,
                kalshi_ticker TEXT NOT NULL,
                polymarket_token_id TEXT NOT NULL,
                simulated_entry_venue TEXT NOT NULL,
                entry_price TEXT NOT NULL,
                entry_comparison_price TEXT NOT NULL,
                entry_edge TEXT NOT NULL,
                entry_fair_price TEXT,
                entry_hold_to_resolution_ev TEXT,
                quantity TEXT NOT NULL,
                entry_fee TEXT,
                fee_mode TEXT,
                fee_adjustment TEXT,
                latest_observation_id INTEGER NOT NULL,
                latest_marked_at TEXT NOT NULL,
                latest_mark_price TEXT,
                latest_exit_fee TEXT,
                latest_gross_unrealized_pnl TEXT,
                latest_unrealized_pnl TEXT,
                latest_fair_price TEXT,
                latest_hold_to_resolution_ev TEXT,
                latest_edge TEXT NOT NULL,
                best_unrealized_pnl TEXT,
                worst_unrealized_pnl TEXT,
                best_hold_to_resolution_ev TEXT,
                worst_hold_to_resolution_ev TEXT,
                observation_count INTEGER NOT NULL,
                FOREIGN KEY(signal_id) REFERENCES paper_signals(id),
                FOREIGN KEY(observation_id) REFERENCES observations(id),
                FOREIGN KEY(latest_observation_id) REFERENCES observations(id)
            )
            """
        )
        ensure_column(connection, "paper_trades", "exit_observation_id", "INTEGER")
        ensure_column(connection, "paper_trades", "exit_price", "TEXT")
        ensure_column(connection, "paper_trades", "entry_fair_price", "TEXT")
        ensure_column(connection, "paper_trades", "entry_hold_to_resolution_ev", "TEXT")
        ensure_column(connection, "paper_trades", "entry_fee", "TEXT")
        ensure_column(connection, "paper_trades", "fee_mode", "TEXT")
        ensure_column(connection, "paper_trades", "fee_adjustment", "TEXT")
        ensure_column(connection, "paper_trades", "latest_exit_fee", "TEXT")
        ensure_column(connection, "paper_trades", "latest_gross_unrealized_pnl", "TEXT")
        ensure_column(connection, "paper_trades", "latest_fair_price", "TEXT")
        ensure_column(connection, "paper_trades", "latest_hold_to_resolution_ev", "TEXT")
        ensure_column(connection, "paper_trades", "best_hold_to_resolution_ev", "TEXT")
        ensure_column(connection, "paper_trades", "worst_hold_to_resolution_ev", "TEXT")
        ensure_column(connection, "paper_trades", "exit_fee", "TEXT")
        ensure_column(connection, "paper_trades", "realized_gross_pnl", "TEXT")
        ensure_column(connection, "paper_trades", "realized_pnl", "TEXT")
        ensure_column(connection, "paper_trades", "close_reason", "TEXT")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_trade_marks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                paper_trade_id INTEGER NOT NULL,
                observation_id INTEGER NOT NULL,
                marked_at TEXT NOT NULL,
                mark_price TEXT,
                unrealized_pnl TEXT,
                gross_unrealized_pnl TEXT,
                entry_fee TEXT,
                exit_fee TEXT,
                fair_price TEXT,
                hold_to_resolution_ev TEXT,
                edge TEXT NOT NULL,
                kalshi_buy_price TEXT NOT NULL,
                kalshi_sell_price TEXT,
                polymarket_buy_price TEXT NOT NULL,
                FOREIGN KEY(paper_trade_id) REFERENCES paper_trades(id),
                FOREIGN KEY(observation_id) REFERENCES observations(id)
            )
            """
        )
        ensure_column(connection, "paper_trade_marks", "gross_unrealized_pnl", "TEXT")
        ensure_column(connection, "paper_trade_marks", "entry_fee", "TEXT")
        ensure_column(connection, "paper_trade_marks", "exit_fee", "TEXT")
        ensure_column(connection, "paper_trade_marks", "fair_price", "TEXT")
        ensure_column(connection, "paper_trade_marks", "hold_to_resolution_ev", "TEXT")


def ensure_column(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
    column_definition: str,
) -> None:
    columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()}
    if column_name not in columns:
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}")
