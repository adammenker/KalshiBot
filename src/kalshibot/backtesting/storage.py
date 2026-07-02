from __future__ import annotations

from decimal import Decimal
import sqlite3
from pathlib import Path
from typing import Any

from kalshibot.spreads import MarketPair


def initialize_historical_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS historical_prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL,
                outcome TEXT NOT NULL,
                kalshi_ticker TEXT NOT NULL,
                polymarket_token_id TEXT NOT NULL,
                source TEXT NOT NULL,
                ts INTEGER NOT NULL,
                price TEXT NOT NULL,
                bid_price TEXT,
                ask_price TEXT,
                volume TEXT,
                open_interest TEXT,
                raw_json TEXT NOT NULL,
                UNIQUE(kalshi_ticker, polymarket_token_id, source, ts)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS historical_aligned_prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL,
                outcome TEXT NOT NULL,
                kalshi_ticker TEXT NOT NULL,
                polymarket_token_id TEXT NOT NULL,
                ts INTEGER NOT NULL,
                kalshi_price TEXT NOT NULL,
                kalshi_bid_price TEXT,
                kalshi_ask_price TEXT NOT NULL,
                polymarket_price TEXT NOT NULL,
                spread TEXT NOT NULL,
                UNIQUE(kalshi_ticker, polymarket_token_id, ts)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS backtest_runs (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                db_path TEXT NOT NULL,
                min_edge TEXT NOT NULL,
                hold_period_minutes INTEGER NOT NULL,
                slippage TEXT NOT NULL,
                trade_count INTEGER NOT NULL,
                winning_trade_count INTEGER NOT NULL,
                total_pnl TEXT NOT NULL,
                average_pnl TEXT,
                max_drawdown TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS backtest_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                label TEXT NOT NULL,
                outcome TEXT NOT NULL,
                kalshi_ticker TEXT NOT NULL,
                polymarket_token_id TEXT NOT NULL,
                entry_ts INTEGER NOT NULL,
                exit_ts INTEGER NOT NULL,
                entry_price TEXT NOT NULL,
                exit_price TEXT NOT NULL,
                entry_edge TEXT NOT NULL,
                exit_edge TEXT NOT NULL,
                pnl TEXT NOT NULL,
                exit_reason TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES backtest_runs(id)
            )
            """
        )


def save_kalshi_history(
    connection: sqlite3.Connection,
    pair: MarketPair,
    payload: dict[str, Any],
) -> int:
    count = 0
    for candle in payload.get("candlesticks", []):
        ts = int(candle["end_period_ts"])
        bid_price = candle_price(candle.get("yes_bid"), "close_dollars")
        ask_price = candle_price(candle.get("yes_ask"), "close_dollars")
        price = ask_price or candle_price(candle.get("price"), "close_dollars")
        if price is None:
            continue
        connection.execute(
            """
            INSERT OR REPLACE INTO historical_prices (
                label, outcome, kalshi_ticker, polymarket_token_id, source, ts,
                price, bid_price, ask_price, volume, open_interest, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pair.label,
                pair.outcome,
                pair.kalshi_ticker,
                pair.polymarket_token_id,
                "kalshi",
                ts,
                str(price),
                str(bid_price) if bid_price is not None else None,
                str(ask_price) if ask_price is not None else None,
                str(candle.get("volume_fp")) if candle.get("volume_fp") is not None else None,
                str(candle.get("open_interest_fp"))
                if candle.get("open_interest_fp") is not None
                else None,
                repr(candle),
            ),
        )
        count += 1
    return count


def save_polymarket_history(
    connection: sqlite3.Connection,
    pair: MarketPair,
    payload: dict[str, Any],
) -> int:
    count = 0
    for point in payload.get("history", []):
        ts = int(point["t"])
        price = Decimal(str(point["p"]))
        connection.execute(
            """
            INSERT OR REPLACE INTO historical_prices (
                label, outcome, kalshi_ticker, polymarket_token_id, source, ts,
                price, bid_price, ask_price, volume, open_interest, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pair.label,
                pair.outcome,
                pair.kalshi_ticker,
                pair.polymarket_token_id,
                "polymarket",
                ts,
                str(price),
                None,
                None,
                None,
                None,
                repr(point),
            ),
        )
        count += 1
    return count


def align_pair_history(
    connection: sqlite3.Connection,
    pair: MarketPair,
    *,
    tolerance_seconds: int = 30,
) -> int:
    kalshi_rows = connection.execute(
        """
        SELECT
            ts, price, bid_price, COALESCE(ask_price, price) AS ask_price
        FROM historical_prices
        WHERE source = 'kalshi'
            AND kalshi_ticker = ?
            AND polymarket_token_id = ?
        ORDER BY ts
        """,
        (pair.kalshi_ticker, pair.polymarket_token_id),
    ).fetchall()
    aligned_count = 0
    for row in kalshi_rows:
        polymarket_row = nearest_polymarket_history_row(
            connection,
            pair,
            ts=int(row[0]),
            tolerance_seconds=tolerance_seconds,
        )
        if polymarket_row is None:
            continue
        spread = Decimal(str(polymarket_row[0])) - Decimal(str(row[3]))
        connection.execute(
            """
            INSERT OR REPLACE INTO historical_aligned_prices (
                label, outcome, kalshi_ticker, polymarket_token_id, ts,
                kalshi_price, kalshi_bid_price, kalshi_ask_price, polymarket_price, spread
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pair.label,
                pair.outcome,
                pair.kalshi_ticker,
                pair.polymarket_token_id,
                int(row[0]),
                str(row[1]),
                str(row[2]) if row[2] is not None else None,
                str(row[3]),
                str(polymarket_row[0]),
                str(spread),
            ),
        )
        aligned_count += 1
    return aligned_count


def nearest_polymarket_history_row(
    connection: sqlite3.Connection,
    pair: MarketPair,
    *,
    ts: int,
    tolerance_seconds: int,
) -> sqlite3.Row | tuple[Any, ...] | None:
    return connection.execute(
        """
        SELECT price
        FROM historical_prices
        WHERE source = 'polymarket'
            AND kalshi_ticker = ?
            AND polymarket_token_id = ?
            AND ABS(ts - ?) <= ?
        ORDER BY ABS(ts - ?), ts
        LIMIT 1
        """,
        (pair.kalshi_ticker, pair.polymarket_token_id, ts, tolerance_seconds, ts),
    ).fetchone()


def candle_price(candle: Any, field: str) -> Decimal | None:
    if not isinstance(candle, dict):
        return None
    value = candle.get(field)
    if value in {None, ""}:
        return None
    return Decimal(str(value))
