from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from kalshibot.spreads import SpreadCheck
from kalshibot.utils import optional_decimal


def open_interest_metrics(
    connection: sqlite3.Connection,
    check: SpreadCheck,
) -> dict[str, str | None]:
    current = check.polymarket_open_interest
    previous = previous_open_interest(connection, check)
    delta = current - previous if current is not None and previous is not None else None
    delta_pct = (
        (delta / previous)
        if delta is not None and previous is not None and previous != Decimal("0")
        else None
    )
    return {
        "polymarket_open_interest": str(current) if current is not None else None,
        "polymarket_open_interest_previous": str(previous) if previous is not None else None,
        "polymarket_open_interest_delta": str(delta) if delta is not None else None,
        "polymarket_open_interest_delta_pct": str(delta_pct) if delta_pct is not None else None,
    }


def previous_open_interest(
    connection: sqlite3.Connection,
    check: SpreadCheck,
) -> Decimal | None:
    if not check.polymarket_condition_id:
        return None
    row = connection.execute(
        """
        SELECT polymarket_open_interest
        FROM observations
        WHERE polymarket_condition_id = ?
            AND polymarket_open_interest IS NOT NULL
        ORDER BY id DESC
        LIMIT 1
        """,
        (check.polymarket_condition_id,),
    ).fetchone()
    if row is None or row[0] in {None, ""}:
        return None
    return Decimal(str(row[0]))


def historical_signal_metrics(
    connection: sqlite3.Connection,
    check: SpreadCheck,
    observed_at: str,
    *,
    lookback_minutes: int,
) -> dict[str, str | None]:
    previous = previous_signal_snapshot(
        connection,
        check,
        observed_at,
        lookback_minutes=lookback_minutes,
    )
    previous_kalshi_mid = optional_decimal(previous.get("kalshi_mid_price")) if previous else None
    previous_poly_mid = optional_decimal(previous.get("polymarket_mid_price")) if previous else None
    previous_volume = optional_decimal(previous.get("polymarket_volume")) if previous else None
    current_kalshi_mid = check.kalshi_mid_price
    current_poly_mid = check.polymarket_mid_price
    current_volume = check.polymarket_volume
    kalshi_mid_delta = (
        current_kalshi_mid - previous_kalshi_mid
        if current_kalshi_mid is not None and previous_kalshi_mid is not None
        else None
    )
    poly_mid_delta = (
        current_poly_mid - previous_poly_mid
        if current_poly_mid is not None and previous_poly_mid is not None
        else None
    )
    volume_delta = (
        current_volume - previous_volume
        if current_volume is not None and previous_volume is not None
        else None
    )
    return {
        "polymarket_volume": str(current_volume) if current_volume is not None else None,
        "polymarket_volume_previous": str(previous_volume) if previous_volume is not None else None,
        "polymarket_volume_delta": str(volume_delta) if volume_delta is not None else None,
        "kalshi_mid_price": str(current_kalshi_mid) if current_kalshi_mid is not None else None,
        "kalshi_mid_previous": str(previous_kalshi_mid) if previous_kalshi_mid is not None else None,
        "kalshi_mid_delta": str(kalshi_mid_delta) if kalshi_mid_delta is not None else None,
        "polymarket_mid_price": str(current_poly_mid) if current_poly_mid is not None else None,
        "polymarket_mid_previous": str(previous_poly_mid) if previous_poly_mid is not None else None,
        "polymarket_mid_delta": str(poly_mid_delta) if poly_mid_delta is not None else None,
        "polymarket_mid_minus_kalshi_mid": str(check.polymarket_mid_minus_kalshi_mid)
        if check.polymarket_mid_minus_kalshi_mid is not None
        else None,
    }


def previous_signal_snapshot(
    connection: sqlite3.Connection,
    check: SpreadCheck,
    observed_at: str,
    *,
    lookback_minutes: int,
) -> dict[str, Any] | None:
    cutoff = datetime.fromisoformat(observed_at) - timedelta(minutes=lookback_minutes)
    row = connection.execute(
        """
        SELECT kalshi_mid_price, polymarket_mid_price, polymarket_volume
        FROM observations
        WHERE kalshi_ticker = ?
            AND polymarket_token_id = ?
            AND observed_at <= ?
        ORDER BY observed_at DESC, id DESC
        LIMIT 1
        """,
        (check.kalshi_ticker, check.polymarket_token_id, cutoff.isoformat()),
    ).fetchone()
    if row is None:
        return None
    return {
        "kalshi_mid_price": row[0],
        "polymarket_mid_price": row[1],
        "polymarket_volume": row[2],
    }


def signal_filter_reasons(
    check: SpreadCheck,
    metrics: dict[str, str | None],
    *,
    min_mid_edge: Decimal,
    min_poly_mid_move: Decimal,
    min_poly_oi_delta: Decimal,
    min_poly_volume_delta: Decimal,
    max_kalshi_mid_move: Decimal,
) -> tuple[str, ...]:
    reasons = [
        reason
        for reason in check.filter_reasons
        if reason not in {"polymarket_buy_size_too_small", "polymarket_depth_too_small"}
    ]
    mid_edge = check.polymarket_mid_minus_kalshi_mid
    if mid_edge is None or mid_edge < min_mid_edge:
        reasons.append("mid_edge_below_minimum")

    poly_mid_delta = optional_decimal(metrics.get("polymarket_mid_delta"))
    if min_poly_mid_move > Decimal("0") and poly_mid_delta is None:
        reasons.append("polymarket_mid_history_missing")
    elif (
        min_poly_mid_move > Decimal("0")
        and poly_mid_delta is not None
        and poly_mid_delta < min_poly_mid_move
    ):
        reasons.append("polymarket_mid_move_too_small")

    oi_delta = optional_decimal(metrics.get("polymarket_open_interest_delta"))
    if min_poly_oi_delta > Decimal("0") and oi_delta is None:
        reasons.append("polymarket_oi_history_missing")
    elif min_poly_oi_delta > Decimal("0") and oi_delta is not None and oi_delta < min_poly_oi_delta:
        reasons.append("polymarket_oi_delta_too_small")

    volume_delta = optional_decimal(metrics.get("polymarket_volume_delta"))
    if min_poly_volume_delta > Decimal("0") and volume_delta is None:
        reasons.append("polymarket_volume_history_missing")
    elif (
        min_poly_volume_delta > Decimal("0")
        and volume_delta is not None
        and volume_delta < min_poly_volume_delta
    ):
        reasons.append("polymarket_volume_delta_too_small")

    kalshi_mid_delta = optional_decimal(metrics.get("kalshi_mid_delta"))
    if max_kalshi_mid_move < Decimal("1") and kalshi_mid_delta is None:
        reasons.append("kalshi_mid_history_missing")
    elif kalshi_mid_delta is not None and abs(kalshi_mid_delta) > max_kalshi_mid_move:
        reasons.append("kalshi_mid_moved_too_much")

    return tuple(dict.fromkeys(reasons))
