from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from kalshibot.storage import initialize_database
from kalshibot.utils import format_float, format_ratio


def analyze_database(
    path: Path,
    *,
    market_limit: int = 20,
    strategy_signal_limit: int = 20,
) -> dict[str, Any]:
    if not path.exists():
        raise ValueError(f"Database does not exist: {path}")
    initialize_database(path)

    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        summary = connection.execute(
            """
            SELECT
                COUNT(*) AS observation_count,
                COALESCE(SUM(passes_filters), 0) AS passing_observation_count,
                COALESCE(SUM(kalshi_lower), 0) AS kalshi_lower_count,
                COALESCE(SUM(CASE WHEN CAST(polymarket_minus_kalshi AS REAL) > 0 THEN 1 ELSE 0 END), 0)
                    AS positive_edge_count,
                MIN(observed_at) AS first_observed_at,
                MAX(observed_at) AS last_observed_at,
                AVG(CAST(polymarket_minus_kalshi AS REAL)) AS average_edge,
                MIN(CAST(polymarket_minus_kalshi AS REAL)) AS minimum_edge,
                MAX(CAST(polymarket_minus_kalshi AS REAL)) AS maximum_edge,
                AVG(CAST(response_skew_ms AS REAL)) AS average_response_skew_ms,
                MAX(CAST(response_skew_ms AS REAL)) AS maximum_response_skew_ms,
                AVG(CAST(kalshi_latency_ms AS REAL)) AS average_kalshi_latency_ms,
                AVG(CAST(polymarket_latency_ms AS REAL)) AS average_polymarket_latency_ms
            FROM observations
            """
        ).fetchone()
        signal_count = connection.execute("SELECT COUNT(*) FROM paper_signals").fetchone()[0]
        trade_summary = connection.execute(
            """
            SELECT
                COUNT(*) AS trade_count,
                COALESCE(SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END), 0) AS open_trade_count,
                COALESCE(SUM(CASE WHEN status = 'closed' THEN 1 ELSE 0 END), 0)
                    AS closed_trade_count,
                AVG(CAST(latest_unrealized_pnl AS REAL)) AS average_latest_unrealized_pnl,
                SUM(CAST(latest_unrealized_pnl AS REAL)) AS total_latest_unrealized_pnl,
                AVG(CAST(latest_gross_unrealized_pnl AS REAL))
                    AS average_latest_gross_unrealized_pnl,
                SUM(CAST(latest_gross_unrealized_pnl AS REAL))
                    AS total_latest_gross_unrealized_pnl,
                AVG(CAST(latest_hold_to_resolution_ev AS REAL))
                    AS average_latest_hold_to_resolution_ev,
                SUM(CAST(latest_hold_to_resolution_ev AS REAL))
                    AS total_latest_hold_to_resolution_ev,
                SUM(CAST(entry_fee AS REAL)) AS total_entry_fees,
                SUM(CAST(latest_exit_fee AS REAL)) AS total_latest_exit_fees,
                AVG(CAST(realized_pnl AS REAL)) AS average_realized_pnl,
                SUM(CAST(realized_pnl AS REAL)) AS total_realized_pnl,
                AVG(CAST(realized_gross_pnl AS REAL)) AS average_realized_gross_pnl,
                SUM(CAST(realized_gross_pnl AS REAL)) AS total_realized_gross_pnl,
                SUM(CAST(exit_fee AS REAL)) AS total_realized_exit_fees,
                MIN(CAST(worst_unrealized_pnl AS REAL)) AS worst_unrealized_pnl,
                MAX(CAST(best_unrealized_pnl AS REAL)) AS best_unrealized_pnl,
                MIN(CAST(worst_hold_to_resolution_ev AS REAL)) AS worst_hold_to_resolution_ev,
                MAX(CAST(best_hold_to_resolution_ev AS REAL)) AS best_hold_to_resolution_ev
            FROM paper_trades
            """
        ).fetchone()
        first_response_counts = {
            row["first_response_venue"]: row["count"]
            for row in connection.execute(
                """
                SELECT first_response_venue, COUNT(*) AS count
                FROM observations
                GROUP BY first_response_venue
                ORDER BY count DESC, first_response_venue
                """
            ).fetchall()
        }
        filter_reason_counts = count_filter_reasons(connection)
        close_reason_counts = count_close_reasons(connection)
        trade_strategy_summaries = paper_trade_strategy_summaries(connection)
        markets = market_summaries(connection, market_limit)
        strategy_signals = strategy_signal_analysis(
            connection,
            observation_count=int(summary["observation_count"]),
            recent_limit=strategy_signal_limit,
        )

    observation_count = int(summary["observation_count"])
    passing_count = int(summary["passing_observation_count"])
    return {
        "database": str(path),
        "observation_count": observation_count,
        "paper_signal_count": int(signal_count),
        "strategy_signals": strategy_signals,
        "paper_trades": {
            **format_paper_trade_summary(trade_summary),
            "close_reason_counts": close_reason_counts,
            "by_strategy": trade_strategy_summaries,
        },
        "passing_observation_count": passing_count,
        "passing_observation_rate": format_ratio(passing_count, observation_count),
        "kalshi_lower_count": int(summary["kalshi_lower_count"]),
        "positive_edge_count": int(summary["positive_edge_count"]),
        "first_observed_at": summary["first_observed_at"],
        "last_observed_at": summary["last_observed_at"],
        "edge": {
            "average": format_float(summary["average_edge"], places=4),
            "minimum": format_float(summary["minimum_edge"], places=4),
            "maximum": format_float(summary["maximum_edge"], places=4),
        },
        "latency": {
            "average_response_skew_ms": format_float(
                summary["average_response_skew_ms"],
                places=2,
            ),
            "maximum_response_skew_ms": format_float(
                summary["maximum_response_skew_ms"],
                places=2,
            ),
            "average_kalshi_latency_ms": format_float(
                summary["average_kalshi_latency_ms"],
                places=2,
            ),
            "average_polymarket_latency_ms": format_float(
                summary["average_polymarket_latency_ms"],
                places=2,
            ),
            "first_response_counts": first_response_counts,
        },
        "filter_reason_counts": filter_reason_counts,
        "markets": markets,
    }


