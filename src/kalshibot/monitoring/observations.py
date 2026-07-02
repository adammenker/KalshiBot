from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import json
import sqlite3
from pathlib import Path
from typing import Any

from kalshibot.defaults import (
    DEFAULT_MAX_KALSHI_MID_MOVE,
    DEFAULT_MIN_MID_EDGE,
    DEFAULT_MIN_POLY_MID_MOVE,
    DEFAULT_MIN_POLY_OI_DELTA,
    DEFAULT_MIN_POLY_VOLUME_DELTA,
    DEFAULT_SIGNAL_LOOKBACK_MINUTES,
)
from kalshibot.monitoring.formatting import format_timed_spread_check
from kalshibot.monitoring.models import TimedSpreadCheck
from kalshibot.paper import (
    PaperExitConfig,
    append_paper_trade_events,
    create_open_paper_trade,
    update_open_paper_trades,
    write_paper_pnl_snapshot,
)
from kalshibot.signals import (
    historical_signal_metrics,
    open_interest_metrics,
    signal_filter_reasons,
)
from kalshibot.storage import initialize_database
from kalshibot.utils import first_response_venue


@dataclass(frozen=True)
class ObservationSaveResult:
    observation_id: int
    signal_fields: dict[str, Any]


def save_observation(
    path: Path,
    timed_check: TimedSpreadCheck,
    *,
    signal_lookback_minutes: int = DEFAULT_SIGNAL_LOOKBACK_MINUTES,
    min_mid_edge: Decimal = DEFAULT_MIN_MID_EDGE,
    min_poly_mid_move: Decimal = DEFAULT_MIN_POLY_MID_MOVE,
    min_poly_oi_delta: Decimal = DEFAULT_MIN_POLY_OI_DELTA,
    min_poly_volume_delta: Decimal = DEFAULT_MIN_POLY_VOLUME_DELTA,
    max_kalshi_mid_move: Decimal = DEFAULT_MAX_KALSHI_MID_MOVE,
    paper_exit_config: PaperExitConfig | None = None,
    paper_trade_log_path: Path | None = None,
    paper_pnl_log_path: Path | None = None,
) -> int:
    results = save_observations(
        path,
        [timed_check],
        signal_lookback_minutes=signal_lookback_minutes,
        min_mid_edge=min_mid_edge,
        min_poly_mid_move=min_poly_mid_move,
        min_poly_oi_delta=min_poly_oi_delta,
        min_poly_volume_delta=min_poly_volume_delta,
        max_kalshi_mid_move=max_kalshi_mid_move,
        paper_exit_config=paper_exit_config,
        paper_trade_log_path=paper_trade_log_path,
        paper_pnl_log_path=paper_pnl_log_path,
    )
    return results[0].observation_id


def save_observations(
    path: Path,
    timed_checks: list[TimedSpreadCheck],
    *,
    signal_lookback_minutes: int = DEFAULT_SIGNAL_LOOKBACK_MINUTES,
    min_mid_edge: Decimal = DEFAULT_MIN_MID_EDGE,
    min_poly_mid_move: Decimal = DEFAULT_MIN_POLY_MID_MOVE,
    min_poly_oi_delta: Decimal = DEFAULT_MIN_POLY_OI_DELTA,
    min_poly_volume_delta: Decimal = DEFAULT_MIN_POLY_VOLUME_DELTA,
    max_kalshi_mid_move: Decimal = DEFAULT_MAX_KALSHI_MID_MOVE,
    paper_exit_config: PaperExitConfig | None = None,
    paper_trade_log_path: Path | None = None,
    paper_pnl_log_path: Path | None = None,
) -> list[ObservationSaveResult]:
    initialize_database(path)
    results: list[ObservationSaveResult] = []
    trade_events = []
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        for timed_check in timed_checks:
            result, new_trade_events = save_observation_on_connection(
                connection,
                timed_check,
                signal_lookback_minutes=signal_lookback_minutes,
                min_mid_edge=min_mid_edge,
                min_poly_mid_move=min_poly_mid_move,
                min_poly_oi_delta=min_poly_oi_delta,
                min_poly_volume_delta=min_poly_volume_delta,
                max_kalshi_mid_move=max_kalshi_mid_move,
                paper_exit_config=paper_exit_config,
            )
            results.append(result)
            trade_events.extend(new_trade_events)
    if trade_events and paper_trade_log_path is not None:
        append_paper_trade_events(paper_trade_log_path, trade_events)
        if paper_pnl_log_path is not None:
            write_paper_pnl_snapshot(paper_pnl_log_path, path)
    return results


