from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from kalshibot.paper.logging import paper_trade_log_event
from kalshibot.paper.models import PaperExitConfig, PaperTradeLogEvent
from kalshibot.paper.pricing import (
    optional_decimal_string,
    paper_trade_snapshot,
    trade_entry_fee,
)
from kalshibot.spreads import SpreadCheck
from kalshibot.utils import optional_decimal


def create_open_paper_trade(
    connection: sqlite3.Connection,
    *,
    signal_id: int | None,
    observation_id: int,
    timed_check: Any,
    strategy_signal_id: int | None = None,
    strategy_id: str | None = None,
    strategy_version: str | None = None,
    fair_value_provider: str | None = None,
    fair_value: Decimal | None = None,
    entry_policy: str | None = None,
    exit_policy: str | None = None,
    side: str | None = None,
    direction: str | None = None,
    initial_observation_count: int = 0,
) -> PaperTradeLogEvent | None:
    check = timed_check.check
    if open_paper_trade_exists(
        connection,
        check,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        side=side,
        direction=direction,
    ):
        return None

    snapshot = paper_trade_snapshot(
        check,
        entry_price=check.kalshi_buy_price,
        entry_fee=check.kalshi_entry_fee,
        fair_value_provider=fair_value_provider,
        fair_value=fair_value,
    )
    cursor = connection.execute(
        """
        INSERT INTO paper_trades (
            signal_id, strategy_signal_id, strategy_id, strategy_version,
            fair_value_provider, entry_policy, exit_policy, side, direction,
            observation_id, run_id, opened_at, closed_at, status,
            label, outcome, kalshi_ticker, polymarket_token_id,
            simulated_entry_venue, entry_price, entry_comparison_price, entry_edge,
            entry_fair_price, entry_hold_to_resolution_ev,
            quantity, entry_fee, fee_mode, fee_adjustment, latest_observation_id,
            latest_marked_at, latest_mark_price, latest_exit_fee,
            latest_gross_unrealized_pnl, latest_unrealized_pnl,
            latest_fair_price, latest_hold_to_resolution_ev,
            latest_edge, best_unrealized_pnl, worst_unrealized_pnl,
            best_hold_to_resolution_ev, worst_hold_to_resolution_ev,
            observation_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            signal_id,
            strategy_signal_id,
            strategy_id,
            strategy_version,
            fair_value_provider,
            entry_policy,
            exit_policy,
            side,
            direction,
            observation_id,
            timed_check.run_id,
            timed_check.observed_at,
            None,
            "open",
            check.label,
            check.outcome,
            check.kalshi_ticker,
            check.polymarket_token_id,
            "kalshi",
            str(check.kalshi_buy_price),
            str(check.polymarket_buy_price),
            str(check.polymarket_minus_kalshi),
            optional_decimal_string(snapshot.fair_price),
            optional_decimal_string(snapshot.hold_to_resolution_ev),
            str(snapshot.quantity),
            str(snapshot.pnl.entry_fee),
            check.fee_mode,
            str(check.fee_adjustment),
            observation_id,
            timed_check.observed_at,
            optional_decimal_string(snapshot.mark_price),
            optional_decimal_string(snapshot.pnl.exit_fee),
            optional_decimal_string(snapshot.pnl.gross),
            optional_decimal_string(snapshot.pnl.net),
            optional_decimal_string(snapshot.fair_price),
            optional_decimal_string(snapshot.hold_to_resolution_ev),
            str(check.polymarket_minus_kalshi),
            optional_decimal_string(snapshot.pnl.net),
            optional_decimal_string(snapshot.pnl.net),
            optional_decimal_string(snapshot.hold_to_resolution_ev),
            optional_decimal_string(snapshot.hold_to_resolution_ev),
            initial_observation_count,
        ),
    )
    trade_id = int(cursor.lastrowid)
    return paper_trade_log_event(
        event="open",
        trade_id=trade_id,
        observation_id=observation_id,
        timed_check=timed_check,
        purchase_price=check.kalshi_buy_price,
        sell_price=snapshot.mark_price,
        pnl=snapshot.pnl,
        close_reason=None,
        fair_value_provider=fair_value_provider,
        fair_value=fair_value,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        strategy_signal_id=strategy_signal_id,
        side=side,
        direction=direction,
    )


def open_paper_trade_exists(
    connection: sqlite3.Connection,
    check: SpreadCheck,
    *,
    strategy_id: str | None = None,
    strategy_version: str | None = None,
    side: str | None = None,
    direction: str | None = None,
) -> bool:
    params: list[str | None] = [
        check.label,
        check.outcome,
        check.kalshi_ticker,
        check.polymarket_token_id,
    ]
    strategy_filter = "strategy_id IS NULL"
    if strategy_id is not None:
        strategy_filter = """
            strategy_id = ?
            AND (
                (? IS NULL AND strategy_version IS NULL)
                OR strategy_version = ?
            )
            AND (
                (? IS NULL AND side IS NULL)
                OR side = ?
            )
            AND (
                (? IS NULL AND direction IS NULL)
                OR direction = ?
            )
        """
        params.extend(
            [
                strategy_id,
                strategy_version,
                strategy_version,
                side,
                side,
                direction,
                direction,
            ]
        )
    row = connection.execute(
        f"""
        SELECT 1
        FROM paper_trades
        WHERE status = 'open'
            AND label = ?
            AND outcome = ?
            AND kalshi_ticker = ?
            AND polymarket_token_id = ?
            AND {strategy_filter}
        LIMIT 1
        """,
        params,
    ).fetchone()
    return row is not None


def update_open_paper_trades(
    connection: sqlite3.Connection,
    observation_id: int,
    timed_check: Any,
    exit_config: PaperExitConfig | None = None,
) -> list[PaperTradeLogEvent]:
    check = timed_check.check
    rows = connection.execute(
        """
        SELECT *
        FROM paper_trades
        WHERE status = 'open'
            AND label = ?
            AND outcome = ?
            AND kalshi_ticker = ?
            AND polymarket_token_id = ?
        """,
        (check.label, check.outcome, check.kalshi_ticker, check.polymarket_token_id),
    ).fetchall()
    events: list[PaperTradeLogEvent] = []
    for row in rows:
        event = mark_open_paper_trade(
            connection,
            row,
            observation_id,
            timed_check,
            exit_config=exit_config or PaperExitConfig(),
        )
        if event is not None:
            events.append(event)
    return events


def mark_open_paper_trade(
    connection: sqlite3.Connection,
    trade: sqlite3.Row,
    observation_id: int,
    timed_check: Any,
    *,
    exit_config: PaperExitConfig,
) -> PaperTradeLogEvent | None:
    trade_row = dict(trade)
    check = timed_check.check
    quantity = Decimal(str(trade_row["quantity"]))
    entry_fee = trade_entry_fee(trade_row)
    entry_price = Decimal(str(trade_row["entry_price"]))
    snapshot = paper_trade_snapshot(
        check,
        entry_price=entry_price,
        quantity=quantity,
        entry_fee=entry_fee,
        fair_value_provider=trade_row.get("fair_value_provider"),
    )
    best_pnl = best_decimal(trade_row["best_unrealized_pnl"], snapshot.pnl.net)
    worst_pnl = worst_decimal(trade_row["worst_unrealized_pnl"], snapshot.pnl.net)
    best_hold_ev = best_decimal(
        trade_row.get("best_hold_to_resolution_ev"),
        snapshot.hold_to_resolution_ev,
    )
    worst_hold_ev = worst_decimal(
        trade_row.get("worst_hold_to_resolution_ev"),
        snapshot.hold_to_resolution_ev,
    )
    connection.execute(
        """
        INSERT INTO paper_trade_marks (
            paper_trade_id, observation_id, marked_at, mark_price, unrealized_pnl,
            gross_unrealized_pnl, entry_fee, exit_fee, fair_price,
            hold_to_resolution_ev, edge, kalshi_buy_price, kalshi_sell_price,
            polymarket_buy_price
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            trade_row["id"],
            observation_id,
            timed_check.observed_at,
            optional_decimal_string(snapshot.mark_price),
            optional_decimal_string(snapshot.pnl.net),
            optional_decimal_string(snapshot.pnl.gross),
            str(snapshot.pnl.entry_fee),
            optional_decimal_string(snapshot.pnl.exit_fee),
            optional_decimal_string(snapshot.fair_price),
            optional_decimal_string(snapshot.hold_to_resolution_ev),
            str(check.polymarket_minus_kalshi),
            str(check.kalshi_buy_price),
            str(check.kalshi_sell_price) if check.kalshi_sell_price is not None else None,
            str(check.polymarket_buy_price),
        ),
    )
    connection.execute(
        """
        UPDATE paper_trades
        SET latest_observation_id = ?,
            latest_marked_at = ?,
            latest_mark_price = ?,
            latest_exit_fee = ?,
            latest_gross_unrealized_pnl = ?,
            latest_unrealized_pnl = ?,
            latest_fair_price = ?,
            latest_hold_to_resolution_ev = ?,
            latest_edge = ?,
            best_unrealized_pnl = ?,
            worst_unrealized_pnl = ?,
            best_hold_to_resolution_ev = ?,
            worst_hold_to_resolution_ev = ?,
            observation_count = observation_count + 1
        WHERE id = ?
        """,
        (
            observation_id,
            timed_check.observed_at,
            optional_decimal_string(snapshot.mark_price),
            optional_decimal_string(snapshot.pnl.exit_fee),
            optional_decimal_string(snapshot.pnl.gross),
            optional_decimal_string(snapshot.pnl.net),
            optional_decimal_string(snapshot.fair_price),
            optional_decimal_string(snapshot.hold_to_resolution_ev),
            str(check.polymarket_minus_kalshi),
            optional_decimal_string(best_pnl),
            optional_decimal_string(worst_pnl),
            optional_decimal_string(best_hold_ev),
            optional_decimal_string(worst_hold_ev),
            trade_row["id"],
        ),
    )
    close_reason = paper_exit_reason(trade_row, timed_check, snapshot.pnl.net, exit_config)
    if close_reason:
        close_open_paper_trade(
            connection,
            trade_id=int(trade_row["id"]),
            observation_id=observation_id,
            closed_at=timed_check.observed_at,
            exit_price=snapshot.mark_price,
            exit_fee=snapshot.pnl.exit_fee,
            realized_gross_pnl=snapshot.pnl.gross,
            realized_pnl=snapshot.pnl.net,
            close_reason=close_reason,
        )
        return paper_trade_log_event(
            event="close",
            trade_id=int(trade_row["id"]),
            observation_id=observation_id,
            timed_check=timed_check,
            purchase_price=Decimal(str(trade_row["entry_price"])),
            sell_price=snapshot.mark_price,
            pnl=snapshot.pnl,
            close_reason=close_reason,
            fair_value_provider=trade_row.get("fair_value_provider"),
            strategy_id=trade_row.get("strategy_id"),
            strategy_version=trade_row.get("strategy_version"),
            strategy_signal_id=optional_int(trade_row.get("strategy_signal_id")),
            side=trade_row.get("side"),
            direction=trade_row.get("direction"),
        )
    return None


