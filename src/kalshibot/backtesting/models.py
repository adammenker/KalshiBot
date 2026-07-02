from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class BackfillSummary:
    label: str
    kalshi_ticker: str
    polymarket_token_id: str
    kalshi_rows: int
    polymarket_rows: int
    aligned_rows: int


@dataclass(frozen=True)
class BacktestTrade:
    label: str
    outcome: str
    kalshi_ticker: str
    polymarket_token_id: str
    entry_ts: int
    exit_ts: int
    entry_price: Decimal
    exit_price: Decimal
    entry_edge: Decimal
    exit_edge: Decimal
    pnl: Decimal
    exit_reason: str
