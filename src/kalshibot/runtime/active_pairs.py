from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import sqlite3
from typing import Any, Literal

from kalshibot.spreads import MarketPair, market_pair_from_dict
from kalshibot.utils import utc_now_iso

ActivePairStatus = Literal["active", "inactive_closed", "inactive_failed", "inactive_stale"]


def create_active_market_pairs_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS active_market_pairs (
            pair_key TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            source TEXT NOT NULL,
            label TEXT NOT NULL,
            outcome TEXT NOT NULL,
            kalshi_ticker TEXT NOT NULL,
            kalshi_url TEXT,
            polymarket_token_id TEXT NOT NULL,
            polymarket_url TEXT,
            polymarket_condition_id TEXT,
            polymarket_slug TEXT,
            polymarket_yes_token_id TEXT,
            polymarket_no_token_id TEXT,
            side_mapping TEXT NOT NULL,
            category TEXT,
            confidence TEXT,
            match_status TEXT,
            target_size TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            last_checked_at TEXT,
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            deactivated_at TEXT,
            deactivation_reason TEXT,
            raw_json TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_active_market_pairs_status
        ON active_market_pairs(status)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_active_market_pairs_kalshi_ticker
        ON active_market_pairs(kalshi_ticker)
        """
    )


def active_pair_key(pair: MarketPair) -> str:
    return f"{pair.kalshi_ticker}:{pair.polymarket_token_id}:{pair.outcome}"


def upsert_active_market_pairs(
    connection: sqlite3.Connection,
    pairs: list[MarketPair],
    *,
    source: str,
    observed_at: str | None = None,
) -> int:
    create_active_market_pairs_table(connection)
    now = observed_at or utc_now_iso()
    inserted_or_updated = 0
    for pair in pairs:
        payload = market_pair_to_dict(pair)
        connection.execute(
            """
            INSERT INTO active_market_pairs (
                pair_key, status, source, label, outcome, kalshi_ticker, kalshi_url,
                polymarket_token_id, polymarket_url, polymarket_condition_id, polymarket_slug,
                polymarket_yes_token_id, polymarket_no_token_id, side_mapping, category,
                confidence, match_status, target_size, first_seen_at, last_seen_at,
                consecutive_failures, deactivated_at, deactivation_reason, raw_json
            )
            VALUES (?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, NULL, ?)
            ON CONFLICT(pair_key) DO UPDATE SET
                status = 'active',
                source = excluded.source,
                label = excluded.label,
                outcome = excluded.outcome,
                kalshi_ticker = excluded.kalshi_ticker,
                kalshi_url = excluded.kalshi_url,
                polymarket_token_id = excluded.polymarket_token_id,
                polymarket_url = excluded.polymarket_url,
                polymarket_condition_id = excluded.polymarket_condition_id,
                polymarket_slug = excluded.polymarket_slug,
                polymarket_yes_token_id = excluded.polymarket_yes_token_id,
                polymarket_no_token_id = excluded.polymarket_no_token_id,
                side_mapping = excluded.side_mapping,
                category = excluded.category,
                confidence = excluded.confidence,
                match_status = excluded.match_status,
                target_size = excluded.target_size,
                last_seen_at = excluded.last_seen_at,
                consecutive_failures = 0,
                deactivated_at = NULL,
                deactivation_reason = NULL,
                raw_json = excluded.raw_json
            """,
            (
                active_pair_key(pair),
                source,
                pair.label,
                pair.outcome,
                pair.kalshi_ticker,
                pair.kalshi_url,
                pair.polymarket_token_id,
                pair.polymarket_url,
                pair.polymarket_condition_id,
                pair.polymarket_slug,
                pair.polymarket_yes_token_id,
                pair.polymarket_no_token_id,
                pair.side_mapping,
                pair.category,
                str(pair.confidence) if pair.confidence is not None else None,
                pair.match_status,
                str(pair.target_size),
                now,
                now,
                json.dumps(payload, sort_keys=True),
            ),
        )
        inserted_or_updated += 1
    return inserted_or_updated


def load_active_market_pairs(
    connection: sqlite3.Connection,
    *,
    limit: int | None = None,
) -> list[MarketPair]:
    create_active_market_pairs_table(connection)
    sql = """
        SELECT raw_json
        FROM active_market_pairs
        WHERE status = 'active'
        ORDER BY last_seen_at DESC, first_seen_at ASC
    """
    params: tuple[Any, ...] = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (limit,)
    rows = connection.execute(sql, params).fetchall()
    return [market_pair_from_dict(json.loads(row["raw_json"])) for row in rows]


def mark_pair_inactive(
    connection: sqlite3.Connection,
    pair_or_key: MarketPair | str,
    *,
    status: ActivePairStatus,
    reason: str,
    observed_at: str | None = None,
) -> None:
    if status == "active":
        raise ValueError("mark_pair_inactive requires an inactive status")
    create_active_market_pairs_table(connection)
    key = active_pair_key(pair_or_key) if isinstance(pair_or_key, MarketPair) else pair_or_key
    now = observed_at or utc_now_iso()
    connection.execute(
        """
        UPDATE active_market_pairs
        SET status = ?, deactivated_at = ?, deactivation_reason = ?
        WHERE pair_key = ?
        """,
        (status, now, reason, key),
    )


def record_pair_failure(
    connection: sqlite3.Connection,
    pair_or_key: MarketPair | str,
    *,
    observed_at: str | None = None,
) -> int:
    create_active_market_pairs_table(connection)
    key = active_pair_key(pair_or_key) if isinstance(pair_or_key, MarketPair) else pair_or_key
    now = observed_at or utc_now_iso()
    connection.execute(
        """
        UPDATE active_market_pairs
        SET consecutive_failures = consecutive_failures + 1, last_checked_at = ?
        WHERE pair_key = ?
        """,
        (now, key),
    )
    row = connection.execute(
        "SELECT consecutive_failures FROM active_market_pairs WHERE pair_key = ?",
        (key,),
    ).fetchone()
    return int(row["consecutive_failures"]) if row else 0


def reset_pair_failure(
    connection: sqlite3.Connection,
    pair_or_key: MarketPair | str,
    *,
    observed_at: str | None = None,
) -> None:
    create_active_market_pairs_table(connection)
    key = active_pair_key(pair_or_key) if isinstance(pair_or_key, MarketPair) else pair_or_key
    now = observed_at or utc_now_iso()
    connection.execute(
        """
        UPDATE active_market_pairs
        SET consecutive_failures = 0, last_checked_at = ?
        WHERE pair_key = ?
        """,
        (now, key),
    )


def write_active_pairs_snapshot(path: Path, pairs: list[MarketPair]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "markets": [market_pair_to_dict(pair) for pair in pairs],
                "updated_at": utc_now_iso(),
            },
            indent=2,
            sort_keys=True,
        )
    )


def market_pair_to_dict(pair: MarketPair) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "label": pair.label,
        "kalshi_ticker": pair.kalshi_ticker,
        "kalshi_url": pair.kalshi_url,
        "polymarket_token_id": pair.polymarket_token_id,
        "polymarket_url": pair.polymarket_url,
        "polymarket_condition_id": pair.polymarket_condition_id,
        "polymarket_slug": pair.polymarket_slug,
        "polymarket_yes_token_id": pair.polymarket_yes_token_id,
        "polymarket_no_token_id": pair.polymarket_no_token_id,
        "side_mapping": pair.side_mapping,
        "category": pair.category,
        "confidence": str(pair.confidence) if pair.confidence is not None else None,
        "match_status": pair.match_status,
        "outcome": pair.outcome,
        "target_size": str(pair.target_size),
    }
    return {key: value for key, value in payload.items() if value is not None}


def market_pairs_from_payload(payload: dict[str, Any]) -> list[MarketPair]:
    pairs = payload.get("markets") if isinstance(payload, dict) else None
    if not isinstance(pairs, list):
        return []
    return [market_pair_from_dict(pair) for pair in pairs if isinstance(pair, dict)]


def decimal_or_none(value: str | None) -> Decimal | None:
    return Decimal(value) if value not in {None, ""} else None