def paper_exit_reason(
    trade_row: dict[str, Any],
    timed_check: Any,
    unrealized_pnl: Decimal | None,
    exit_config: PaperExitConfig,
) -> str | None:
    check = timed_check.check
    if exit_config.exit_edge is not None and check.polymarket_minus_kalshi <= exit_config.exit_edge:
        return "edge_closed"
    if (
        exit_config.take_profit is not None
        and unrealized_pnl is not None
        and unrealized_pnl >= exit_config.take_profit
    ):
        return "take_profit"
    if (
        exit_config.stop_loss is not None
        and unrealized_pnl is not None
        and unrealized_pnl <= -exit_config.stop_loss
    ):
        return "stop_loss"
    if exit_config.max_hold_minutes is not None:
        opened_at = datetime.fromisoformat(str(trade_row["opened_at"]))
        marked_at = datetime.fromisoformat(timed_check.observed_at)
        if marked_at - opened_at >= timedelta(minutes=exit_config.max_hold_minutes):
            return "max_hold"
    return None


def close_open_paper_trade(
    connection: sqlite3.Connection,
    *,
    trade_id: int,
    observation_id: int,
    closed_at: str,
    exit_price: Decimal | None,
    exit_fee: Decimal | None,
    realized_gross_pnl: Decimal | None,
    realized_pnl: Decimal | None,
    close_reason: str,
) -> None:
    connection.execute(
        """
        UPDATE paper_trades
        SET status = 'closed',
            closed_at = ?,
            exit_observation_id = ?,
            exit_price = ?,
            exit_fee = ?,
            realized_gross_pnl = ?,
            realized_pnl = ?,
            close_reason = ?
        WHERE id = ?
            AND status = 'open'
        """,
        (
            closed_at,
            observation_id,
            str(exit_price) if exit_price is not None else None,
            str(exit_fee) if exit_fee is not None else None,
            str(realized_gross_pnl) if realized_gross_pnl is not None else None,
            str(realized_pnl) if realized_pnl is not None else None,
            close_reason,
            trade_id,
        ),
    )


def optional_int(value: Any) -> int | None:
    return int(value) if value is not None else None


def best_decimal(current: Any, candidate: Decimal | None) -> Decimal | None:
    current_decimal = optional_decimal(current)
    if candidate is None:
        return current_decimal
    if current_decimal is None:
        return candidate
    return max(current_decimal, candidate)


def worst_decimal(current: Any, candidate: Decimal | None) -> Decimal | None:
    current_decimal = optional_decimal(current)
    if candidate is None:
        return current_decimal
    if current_decimal is None:
        return candidate
    return min(current_decimal, candidate)
