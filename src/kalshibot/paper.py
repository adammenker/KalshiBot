from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Any

from kalshibot.fees import kalshi_taker_fee
from kalshibot.spreads import SpreadCheck
from kalshibot.utils import optional_decimal, utc_now_iso


@dataclass(frozen=True)
class PaperExitConfig:
    exit_edge: Decimal | None = None
    take_profit: Decimal | None = None
    stop_loss: Decimal | None = None
    max_hold_minutes: int | None = None


@dataclass(frozen=True)
class PaperPnl:
    gross: Decimal | None
    entry_fee: Decimal
    exit_fee: Decimal | None
    net: Decimal | None


@dataclass(frozen=True)
class PaperTradeSnapshot:
    entry_price: Decimal
    quantity: Decimal
    mark_price: Decimal | None
    fair_price: Decimal | None
    pnl: PaperPnl
    hold_to_resolution_ev: Decimal | None


@dataclass(frozen=True)
class PaperTradeLogEvent:
    event: str
    trade_id: int
    observation_id: int
    run_id: str
    timestamp: str
    label: str
    outcome: str
    kalshi_ticker: str
    polymarket_token_id: str
    purchase_price: Decimal
    sell_price: Decimal | None
    quantity: Decimal
    gross_pnl: Decimal | None
    entry_fee: Decimal
    exit_fee: Decimal | None
    fair_price: Decimal | None
    hold_to_resolution_ev: Decimal | None
    fee_mode: str
    fee_adjustment: Decimal
    net_pnl: Decimal | None
    edge: Decimal
    close_reason: str | None = None
    kalshi_url: str = ""
    polymarket_url: str = ""