def save_observation_on_connection(
    connection: sqlite3.Connection,
    timed_check: TimedSpreadCheck,
    *,
    signal_lookback_minutes: int,
    min_mid_edge: Decimal,
    min_poly_mid_move: Decimal,
    min_poly_oi_delta: Decimal,
    min_poly_volume_delta: Decimal,
    max_kalshi_mid_move: Decimal,
    paper_exit_config: PaperExitConfig | None,
) -> tuple[ObservationSaveResult, list[Any]]:
    oi_metrics = open_interest_metrics(connection, timed_check.check)
    momentum_metrics = historical_signal_metrics(
        connection,
        timed_check.check,
        timed_check.observed_at,
        lookback_minutes=signal_lookback_minutes,
    )
    signal_metrics = {**oi_metrics, **momentum_metrics}
    filter_reasons = signal_filter_reasons(
        timed_check.check,
        signal_metrics,
        min_mid_edge=min_mid_edge,
        min_poly_mid_move=min_poly_mid_move,
        min_poly_oi_delta=min_poly_oi_delta,
        min_poly_volume_delta=min_poly_volume_delta,
        max_kalshi_mid_move=max_kalshi_mid_move,
    )
    row = observation_row(
        timed_check,
        oi_metrics=oi_metrics,
        momentum_metrics=momentum_metrics,
        signal_lookback_minutes=signal_lookback_minutes,
        filter_reasons=filter_reasons,
    )
    cursor = connection.execute(
        """
        INSERT INTO observations (
            run_id, observed_at, comparison_started_at, comparison_completed_at,
            label, outcome, kalshi_ticker, kalshi_url, polymarket_token_id,
            polymarket_url,
            polymarket_condition_id, polymarket_open_interest,
            polymarket_open_interest_previous, polymarket_open_interest_delta,
            polymarket_open_interest_delta_pct,
            polymarket_volume, polymarket_volume_previous, polymarket_volume_delta,
            kalshi_mid_price, kalshi_mid_previous, kalshi_mid_delta,
            polymarket_mid_price, polymarket_mid_previous, polymarket_mid_delta,
            polymarket_mid_minus_kalshi_mid, signal_lookback_minutes,
            kalshi_request_started_at, kalshi_response_received_at, kalshi_latency_ms,
            polymarket_request_started_at, polymarket_response_received_at,
            polymarket_latency_ms, response_skew_ms, first_response_venue,
            kalshi_buy_price, kalshi_sell_price, kalshi_buy_size, kalshi_buy_depth,
            kalshi_spread, polymarket_buy_price, polymarket_sell_price,
            polymarket_buy_size, polymarket_buy_depth, polymarket_spread,
            depth_window, polymarket_minus_kalshi, kalshi_entry_fee,
            kalshi_exit_fee, kalshi_round_trip_fee, fee_mode, fee_adjustment,
            fee_adjusted_edge,
            kalshi_lower, passes_filters,
            filter_reasons, raw_json
        ) VALUES (
            :run_id, :observed_at, :comparison_started_at, :comparison_completed_at,
            :label, :outcome, :kalshi_ticker, :kalshi_url, :polymarket_token_id,
            :polymarket_url,
            :polymarket_condition_id, :polymarket_open_interest,
            :polymarket_open_interest_previous, :polymarket_open_interest_delta,
            :polymarket_open_interest_delta_pct,
            :polymarket_volume, :polymarket_volume_previous, :polymarket_volume_delta,
            :kalshi_mid_price, :kalshi_mid_previous, :kalshi_mid_delta,
            :polymarket_mid_price, :polymarket_mid_previous, :polymarket_mid_delta,
            :polymarket_mid_minus_kalshi_mid, :signal_lookback_minutes,
            :kalshi_request_started_at, :kalshi_response_received_at, :kalshi_latency_ms,
            :polymarket_request_started_at, :polymarket_response_received_at,
            :polymarket_latency_ms, :response_skew_ms, :first_response_venue,
            :kalshi_buy_price, :kalshi_sell_price, :kalshi_buy_size, :kalshi_buy_depth,
            :kalshi_spread, :polymarket_buy_price, :polymarket_sell_price,
            :polymarket_buy_size, :polymarket_buy_depth, :polymarket_spread,
            :depth_window, :polymarket_minus_kalshi, :kalshi_entry_fee,
            :kalshi_exit_fee, :kalshi_round_trip_fee, :fee_mode, :fee_adjustment,
            :fee_adjusted_edge,
            :kalshi_lower, :passes_filters,
            :filter_reasons, :raw_json
        )
        """,
        row,
    )
    observation_id = int(cursor.lastrowid)
    trade_events = []
    if row["passes_filters"]:
        signal_cursor = connection.execute(
            """
            INSERT INTO paper_signals (
                observation_id, run_id, created_at, label, outcome,
                simulated_entry_venue, simulated_entry_price, comparison_price, edge
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                observation_id,
                timed_check.run_id,
                timed_check.observed_at,
                timed_check.check.label,
                timed_check.check.outcome,
                "kalshi",
                str(timed_check.check.kalshi_buy_price),
                str(timed_check.check.polymarket_buy_price),
                str(timed_check.check.polymarket_minus_kalshi),
            ),
        )
        trade_event = create_open_paper_trade(
            connection,
            signal_id=int(signal_cursor.lastrowid),
            observation_id=observation_id,
            timed_check=timed_check,
        )
        if trade_event is not None:
            trade_events.append(trade_event)
    trade_events.extend(
        update_open_paper_trades(
            connection,
            observation_id,
            timed_check,
            exit_config=paper_exit_config,
        )
    )
    return (
        ObservationSaveResult(
            observation_id=observation_id,
            signal_fields=signal_fields_from_row(row),
        ),
        trade_events,
    )


def observation_signal_fields(path: Path, observation_id: int) -> dict[str, Any]:
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT passes_filters, filter_reasons, signal_lookback_minutes,
                polymarket_open_interest_previous, polymarket_open_interest_delta,
                polymarket_open_interest_delta_pct,
                polymarket_volume_previous, polymarket_volume_delta,
                kalshi_mid_previous, kalshi_mid_delta,
                polymarket_mid_previous, polymarket_mid_delta
            FROM observations
            WHERE id = ?
            """,
            (observation_id,),
        ).fetchone()
    if row is None:
        return {}
    return signal_fields_from_row(row)


