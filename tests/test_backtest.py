from __future__ import annotations

from decimal import Decimal
import sqlite3

from kalshibot.backtest import (
    align_pair_history,
    calculate_max_drawdown,
    initialize_historical_database,
    infer_series_ticker,
    run_historical_backtest,
    save_kalshi_history,
    save_polymarket_history,
)
from kalshibot.spreads import MarketPair


def test_infer_series_ticker_uses_prefix_before_first_dash() -> None:
    assert infer_series_ticker("KXWCGAME-26JUN21ESPKSA-ESP") == "KXWCGAME"


def test_save_history_aligns_matching_timestamps(tmp_path) -> None:
    db_path = tmp_path / "history.sqlite"
    pair = MarketPair(
        label="Example",
        kalshi_ticker="KXTEST-26-YES",
        polymarket_token_id="token-1",
    )
    initialize_historical_database(db_path)
    with sqlite3.connect(db_path) as connection:
        kalshi_rows = save_kalshi_history(
            connection,
            pair,
            {
                "candlesticks": [
                    {
                        "end_period_ts": 100,
                        "yes_bid": {"close_dollars": "0.4100"},
                        "yes_ask": {"close_dollars": "0.4300"},
                        "price": {"close_dollars": "0.4200"},
                        "volume_fp": "10",
                        "open_interest_fp": "100",
                    },
                    {
                        "end_period_ts": 160,
                        "yes_bid": {"close_dollars": "0.4400"},
                        "yes_ask": {"close_dollars": "0.4600"},
                        "price": {"close_dollars": "0.4500"},
                    },
                ]
            },
        )
        polymarket_rows = save_polymarket_history(
            connection,
            pair,
            {"history": [{"t": 100, "p": 0.48}, {"t": 160, "p": 0.45}]},
        )
        aligned_rows = align_pair_history(connection, pair)
        aligned = connection.execute(
            """
            SELECT ts, kalshi_ask_price, polymarket_price, spread
            FROM historical_aligned_prices
            ORDER BY ts
            """
        ).fetchall()

    assert kalshi_rows == 2
    assert polymarket_rows == 2
    assert aligned_rows == 2
    assert aligned == [(100, "0.4300", "0.48", "0.0500"), (160, "0.4600", "0.45", "-0.0100")]


def test_align_pair_history_matches_nearest_timestamp_within_tolerance(tmp_path) -> None:
    db_path = tmp_path / "history.sqlite"
    pair = MarketPair(
        label="Example",
        kalshi_ticker="KXTEST-26-YES",
        polymarket_token_id="token-1",
    )
    initialize_historical_database(db_path)
    with sqlite3.connect(db_path) as connection:
        save_kalshi_history(
            connection,
            pair,
            {"candlesticks": [candle(100, bid="0.40", ask="0.43")]},
        )
        save_polymarket_history(
            connection,
            pair,
            {"history": [{"t": 108, "p": 0.48}]},
        )
        aligned_rows = align_pair_history(connection, pair, tolerance_seconds=10)
        aligned = connection.execute(
            "SELECT ts, kalshi_ask_price, polymarket_price, spread FROM historical_aligned_prices"
        ).fetchall()

    assert aligned_rows == 1
    assert aligned == [(100, "0.43", "0.48", "0.05")]


def test_run_historical_backtest_enters_and_exits_on_spread_close(tmp_path) -> None:
    db_path = tmp_path / "history.sqlite"
    pair = MarketPair(
        label="Example",
        kalshi_ticker="KXTEST-26-YES",
        polymarket_token_id="token-1",
    )
    initialize_historical_database(db_path)
    with sqlite3.connect(db_path) as connection:
        save_kalshi_history(
            connection,
            pair,
            {
                "candlesticks": [
                    candle(100, bid="0.40", ask="0.43"),
                    candle(160, bid="0.46", ask="0.46"),
                    candle(220, bid="0.47", ask="0.49"),
                ]
            },
        )
        save_polymarket_history(
            connection,
            pair,
            {"history": [{"t": 100, "p": 0.48}, {"t": 160, "p": 0.46}, {"t": 220, "p": 0.48}]},
        )
        align_pair_history(connection, pair)

    summary = run_historical_backtest(
        db_path=db_path,
        min_edge=Decimal("0.02"),
        hold_period_minutes=10,
        slippage=Decimal("0.005"),
    )

    with sqlite3.connect(db_path) as connection:
        stored_trade = connection.execute(
            """
            SELECT entry_price, exit_price, entry_edge, exit_edge, pnl, exit_reason
            FROM backtest_trades
            """
        ).fetchone()

    assert summary["trade_count"] == 1
    assert summary["winning_trade_count"] == 1
    assert summary["total_pnl"] == "0.0200"
    assert stored_trade == ("0.435", "0.455", "0.05", "0.00", "0.020", "spread_closed")


def test_calculate_max_drawdown_tracks_equity_peak() -> None:
    assert calculate_max_drawdown([Decimal("0.10"), Decimal("-0.03"), Decimal("-0.05")]) == Decimal(
        "-0.08"
    )


def candle(ts: int, *, bid: str, ask: str) -> dict[str, object]:
    return {
        "end_period_ts": ts,
        "yes_bid": {"close_dollars": bid},
        "yes_ask": {"close_dollars": ask},
        "price": {"close_dollars": ask},
    }