def count_filter_reasons(connection: sqlite3.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    rows = connection.execute(
        "SELECT filter_reasons FROM observations WHERE filter_reasons != ''"
    ).fetchall()
    for row in rows:
        for reason in str(row["filter_reasons"]).split(", "):
            if reason:
                counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def count_close_reasons(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        row["close_reason"]: row["count"]
        for row in connection.execute(
            """
            SELECT close_reason, COUNT(*) AS count
            FROM paper_trades
            WHERE close_reason IS NOT NULL
            GROUP BY close_reason
            ORDER BY count DESC, close_reason
            """
        ).fetchall()
    }


def paper_trade_strategy_summaries(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            strategy_id,
            strategy_version,
            COUNT(*) AS trade_count,
            COALESCE(SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END), 0) AS open_trade_count,
            COALESCE(SUM(CASE WHEN status = 'closed' THEN 1 ELSE 0 END), 0)
                AS closed_trade_count,
            AVG(CAST(latest_unrealized_pnl AS REAL)) AS average_latest_unrealized_pnl,
            SUM(CAST(latest_unrealized_pnl AS REAL)) AS total_latest_unrealized_pnl,
            AVG(CAST(latest_gross_unrealized_pnl AS REAL))
                AS average_latest_gross_unrealized_pnl,
            SUM(CAST(latest_gross_unrealized_pnl AS REAL))
                AS total_latest_gross_unrealized_pnl,
            AVG(CAST(latest_hold_to_resolution_ev AS REAL))
                AS average_latest_hold_to_resolution_ev,
            SUM(CAST(latest_hold_to_resolution_ev AS REAL))
                AS total_latest_hold_to_resolution_ev,
            SUM(CAST(entry_fee AS REAL)) AS total_entry_fees,
            SUM(CAST(latest_exit_fee AS REAL)) AS total_latest_exit_fees,
            AVG(CAST(realized_pnl AS REAL)) AS average_realized_pnl,
            SUM(CAST(realized_pnl AS REAL)) AS total_realized_pnl,
            AVG(CAST(realized_gross_pnl AS REAL)) AS average_realized_gross_pnl,
            SUM(CAST(realized_gross_pnl AS REAL)) AS total_realized_gross_pnl,
            SUM(CAST(exit_fee AS REAL)) AS total_realized_exit_fees,
            MIN(CAST(worst_unrealized_pnl AS REAL)) AS worst_unrealized_pnl,
            MAX(CAST(best_unrealized_pnl AS REAL)) AS best_unrealized_pnl,
            MIN(CAST(worst_hold_to_resolution_ev AS REAL)) AS worst_hold_to_resolution_ev,
            MAX(CAST(best_hold_to_resolution_ev AS REAL)) AS best_hold_to_resolution_ev
        FROM paper_trades
        GROUP BY strategy_id, strategy_version
        ORDER BY
            CASE WHEN strategy_id IS NULL THEN 0 ELSE 1 END,
            trade_count DESC,
            strategy_id,
            strategy_version
        """
    ).fetchall()
    return [
        {
            "strategy_id": row["strategy_id"],
            "strategy_version": row["strategy_version"],
            **format_paper_trade_summary(row),
        }
        for row in rows
    ]


def format_paper_trade_summary(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "trade_count": int(row["trade_count"]),
        "open_trade_count": int(row["open_trade_count"]),
        "closed_trade_count": int(row["closed_trade_count"]),
        "average_latest_unrealized_pnl": format_float(
            row["average_latest_unrealized_pnl"],
            places=4,
        ),
        "total_latest_unrealized_pnl": format_float(
            row["total_latest_unrealized_pnl"],
            places=4,
        ),
        "average_latest_gross_unrealized_pnl": format_float(
            row["average_latest_gross_unrealized_pnl"],
            places=4,
        ),
        "total_latest_gross_unrealized_pnl": format_float(
            row["total_latest_gross_unrealized_pnl"],
            places=4,
        ),
        "average_latest_hold_to_resolution_ev": format_float(
            row["average_latest_hold_to_resolution_ev"],
            places=4,
        ),
        "total_latest_hold_to_resolution_ev": format_float(
            row["total_latest_hold_to_resolution_ev"],
            places=4,
        ),
        "total_entry_fees": format_float(row["total_entry_fees"], places=4),
        "total_latest_exit_fees": format_float(
            row["total_latest_exit_fees"],
            places=4,
        ),
        "average_realized_pnl": format_float(
            row["average_realized_pnl"],
            places=4,
        ),
        "total_realized_pnl": format_float(
            row["total_realized_pnl"],
            places=4,
        ),
        "average_realized_gross_pnl": format_float(
            row["average_realized_gross_pnl"],
            places=4,
        ),
        "total_realized_gross_pnl": format_float(
            row["total_realized_gross_pnl"],
            places=4,
        ),
        "total_realized_exit_fees": format_float(
            row["total_realized_exit_fees"],
            places=4,
        ),
        "best_unrealized_pnl": format_float(
            row["best_unrealized_pnl"],
            places=4,
        ),
        "worst_unrealized_pnl": format_float(
            row["worst_unrealized_pnl"],
            places=4,
        ),
        "best_hold_to_resolution_ev": format_float(
            row["best_hold_to_resolution_ev"],
            places=4,
        ),
        "worst_hold_to_resolution_ev": format_float(
            row["worst_hold_to_resolution_ev"],
            places=4,
        ),
    }


def strategy_signal_analysis(
    connection: sqlite3.Connection,
    *,
    observation_count: int,
    recent_limit: int,
) -> dict[str, Any]:
    signal_count = connection.execute("SELECT COUNT(*) FROM strategy_signals").fetchone()[0]
    return {
        "signal_count": int(signal_count),
        "signal_rate": format_ratio(int(signal_count), observation_count),
        "signal_type_counts": count_strategy_signal_types(connection),
        "reason_counts": count_strategy_signal_json_values(connection, "reasons_json"),
        "rejection_reason_counts": count_strategy_signal_json_values(
            connection,
            "rejection_reasons_json",
        ),
        "strategies": strategy_signal_summaries(connection, observation_count=observation_count),
        "recent": recent_strategy_signals(connection, limit=recent_limit),
    }


def count_strategy_signal_types(
    connection: sqlite3.Connection,
    *,
    strategy_id: str | None = None,
    strategy_version: str | None = None,
) -> dict[str, int]:
    query = """
        SELECT signal_type, COUNT(*) AS count
        FROM strategy_signals
    """
    params: list[str] = []
    filters = []
    if strategy_id is not None:
        filters.append("strategy_id = ?")
        params.append(strategy_id)
    if strategy_version is not None:
        filters.append("strategy_version = ?")
        params.append(strategy_version)
    if filters:
        query += " WHERE " + " AND ".join(filters)
    query += " GROUP BY signal_type ORDER BY count DESC, signal_type"
    return {
        row["signal_type"]: row["count"]
        for row in connection.execute(query, params).fetchall()
    }


def strategy_signal_summaries(
    connection: sqlite3.Connection,
    *,
    observation_count: int,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            strategy_id,
            strategy_version,
            COUNT(*) AS signal_count,
            MIN(created_at) AS first_signal_at,
            MAX(created_at) AS last_signal_at,
            AVG(CAST(score AS REAL)) AS average_score,
            AVG(CAST(confidence AS REAL)) AS average_confidence,
            AVG(CAST(edge AS REAL)) AS average_edge,
            MIN(CAST(edge AS REAL)) AS minimum_edge,
            MAX(CAST(edge AS REAL)) AS maximum_edge,
            AVG(CAST(fee_adjusted_edge AS REAL)) AS average_fee_adjusted_edge,
            MIN(CAST(fee_adjusted_edge AS REAL)) AS minimum_fee_adjusted_edge,
            MAX(CAST(fee_adjusted_edge AS REAL)) AS maximum_fee_adjusted_edge
        FROM strategy_signals
        GROUP BY strategy_id, strategy_version
        ORDER BY signal_count DESC, strategy_id, strategy_version
        """
    ).fetchall()
    summaries = []
    for row in rows:
        signal_count = int(row["signal_count"])
        summaries.append(
            {
                "strategy_id": row["strategy_id"],
                "strategy_version": row["strategy_version"],
                "signal_count": signal_count,
                "signal_rate": format_ratio(signal_count, observation_count),
                "signal_type_counts": count_strategy_signal_types(
                    connection,
                    strategy_id=row["strategy_id"],
                    strategy_version=row["strategy_version"],
                ),
                "reason_counts": count_strategy_signal_json_values(
                    connection,
                    "reasons_json",
                    strategy_id=row["strategy_id"],
                    strategy_version=row["strategy_version"],
                ),
                "rejection_reason_counts": count_strategy_signal_json_values(
                    connection,
                    "rejection_reasons_json",
                    strategy_id=row["strategy_id"],
                    strategy_version=row["strategy_version"],
                ),
                "first_signal_at": row["first_signal_at"],
                "last_signal_at": row["last_signal_at"],
                "score": {
                    "average": format_float(row["average_score"], places=4),
                },
                "confidence": {
                    "average": format_float(row["average_confidence"], places=4),
                },
                "edge": {
                    "average": format_float(row["average_edge"], places=4),
                    "minimum": format_float(row["minimum_edge"], places=4),
                    "maximum": format_float(row["maximum_edge"], places=4),
                },
                "fee_adjusted_edge": {
                    "average": format_float(row["average_fee_adjusted_edge"], places=4),
                    "minimum": format_float(row["minimum_fee_adjusted_edge"], places=4),
                    "maximum": format_float(row["maximum_fee_adjusted_edge"], places=4),
                },
            }
        )
    return summaries


