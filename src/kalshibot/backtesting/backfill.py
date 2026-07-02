from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from kalshibot.backtesting.models import BackfillSummary
from kalshibot.backtesting.storage import (
    align_pair_history,
    initialize_historical_database,
    save_kalshi_history,
    save_polymarket_history,
)
from kalshibot.client import KalshiClient
from kalshibot.polymarket import PolymarketClient
from kalshibot.spreads import MarketPair


def infer_series_ticker(kalshi_ticker: str) -> str:
    return kalshi_ticker.split("-", maxsplit=1)[0]


def backfill_pair_history(
    *,
    db_path: Path,
    pair: MarketPair,
    kalshi_client: KalshiClient,
    polymarket_client: PolymarketClient,
    start_ts: int,
    end_ts: int,
    period_interval: int = 1,
    polymarket_interval: str = "1m",
    series_ticker: str | None = None,
) -> BackfillSummary:
    initialize_historical_database(db_path)
    series = series_ticker or infer_series_ticker(pair.kalshi_ticker)
    kalshi_payload = kalshi_client.get_market_candlesticks(
        series,
        pair.kalshi_ticker,
        start_ts=start_ts,
        end_ts=end_ts,
        period_interval=period_interval,
    )
    polymarket_payload = polymarket_client.get_prices_history(
        pair.polymarket_token_id,
        start_ts=start_ts,
        end_ts=end_ts,
        interval=polymarket_interval,
        fidelity=period_interval,
    )

    with sqlite3.connect(db_path) as connection:
        kalshi_rows = save_kalshi_history(connection, pair, kalshi_payload)
        polymarket_rows = save_polymarket_history(connection, pair, polymarket_payload)
        aligned_rows = align_pair_history(connection, pair)

    return BackfillSummary(
        label=pair.label,
        kalshi_ticker=pair.kalshi_ticker,
        polymarket_token_id=pair.polymarket_token_id,
        kalshi_rows=kalshi_rows,
        polymarket_rows=polymarket_rows,
        aligned_rows=aligned_rows,
    )


def format_backfill_summary(summary: BackfillSummary) -> dict[str, Any]:
    return {
        "label": summary.label,
        "kalshi_ticker": summary.kalshi_ticker,
        "polymarket_token_id": summary.polymarket_token_id,
        "kalshi_rows": summary.kalshi_rows,
        "polymarket_rows": summary.polymarket_rows,
        "aligned_rows": summary.aligned_rows,
    }
