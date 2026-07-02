from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from typing import Any

from kalshibot.strategies.base import StrategyDecision
from kalshibot.strategies.context import StrategyContext
from kalshibot.utils import utc_now_iso


def insert_strategy_signal(
    connection: sqlite3.Connection,
    context: StrategyContext,
    decision: StrategyDecision,
    *,
    created_at: str | None = None,
) -> int | None:
    if decision.signal_type == "none":
        return None
    check = context.check
    cursor = connection.execute(
        """
        INSERT INTO strategy_signals (
            observation_id, run_id, observed_at,
            strategy_id, strategy_version, signal_type,
            label, outcome, kalshi_ticker, polymarket_token_id, polymarket_condition_id,
            side, direction,
            score, confidence,
            fair_value, entry_price, mark_price, edge, fee_adjusted_edge,
            kalshi_buy_price, kalshi_sell_price, polymarket_buy_price,
            polymarket_mid_price, kalshi_mid_price, polymarket_mid_minus_kalshi_mid,
            polymarket_mid_delta, kalshi_mid_delta,
            polymarket_open_interest, polymarket_open_interest_delta,
            polymarket_volume, polymarket_volume_delta,
            reasons_json, rejection_reasons_json, metadata_json,
            created_at
        )
        VALUES (
            :observation_id, :run_id, :observed_at,
            :strategy_id, :strategy_version, :signal_type,
            :label, :outcome, :kalshi_ticker, :polymarket_token_id, :polymarket_condition_id,
            :side, :direction,
            :score, :confidence,
            :fair_value, :entry_price, :mark_price, :edge, :fee_adjusted_edge,
            :kalshi_buy_price, :kalshi_sell_price, :polymarket_buy_price,
            :polymarket_mid_price, :kalshi_mid_price, :polymarket_mid_minus_kalshi_mid,
            :polymarket_mid_delta, :kalshi_mid_delta,
            :polymarket_open_interest, :polymarket_open_interest_delta,
            :polymarket_volume, :polymarket_volume_delta,
            :reasons_json, :rejection_reasons_json, :metadata_json,
            :created_at
        )
        """,
        {
            "observation_id": context.observation_id,
            "run_id": context.run_id,
            "observed_at": context.observed_at,
            "strategy_id": decision.strategy_id,
            "strategy_version": decision.strategy_version,
            "signal_type": decision.signal_type,
            "label": check.label,
            "outcome": check.outcome,
            "kalshi_ticker": check.kalshi_ticker,
            "polymarket_token_id": check.polymarket_token_id,
            "polymarket_condition_id": check.polymarket_condition_id,
            "side": decision.side,
            "direction": decision.direction,
            "score": decimal_string(decision.score),
            "confidence": decimal_string(decision.confidence),
            "fair_value": decimal_string(decision.fair_value),
            "entry_price": decimal_string(decision.entry_price),
            "mark_price": decimal_string(decision.mark_price),
            "edge": decimal_string(decision.edge),
            "fee_adjusted_edge": decimal_string(decision.fee_adjusted_edge),
            "kalshi_buy_price": decimal_string(check.kalshi_buy_price),
            "kalshi_sell_price": decimal_string(check.kalshi_sell_price),
            "polymarket_buy_price": decimal_string(check.polymarket_buy_price),
            "polymarket_mid_price": decimal_string(check.polymarket_mid_price),
            "kalshi_mid_price": decimal_string(check.kalshi_mid_price),
            "polymarket_mid_minus_kalshi_mid": decimal_string(
                check.polymarket_mid_minus_kalshi_mid
            ),
            "polymarket_mid_delta": context.metrics.get("polymarket_mid_delta"),
            "kalshi_mid_delta": context.metrics.get("kalshi_mid_delta"),
            "polymarket_open_interest": decimal_string(check.polymarket_open_interest),
            "polymarket_open_interest_delta": context.metrics.get(
                "polymarket_open_interest_delta"
            ),
            "polymarket_volume": decimal_string(check.polymarket_volume),
            "polymarket_volume_delta": context.metrics.get("polymarket_volume_delta"),
            "reasons_json": json.dumps(list(decision.reasons), sort_keys=True),
            "rejection_reasons_json": json.dumps(
                list(decision.rejection_reasons),
                sort_keys=True,
            ),
            "metadata_json": json.dumps(decision.metadata, default=str, sort_keys=True),
            "created_at": created_at or utc_now_iso(),
        },
    )
    return int(cursor.lastrowid)


def list_strategy_signals(
    connection: sqlite3.Connection,
    *,
    strategy_id: str | None = None,
) -> list[dict[str, Any]]:
    query = "SELECT * FROM strategy_signals"
    params: tuple[str, ...] = ()
    if strategy_id is not None:
        query += " WHERE strategy_id = ?"
        params = (strategy_id,)
    query += " ORDER BY id"
    cursor = connection.execute(query, params)
    columns = [column[0] for column in cursor.description]
    return [decode_strategy_signal_row(dict(zip(columns, row))) for row in cursor.fetchall()]


def decode_strategy_signal_row(row: dict[str, Any]) -> dict[str, Any]:
    decoded = dict(row)
    decoded["reasons"] = tuple(json.loads(str(decoded.pop("reasons_json"))))
    decoded["rejection_reasons"] = tuple(json.loads(str(decoded.pop("rejection_reasons_json"))))
    decoded["metadata"] = json.loads(str(decoded.pop("metadata_json")))
    return decoded


def decimal_string(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None