def count_strategy_signal_json_values(
    connection: sqlite3.Connection,
    column_name: str,
    *,
    strategy_id: str | None = None,
    strategy_version: str | None = None,
) -> dict[str, int]:
    if column_name not in {"reasons_json", "rejection_reasons_json"}:
        raise ValueError("Unsupported strategy signal JSON column")
    query = f"SELECT {column_name} FROM strategy_signals"
    params: list[str] = []
    filters = []
    if strategy_id is not None:
        filters.append("strategy_id = ?")
        params.append(strategy_id)
    if strategy_version is not None:
        filters.append("strategy_version = ?")
        params.append(strategy_version)
    if filters:
        query += " WHERE " + " AND ".join(filters)
    counts: dict[str, int] = {}
    for row in connection.execute(query, params).fetchall():
        for value in parse_json_list(row[column_name]):
            key = str(value)
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def recent_strategy_signals(connection: sqlite3.Connection, *, limit: int) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    rows = connection.execute(
        """
        SELECT
            id,
            observation_id,
            run_id,
            observed_at,
            created_at,
            strategy_id,
            strategy_version,
            signal_type,
            label,
            outcome,
            kalshi_ticker,
            polymarket_token_id,
            side,
            direction,
            score,
            confidence,
            fair_value,
            entry_price,
            mark_price,
            edge,
            fee_adjusted_edge,
            reasons_json,
            rejection_reasons_json,
            metadata_json
        FROM strategy_signals
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [format_recent_strategy_signal(row) for row in rows]


def format_recent_strategy_signal(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "observation_id": row["observation_id"],
        "run_id": row["run_id"],
        "observed_at": row["observed_at"],
        "created_at": row["created_at"],
        "strategy_id": row["strategy_id"],
        "strategy_version": row["strategy_version"],
        "signal_type": row["signal_type"],
        "label": row["label"],
        "outcome": row["outcome"],
        "kalshi_ticker": row["kalshi_ticker"],
        "polymarket_token_id": row["polymarket_token_id"],
        "side": row["side"],
        "direction": row["direction"],
        "score": row["score"],
        "confidence": row["confidence"],
        "fair_value": row["fair_value"],
        "entry_price": row["entry_price"],
        "mark_price": row["mark_price"],
        "edge": row["edge"],
        "fee_adjusted_edge": row["fee_adjusted_edge"],
        "reasons": parse_json_list(row["reasons_json"]),
        "rejection_reasons": parse_json_list(row["rejection_reasons_json"]),
        "metadata": parse_json_object(row["metadata_json"]),
    }


def parse_json_list(value: str | None) -> list[Any]:
    if not value:
        return []
    parsed = json.loads(value)
    return parsed if isinstance(parsed, list) else []


def parse_json_object(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    parsed = json.loads(value)
    return parsed if isinstance(parsed, dict) else {}


def market_summaries(connection: sqlite3.Connection, limit: int) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            label,
            outcome,
            kalshi_ticker,
            polymarket_token_id,
            COUNT(*) AS observation_count,
            COALESCE(SUM(passes_filters), 0) AS passing_observation_count,
            COALESCE(SUM(kalshi_lower), 0) AS kalshi_lower_count,
            AVG(CAST(polymarket_minus_kalshi AS REAL)) AS average_edge,
            MIN(CAST(polymarket_minus_kalshi AS REAL)) AS minimum_edge,
            MAX(CAST(polymarket_minus_kalshi AS REAL)) AS maximum_edge,
            AVG(CAST(polymarket_open_interest_delta AS REAL)) AS average_oi_delta,
            MIN(CAST(polymarket_open_interest_delta AS REAL)) AS minimum_oi_delta,
            MAX(CAST(polymarket_open_interest_delta AS REAL)) AS maximum_oi_delta,
            AVG(CAST(polymarket_volume_delta AS REAL)) AS average_volume_delta,
            MIN(CAST(polymarket_volume_delta AS REAL)) AS minimum_volume_delta,
            MAX(CAST(polymarket_volume_delta AS REAL)) AS maximum_volume_delta,
            AVG(CAST(polymarket_mid_delta AS REAL)) AS average_polymarket_mid_delta,
            AVG(CAST(kalshi_mid_delta AS REAL)) AS average_kalshi_mid_delta,
            AVG(CAST(response_skew_ms AS REAL)) AS average_response_skew_ms,
            COALESCE(SUM(CASE WHEN first_response_venue = 'kalshi' THEN 1 ELSE 0 END), 0)
                AS kalshi_first_count,
            COALESCE(SUM(CASE WHEN first_response_venue = 'polymarket' THEN 1 ELSE 0 END), 0)
                AS polymarket_first_count,
            COALESCE(SUM(CASE WHEN first_response_venue = 'tie' THEN 1 ELSE 0 END), 0)
                AS tie_first_count,
            MAX(id) AS latest_observation_id
        FROM observations
        GROUP BY label, outcome, kalshi_ticker, polymarket_token_id
        ORDER BY observation_count DESC, label
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [format_market_summary(connection, row) for row in rows]


def format_market_summary(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
) -> dict[str, Any]:
    latest = connection.execute(
        """
        SELECT observed_at, polymarket_minus_kalshi, passes_filters, filter_reasons,
            polymarket_open_interest, polymarket_open_interest_previous,
            polymarket_open_interest_delta, polymarket_open_interest_delta_pct,
            polymarket_volume, polymarket_volume_previous, polymarket_volume_delta,
            kalshi_mid_price, kalshi_mid_previous, kalshi_mid_delta,
            polymarket_mid_price, polymarket_mid_previous, polymarket_mid_delta,
            polymarket_mid_minus_kalshi_mid
        FROM observations
        WHERE id = ?
        """,
        (row["latest_observation_id"],),
    ).fetchone()
    observation_count = int(row["observation_count"])
    passing_count = int(row["passing_observation_count"])
    return {
        "label": row["label"],
        "outcome": row["outcome"],
        "kalshi_ticker": row["kalshi_ticker"],
        "polymarket_token_id": row["polymarket_token_id"],
        "observation_count": observation_count,
        "passing_observation_count": passing_count,
        "passing_observation_rate": format_ratio(passing_count, observation_count),
        "kalshi_lower_count": int(row["kalshi_lower_count"]),
        "edge": {
            "average": format_float(row["average_edge"], places=4),
            "minimum": format_float(row["minimum_edge"], places=4),
            "maximum": format_float(row["maximum_edge"], places=4),
            "latest": format_float(latest["polymarket_minus_kalshi"], places=4),
        },
        "polymarket_open_interest": {
            "latest": format_float(latest["polymarket_open_interest"], places=2),
            "previous": format_float(latest["polymarket_open_interest_previous"], places=2),
            "latest_delta": format_float(latest["polymarket_open_interest_delta"], places=2),
            "latest_delta_pct": format_float(
                latest["polymarket_open_interest_delta_pct"],
                places=4,
            ),
            "average_delta": format_float(row["average_oi_delta"], places=2),
            "minimum_delta": format_float(row["minimum_oi_delta"], places=2),
            "maximum_delta": format_float(row["maximum_oi_delta"], places=2),
        },
        "polymarket_volume": {
            "latest": format_float(latest["polymarket_volume"], places=2),
            "previous": format_float(latest["polymarket_volume_previous"], places=2),
            "latest_delta": format_float(latest["polymarket_volume_delta"], places=2),
            "average_delta": format_float(row["average_volume_delta"], places=2),
            "minimum_delta": format_float(row["minimum_volume_delta"], places=2),
            "maximum_delta": format_float(row["maximum_volume_delta"], places=2),
        },
        "mid_prices": {
            "kalshi_latest": format_float(latest["kalshi_mid_price"], places=4),
            "kalshi_previous": format_float(latest["kalshi_mid_previous"], places=4),
            "kalshi_latest_delta": format_float(latest["kalshi_mid_delta"], places=4),
            "kalshi_average_delta": format_float(row["average_kalshi_mid_delta"], places=4),
            "polymarket_latest": format_float(latest["polymarket_mid_price"], places=4),
            "polymarket_previous": format_float(latest["polymarket_mid_previous"], places=4),
            "polymarket_latest_delta": format_float(latest["polymarket_mid_delta"], places=4),
            "polymarket_average_delta": format_float(
                row["average_polymarket_mid_delta"],
                places=4,
            ),
            "latest_mid_edge": format_float(
                latest["polymarket_mid_minus_kalshi_mid"],
                places=4,
            ),
        },
        "average_response_skew_ms": format_float(row["average_response_skew_ms"], places=2),
        "first_response_counts": {
            "kalshi": int(row["kalshi_first_count"]),
            "polymarket": int(row["polymarket_first_count"]),
            "tie": int(row["tie_first_count"]),
        },
        "latest_observed_at": latest["observed_at"],
        "latest_passed_filters": bool(latest["passes_filters"]),
        "latest_filter_reasons": latest["filter_reasons"],
    }