def create_open_paper_trade(
    connection: sqlite3.Connection,
    *,
    signal_id: int,
    observation_id: int,
    timed_check: Any,
) -> PaperTradeLogEvent | None:
    check = timed_check.check
    if open_paper_trade_exists(connection, check):
        return None

    snapshot = paper_trade_snapshot(
        check,
        entry_price=check.kalshi_buy_price,
        entry_fee=check.kalshi_entry_fee,
    )
    cursor = connection.execute(
        """
        INSERT INTO paper_trades (
            signal_id, observation_id, run_id, opened_at, closed_at, status,
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
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            signal_id,
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
            0,
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
    )


def open_paper_trade_exists(connection: sqlite3.Connection, check: SpreadCheck) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM paper_trades
        WHERE status = 'open'
            AND label = ?
            AND outcome = ?
            AND kalshi_ticker = ?
            AND polymarket_token_id = ?
        LIMIT 1
        """,
        (check.label, check.outcome, check.kalshi_ticker, check.polymarket_token_id),
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


def paper_trade_pnl(
    *,
    entry_price: Decimal,
    mark_price: Decimal | None,
    quantity: Decimal,
    entry_fee: Decimal | None = None,
    exit_fee: Decimal | None = None,
) -> PaperPnl:
    resolved_entry_fee = entry_fee if entry_fee is not None else kalshi_taker_fee(entry_price, quantity)
    if mark_price is None:
        return PaperPnl(gross=None, entry_fee=resolved_entry_fee, exit_fee=None, net=None)
    resolved_exit_fee = exit_fee if exit_fee is not None else kalshi_taker_fee(mark_price, quantity)
    gross = (mark_price - entry_price) * quantity
    return PaperPnl(
        gross=gross,
        entry_fee=resolved_entry_fee,
        exit_fee=resolved_exit_fee,
        net=gross - resolved_entry_fee - resolved_exit_fee,
    )


def paper_trade_snapshot(
    check: SpreadCheck,
    *,
    entry_price: Decimal,
    quantity: Decimal | None = None,
    entry_fee: Decimal | None = None,
) -> PaperTradeSnapshot:
    resolved_quantity = quantity or check.target_size
    pnl = paper_trade_pnl(
        entry_price=entry_price,
        mark_price=check.kalshi_sell_price,
        quantity=resolved_quantity,
        entry_fee=entry_fee if entry_fee is not None else check.kalshi_entry_fee,
        exit_fee=check.kalshi_exit_fee,
    )
    fair_price = hold_to_resolution_fair_price(check)
    return PaperTradeSnapshot(
        entry_price=entry_price,
        quantity=resolved_quantity,
        mark_price=check.kalshi_sell_price,
        fair_price=fair_price,
        pnl=pnl,
        hold_to_resolution_ev=paper_hold_to_resolution_ev(
            entry_price=entry_price,
            fair_price=fair_price,
            quantity=resolved_quantity,
            entry_fee=pnl.entry_fee,
        ),
    )


def hold_to_resolution_fair_price(check: SpreadCheck) -> Decimal | None:
    return check.polymarket_mid_price


def paper_hold_to_resolution_ev(
    *,
    entry_price: Decimal,
    fair_price: Decimal | None,
    quantity: Decimal,
    entry_fee: Decimal,
) -> Decimal | None:
    if fair_price is None:
        return None
    return (fair_price - entry_price) * quantity - entry_fee


def trade_entry_fee(trade_row: dict[str, Any]) -> Decimal:
    existing = optional_decimal(trade_row.get("entry_fee"))
    if existing is not None:
        return existing
    return kalshi_taker_fee(
        Decimal(str(trade_row["entry_price"])),
        Decimal(str(trade_row["quantity"])),
    )


def paper_trade_log_event(
    *,
    event: str,
    trade_id: int,
    observation_id: int,
    timed_check: Any,
    purchase_price: Decimal,
    sell_price: Decimal | None,
    pnl: PaperPnl,
    close_reason: str | None,
) -> PaperTradeLogEvent:
    check = timed_check.check
    fair_price = hold_to_resolution_fair_price(check)
    hold_ev = paper_hold_to_resolution_ev(
        entry_price=purchase_price,
        fair_price=fair_price,
        quantity=check.target_size,
        entry_fee=pnl.entry_fee,
    )
    return PaperTradeLogEvent(
        event=event,
        trade_id=trade_id,
        observation_id=observation_id,
        run_id=timed_check.run_id,
        timestamp=timed_check.observed_at,
        label=check.label,
        outcome=check.outcome,
        kalshi_ticker=check.kalshi_ticker,
        polymarket_token_id=check.polymarket_token_id,
        purchase_price=purchase_price,
        sell_price=sell_price,
        quantity=check.target_size,
        gross_pnl=pnl.gross,
        entry_fee=pnl.entry_fee,
        exit_fee=pnl.exit_fee,
        fair_price=fair_price,
        hold_to_resolution_ev=hold_ev,
        fee_mode=check.fee_mode,
        fee_adjustment=check.fee_adjustment,
        net_pnl=pnl.net,
        edge=check.polymarket_minus_kalshi,
        close_reason=close_reason,
        kalshi_url=check.kalshi_url,
        polymarket_url=check.polymarket_url,
    )


def append_paper_trade_events(path: Path, events: list[PaperTradeLogEvent]) -> None:
    if not events:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        for event in events:
            handle.write(json.dumps(format_paper_trade_log_event(event), sort_keys=True) + "\n")


def format_paper_trade_log_event(event: PaperTradeLogEvent) -> dict[str, Any]:
    return {
        "event": event.event,
        "trade_id": event.trade_id,
        "observation_id": event.observation_id,
        "run_id": event.run_id,
        "timestamp": event.timestamp,
        "market": event.label,
        "outcome": event.outcome,
        "kalshi_ticker": event.kalshi_ticker,
        "polymarket_token_id": event.polymarket_token_id,
        "kalshi_url": event.kalshi_url,
        "polymarket_url": event.polymarket_url,
        "purchase_price": str(event.purchase_price),
        "sell_price": str(event.sell_price) if event.sell_price is not None else None,
        "quantity": str(event.quantity),
        "gross_pnl": str(event.gross_pnl) if event.gross_pnl is not None else None,
        "entry_fee": str(event.entry_fee),
        "exit_fee": str(event.exit_fee) if event.exit_fee is not None else None,
        "hold_to_resolution_fair_price": str(event.fair_price)
        if event.fair_price is not None
        else None,
        "hold_to_resolution_ev": str(event.hold_to_resolution_ev)
        if event.hold_to_resolution_ev is not None
        else None,
        "fee_mode": event.fee_mode,
        "fee_adjustment": str(event.fee_adjustment),
        "net_pnl": str(event.net_pnl) if event.net_pnl is not None else None,
        "edge": str(event.edge),
        "close_reason": event.close_reason,
    }


def write_paper_pnl_snapshot(path: Path, db_path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(paper_pnl_snapshot(db_path), indent=2, sort_keys=True) + "\n")


def paper_pnl_snapshot(db_path: Path) -> dict[str, Any]:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        trade_rows = connection.execute(
            """
            SELECT status, realized_pnl, realized_gross_pnl, latest_unrealized_pnl,
                latest_gross_unrealized_pnl, latest_hold_to_resolution_ev,
                entry_fee, exit_fee, latest_exit_fee
            FROM paper_trades
            """
        ).fetchall()
        open_rows = connection.execute(
            """
            SELECT id, label, outcome, kalshi_ticker, polymarket_token_id,
                entry_price, entry_fair_price, entry_hold_to_resolution_ev,
                fee_mode, fee_adjustment, latest_mark_price, latest_unrealized_pnl,
                latest_gross_unrealized_pnl, latest_fair_price,
                latest_hold_to_resolution_ev, latest_edge, observation_count
            FROM paper_trades
            WHERE status = 'open'
            ORDER BY latest_marked_at DESC, id DESC
            """
        ).fetchall()
    open_trade_count = sum(1 for row in trade_rows if row["status"] == "open")
    closed_trade_count = sum(1 for row in trade_rows if row["status"] == "closed")
    open_trade_rows = [row for row in trade_rows if row["status"] == "open"]
    realized = sum_optional_decimals(row["realized_pnl"] for row in trade_rows) or Decimal("0")
    unrealized = (
        sum_optional_decimals(row["latest_unrealized_pnl"] for row in open_trade_rows)
        or Decimal("0")
    )
    realized_gross = (
        sum_optional_decimals(row["realized_gross_pnl"] for row in trade_rows)
        or Decimal("0")
    )
    unrealized_gross = (
        sum_optional_decimals(row["latest_gross_unrealized_pnl"] for row in open_trade_rows)
        or Decimal("0")
    )
    hold_ev = (
        sum_optional_decimals(row["latest_hold_to_resolution_ev"] for row in open_trade_rows)
        or Decimal("0")
    )
    return {
        "updated_at": utc_now_iso(),
        "database": str(db_path),
        "trade_count": len(trade_rows),
        "open_trade_count": open_trade_count,
        "closed_trade_count": closed_trade_count,
        "total_realized_pnl": str(realized),
        "total_open_unrealized_pnl": str(unrealized),
        "total_pnl": str(realized + unrealized),
        "total_realized_gross_pnl": str(realized_gross),
        "total_open_gross_unrealized_pnl": str(unrealized_gross),
        "total_gross_pnl": str(realized_gross + unrealized_gross),
        "total_open_hold_to_resolution_ev": str(hold_ev),
        "average_open_hold_to_resolution_ev": str(hold_ev / open_trade_count)
        if open_trade_count
        else None,
        "total_entry_fees": optional_string_decimal(
            sum_optional_decimals(row["entry_fee"] for row in trade_rows)
        ),
        "total_realized_exit_fees": optional_string_decimal(
            sum_optional_decimals(row["exit_fee"] for row in trade_rows)
        ),
        "total_open_exit_fees": optional_string_decimal(
            sum_optional_decimals(row["latest_exit_fee"] for row in open_trade_rows)
        ),
        "open_trades": [format_open_trade_row(row) for row in open_rows],
    }


def format_open_trade_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "trade_id": row["id"],
        "market": row["label"],
        "outcome": row["outcome"],
        "kalshi_ticker": row["kalshi_ticker"],
        "polymarket_token_id": row["polymarket_token_id"],
        "purchase_price": row["entry_price"],
        "entry_hold_to_resolution_fair_price": row["entry_fair_price"],
        "entry_hold_to_resolution_ev": row["entry_hold_to_resolution_ev"],
        "fee_mode": row["fee_mode"],
        "fee_adjustment": row["fee_adjustment"],
        "current_sell_price": row["latest_mark_price"],
        "net_unrealized_pnl": row["latest_unrealized_pnl"],
        "gross_unrealized_pnl": row["latest_gross_unrealized_pnl"],
        "current_hold_to_resolution_fair_price": row["latest_fair_price"],
        "current_hold_to_resolution_ev": row["latest_hold_to_resolution_ev"],
        "latest_edge": row["latest_edge"],
        "observation_count": row["observation_count"],
    }


def optional_string_decimal(value: Any) -> str | None:
    decimal = optional_decimal(value)
    return str(decimal) if decimal is not None else None


def optional_decimal_string(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def sum_optional_decimals(values: Any) -> Decimal | None:
    total: Decimal | None = None
    for value in values:
        decimal = optional_decimal(value)
        if decimal is None:
            continue
        total = decimal if total is None else total + decimal
    return total


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
