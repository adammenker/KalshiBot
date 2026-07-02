from __future__ import annotations

import sqlite3
from pathlib import Path

from kalshibot.runtime.active_pairs import create_active_market_pairs_table


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
        create_paper_trades_table(connection)
        ensure_paper_trades_signal_id_nullable(connection)
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
        ensure_column(connection, "paper_trades", "strategy_id", "TEXT")
        ensure_column(connection, "paper_trades", "strategy_version", "TEXT")
        ensure_column(connection, "paper_trades", "strategy_signal_id", "INTEGER")
        ensure_column(connection, "paper_trades", "fair_value_provider", "TEXT")
        ensure_column(connection, "paper_trades", "entry_policy", "TEXT")
        ensure_column(connection, "paper_trades", "exit_policy", "TEXT")
        ensure_column(connection, "paper_trades", "side", "TEXT")
        ensure_column(connection, "paper_trades", "direction", "TEXT")
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
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                observation_id INTEGER NOT NULL,
                run_id TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                strategy_id TEXT NOT NULL,
                strategy_version TEXT NOT NULL,
                signal_type TEXT NOT NULL,
                label TEXT,
                outcome TEXT,
                kalshi_ticker TEXT NOT NULL,
                polymarket_token_id TEXT NOT NULL,
                polymarket_condition_id TEXT,
                side TEXT,
                direction TEXT,
                score TEXT,
                confidence TEXT,
                fair_value TEXT,
                entry_price TEXT,
                mark_price TEXT,
                edge TEXT,
                fee_adjusted_edge TEXT,
                kalshi_buy_price TEXT,
                kalshi_sell_price TEXT,
                polymarket_buy_price TEXT,
                polymarket_mid_price TEXT,
                kalshi_mid_price TEXT,
                polymarket_mid_minus_kalshi_mid TEXT,
                polymarket_mid_delta TEXT,
                kalshi_mid_delta TEXT,
                polymarket_open_interest TEXT,
                polymarket_open_interest_delta TEXT,
                polymarket_volume TEXT,
                polymarket_volume_delta TEXT,
                reasons_json TEXT NOT NULL,
                rejection_reasons_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(observation_id) REFERENCES observations(id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_strategy_signals_observation_id
            ON strategy_signals(observation_id)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_strategy_signals_strategy_id
            ON strategy_signals(strategy_id)
            """
        )
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
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_paper_trades_open_market
            ON paper_trades(status, label, outcome, kalshi_ticker, polymarket_token_id)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_paper_trades_strategy_id
            ON paper_trades(strategy_id)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_paper_trades_strategy_signal_id
            ON paper_trades(strategy_signal_id)
            """
        )
        create_active_market_pairs_table(connection)


def create_paper_trades_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS paper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER,
            strategy_signal_id INTEGER,
            strategy_id TEXT,
            strategy_version TEXT,
            fair_value_provider TEXT,
            entry_policy TEXT,
            exit_policy TEXT,
            side TEXT,
            direction TEXT,
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
            exit_observation_id INTEGER,
            exit_price TEXT,
            exit_fee TEXT,
            realized_gross_pnl TEXT,
            realized_pnl TEXT,
            close_reason TEXT,
            FOREIGN KEY(signal_id) REFERENCES paper_signals(id),
            FOREIGN KEY(strategy_signal_id) REFERENCES strategy_signals(id),
            FOREIGN KEY(observation_id) REFERENCES observations(id),
            FOREIGN KEY(latest_observation_id) REFERENCES observations(id)
        )
        """
    )


def ensure_paper_trades_signal_id_nullable(connection: sqlite3.Connection) -> None:
    columns = connection.execute("PRAGMA table_info(paper_trades)").fetchall()
    signal_id = next((column for column in columns if column[1] == "signal_id"), None)
    if signal_id is None or signal_id[3] == 0:
        return

    connection.execute("ALTER TABLE paper_trades RENAME TO paper_trades_old")
    create_paper_trades_table(connection)
    old_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(paper_trades_old)").fetchall()
    }
    new_columns = {row[1] for row in connection.execute("PRAGMA table_info(paper_trades)").fetchall()}
    shared_columns = sorted(old_columns & new_columns)
    column_list = ", ".join(shared_columns)
    connection.execute(
        f"INSERT INTO paper_trades ({column_list}) SELECT {column_list} FROM paper_trades_old"
    )
    connection.execute("DROP TABLE paper_trades_old")


def ensure_column(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
    column_definition: str,
) -> None:
    columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()}
    if column_name not in columns:
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}")
