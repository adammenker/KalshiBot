from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Any

from kalshibot.utils import optional_decimal, utc_now_iso


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
                strategy_id, strategy_version, strategy_signal_id, side, direction,
                fair_value_provider, entry_price, entry_fair_price, entry_hold_to_resolution_ev,
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
        "strategy_id": row["strategy_id"],
        "strategy_version": row["strategy_version"],
        "strategy_signal_id": row["strategy_signal_id"],
        "side": row["side"],
        "direction": row["direction"],
        "fair_value_provider": row["fair_value_provider"],
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


def sum_optional_decimals(values: Any) -> Decimal | None:
    total: Decimal | None = None
    for value in values:
        decimal = optional_decimal(value)
        if decimal is None:
            continue
        total = decimal if total is None else total + decimal
    return total
