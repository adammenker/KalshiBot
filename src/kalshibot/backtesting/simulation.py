from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

from kalshibot.backtesting.models import BacktestTrade
from kalshibot.backtesting.storage import initialize_historical_database
from kalshibot.utils import format_decimal, format_ratio


def run_historical_backtest(
    *,
    db_path: Path,
    min_edge: Decimal,
    hold_period_minutes: int,
    slippage: Decimal = Decimal("0"),
) -> dict[str, Any]:
    if hold_period_minutes < 1:
        raise ValueError("hold_period_minutes must be at least 1")
    initialize_historical_database(db_path)
    run_id = str(uuid4())
    trades: list[BacktestTrade] = []
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        for key in historical_pair_keys(connection):
            rows = aligned_rows_for_pair(connection, key)
            trades.extend(
                simulate_pair_backtest(
                    rows,
                    min_edge=min_edge,
                    hold_period_minutes=hold_period_minutes,
                    slippage=slippage,
                )
            )
        summary = save_backtest_run(
            connection,
            run_id=run_id,
            db_path=db_path,
            min_edge=min_edge,
            hold_period_minutes=hold_period_minutes,
            slippage=slippage,
            trades=trades,
        )
    return summary


def historical_pair_keys(connection: sqlite3.Connection) -> list[dict[str, str]]:
    return [
        dict(row)
        for row in connection.execute(
            """
            SELECT DISTINCT label, outcome, kalshi_ticker, polymarket_token_id
            FROM historical_aligned_prices
            ORDER BY label
            """
        ).fetchall()
    ]


def aligned_rows_for_pair(
    connection: sqlite3.Connection,
    key: dict[str, str],
) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT *
        FROM historical_aligned_prices
        WHERE kalshi_ticker = ?
            AND polymarket_token_id = ?
        ORDER BY ts
        """,
        (key["kalshi_ticker"], key["polymarket_token_id"]),
    ).fetchall()


def simulate_pair_backtest(
    rows: list[sqlite3.Row],
    *,
    min_edge: Decimal,
    hold_period_minutes: int,
    slippage: Decimal,
) -> list[BacktestTrade]:
    trades: list[BacktestTrade] = []
    open_trade: sqlite3.Row | None = None
    entry_index: int | None = None
    for index, row in enumerate(rows):
        spread = Decimal(str(row["spread"]))
        if open_trade is None:
            if spread >= min_edge:
                open_trade = row
                entry_index = index
            continue

        assert entry_index is not None
        held_periods = index - entry_index
        if spread <= 0:
            trades.append(backtest_trade_from_rows(open_trade, row, slippage, "spread_closed"))
            open_trade = None
            entry_index = None
        elif held_periods >= hold_period_minutes:
            trades.append(backtest_trade_from_rows(open_trade, row, slippage, "hold_period"))
            open_trade = None
            entry_index = None

    if open_trade is not None and rows:
        trades.append(backtest_trade_from_rows(open_trade, rows[-1], slippage, "end_of_data"))
    return trades


def backtest_trade_from_rows(
    entry: sqlite3.Row,
    exit_row: sqlite3.Row,
    slippage: Decimal,
    exit_reason: str,
) -> BacktestTrade:
    entry_price = Decimal(str(entry["kalshi_ask_price"])) + slippage
    exit_price = exit_mark_price(exit_row) - slippage
    pnl = exit_price - entry_price
    return BacktestTrade(
        label=entry["label"],
        outcome=entry["outcome"],
        kalshi_ticker=entry["kalshi_ticker"],
        polymarket_token_id=entry["polymarket_token_id"],
        entry_ts=int(entry["ts"]),
        exit_ts=int(exit_row["ts"]),
        entry_price=entry_price,
        exit_price=exit_price,
        entry_edge=Decimal(str(entry["spread"])),
        exit_edge=Decimal(str(exit_row["spread"])),
        pnl=pnl,
        exit_reason=exit_reason,
    )


def exit_mark_price(row: sqlite3.Row) -> Decimal:
    if row["kalshi_bid_price"] is not None:
        return Decimal(str(row["kalshi_bid_price"]))
    return Decimal(str(row["kalshi_price"]))


def save_backtest_run(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    db_path: Path,
    min_edge: Decimal,
    hold_period_minutes: int,
    slippage: Decimal,
    trades: list[BacktestTrade],
) -> dict[str, Any]:
    total_pnl = sum((trade.pnl for trade in trades), Decimal("0"))
    winning_count = sum(1 for trade in trades if trade.pnl > 0)
    average_pnl = total_pnl / len(trades) if trades else None
    max_drawdown = calculate_max_drawdown([trade.pnl for trade in trades])
    created_at = datetime.now(timezone.utc).isoformat()
    connection.execute(
        """
        INSERT INTO backtest_runs (
            id, created_at, db_path, min_edge, hold_period_minutes, slippage,
            trade_count, winning_trade_count, total_pnl, average_pnl, max_drawdown
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            created_at,
            str(db_path),
            str(min_edge),
            hold_period_minutes,
            str(slippage),
            len(trades),
            winning_count,
            str(total_pnl),
            str(average_pnl) if average_pnl is not None else None,
            str(max_drawdown),
        ),
    )
    for trade in trades:
        connection.execute(
            """
            INSERT INTO backtest_trades (
                run_id, label, outcome, kalshi_ticker, polymarket_token_id,
                entry_ts, exit_ts, entry_price, exit_price, entry_edge, exit_edge,
                pnl, exit_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                trade.label,
                trade.outcome,
                trade.kalshi_ticker,
                trade.polymarket_token_id,
                trade.entry_ts,
                trade.exit_ts,
                str(trade.entry_price),
                str(trade.exit_price),
                str(trade.entry_edge),
                str(trade.exit_edge),
                str(trade.pnl),
                trade.exit_reason,
            ),
        )
    return {
        "run_id": run_id,
        "created_at": created_at,
        "trade_count": len(trades),
        "winning_trade_count": winning_count,
        "win_rate": format_ratio(winning_count, len(trades)),
        "total_pnl": format_decimal(total_pnl),
        "average_pnl": format_decimal(average_pnl),
        "max_drawdown": format_decimal(max_drawdown),
        "min_edge": str(min_edge),
        "hold_period_minutes": hold_period_minutes,
        "slippage": str(slippage),
    }


def calculate_max_drawdown(pnls: list[Decimal]) -> Decimal:
    equity = Decimal("0")
    peak = Decimal("0")
    max_drawdown = Decimal("0")
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity - peak)
    return max_drawdown