def signal_fields_from_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return {
        "passes_filters": bool(row["passes_filters"]),
        "filter_reasons": row["filter_reasons"],
        "signal_lookback_minutes": row["signal_lookback_minutes"],
        "polymarket_open_interest_previous": row["polymarket_open_interest_previous"],
        "polymarket_open_interest_delta": row["polymarket_open_interest_delta"],
        "polymarket_open_interest_delta_pct": row["polymarket_open_interest_delta_pct"],
        "polymarket_volume_previous": row["polymarket_volume_previous"],
        "polymarket_volume_delta": row["polymarket_volume_delta"],
        "kalshi_mid_previous": row["kalshi_mid_previous"],
        "kalshi_mid_delta": row["kalshi_mid_delta"],
        "polymarket_mid_previous": row["polymarket_mid_previous"],
        "polymarket_mid_delta": row["polymarket_mid_delta"],
    }


def observation_row(
    timed_check: TimedSpreadCheck,
    *,
    oi_metrics: dict[str, str | None] | None = None,
    momentum_metrics: dict[str, str | None] | None = None,
    signal_lookback_minutes: int = DEFAULT_SIGNAL_LOOKBACK_MINUTES,
    filter_reasons: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    check = timed_check.check
    formatted = format_timed_spread_check(timed_check)
    metrics = oi_metrics or {
        "polymarket_open_interest": str(check.polymarket_open_interest)
        if check.polymarket_open_interest is not None
        else None,
        "polymarket_open_interest_previous": None,
        "polymarket_open_interest_delta": None,
        "polymarket_open_interest_delta_pct": None,
    }
    momentum = momentum_metrics or {
        "polymarket_volume": str(check.polymarket_volume)
        if check.polymarket_volume is not None
        else None,
        "polymarket_volume_previous": None,
        "polymarket_volume_delta": None,
        "kalshi_mid_price": str(check.kalshi_mid_price) if check.kalshi_mid_price is not None else None,
        "kalshi_mid_previous": None,
        "kalshi_mid_delta": None,
        "polymarket_mid_price": str(check.polymarket_mid_price)
        if check.polymarket_mid_price is not None
        else None,
        "polymarket_mid_previous": None,
        "polymarket_mid_delta": None,
        "polymarket_mid_minus_kalshi_mid": str(check.polymarket_mid_minus_kalshi_mid)
        if check.polymarket_mid_minus_kalshi_mid is not None
        else None,
    }
    final_reasons = filter_reasons if filter_reasons is not None else check.filter_reasons
    passes_filters = not final_reasons
    formatted.update(metrics)
    formatted.update(momentum)
    formatted.update(
        {
            "signal_lookback_minutes": signal_lookback_minutes,
            "passes_filters": passes_filters,
            "filter_reasons": ", ".join(final_reasons),
        }
    )
    return {
        "run_id": timed_check.run_id,
        "observed_at": timed_check.observed_at,
        "comparison_started_at": timed_check.comparison_started_at,
        "comparison_completed_at": timed_check.comparison_completed_at,
        "label": check.label,
        "outcome": check.outcome,
        "kalshi_ticker": check.kalshi_ticker,
        "kalshi_url": check.kalshi_url,
        "polymarket_token_id": check.polymarket_token_id,
        "polymarket_url": check.polymarket_url,
        "polymarket_condition_id": check.polymarket_condition_id,
        **metrics,
        **momentum,
        "signal_lookback_minutes": str(signal_lookback_minutes),
        "kalshi_request_started_at": timed_check.kalshi_request_started_at,
        "kalshi_response_received_at": timed_check.kalshi_response_received_at,
        "kalshi_latency_ms": str(timed_check.kalshi_latency_ms),
        "polymarket_request_started_at": timed_check.polymarket_request_started_at,
        "polymarket_response_received_at": timed_check.polymarket_response_received_at,
        "polymarket_latency_ms": str(timed_check.polymarket_latency_ms),
        "response_skew_ms": str(timed_check.response_skew_ms),
        "first_response_venue": first_response_venue(
            timed_check.kalshi_response_received_at,
            timed_check.polymarket_response_received_at,
        ),
        "kalshi_buy_price": str(check.kalshi_buy_price),
        "kalshi_sell_price": str(check.kalshi_sell_price) if check.kalshi_sell_price else None,
        "kalshi_buy_size": str(check.kalshi_buy_size) if check.kalshi_buy_size else None,
        "kalshi_buy_depth": str(check.kalshi_buy_depth),
        "kalshi_spread": str(check.kalshi_spread) if check.kalshi_spread else None,
        "polymarket_buy_price": str(check.polymarket_buy_price),
        "polymarket_sell_price": str(check.polymarket_sell_price)
        if check.polymarket_sell_price
        else None,
        "polymarket_buy_size": str(check.polymarket_buy_size)
        if check.polymarket_buy_size
        else None,
        "polymarket_buy_depth": str(check.polymarket_buy_depth),
        "polymarket_spread": str(check.polymarket_spread) if check.polymarket_spread else None,
        "depth_window": str(check.depth_window),
        "polymarket_minus_kalshi": str(check.polymarket_minus_kalshi),
        "kalshi_entry_fee": str(check.kalshi_entry_fee),
        "kalshi_exit_fee": str(check.kalshi_exit_fee),
        "kalshi_round_trip_fee": str(check.kalshi_round_trip_fee),
        "fee_mode": check.fee_mode,
        "fee_adjustment": str(check.fee_adjustment),
        "fee_adjusted_edge": str(check.fee_adjusted_edge),
        "kalshi_lower": int(check.kalshi_lower),
        "passes_filters": int(passes_filters),
        "filter_reasons": ", ".join(final_reasons),
        "raw_json": json.dumps(formatted, sort_keys=True),
    }
