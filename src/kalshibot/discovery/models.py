from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

SideMapping = Literal["same", "inverse", "unknown"]
MatchStatus = Literal["approved", "maybe", "rejected"]


@dataclass(frozen=True)
class PolymarketDiscoveryMarket:
    event_title: str
    market_question: str
    outcome: str
    token_id: str
    condition_id: str | None
    title: str
    tags: tuple[str, ...] = ()
    end_date: str | None = None
    start_date: str | None = None
    slug: str | None = None
    outcome_token_ids: tuple[tuple[str, str], ...] = ()
    sibling_token_id: str | None = None
    description: str | None = None
    rules_text: str | None = None


@dataclass(frozen=True)
class KalshiDiscoveryMarket:
    ticker: str
    event_ticker: str | None
    title: str
    yes_sub_title: str | None
    no_sub_title: str | None
    close_time: str | None
    full_title: str
    expected_expiration_time: str | None = None
    expiration_time: str | None = None
    volume: Decimal | None = None
    volume_24h: Decimal | None = None
    open_interest: Decimal | None = None
    liquidity: Decimal | None = None
    notional_value: Decimal | None = None
    description: str | None = None
    rules_text: str | None = None


@dataclass(frozen=True)
class NormalizedMarket:
    venue: str
    ticker_or_token_id: str
    title: str
    subtitle: str | None
    question: str | None
    description: str | None
    rules_text: str | None
    category: str | None
    tags: tuple[str, ...]
    event_type: str | None
    entities: tuple[str, ...]
    target_metric: str | None
    comparator: str | None
    threshold: float | None
    unit: str | None
    start_time: str | None
    close_time: str | None
    expiration_time: str | None
    resolution_time: str | None
    resolution_source: str | None
    yes_condition_text: str | None
    no_condition_text: str | None
    settlement_timing: str | None = None


@dataclass(frozen=True)
class DiscoveryMatch:
    polymarket_title: str
    polymarket_token_id: str
    polymarket_condition_id: str | None
    kalshi_title: str
    kalshi_ticker: str
    confidence: float
    reason: str
    method: str
    side_mapping: SideMapping = "same"
    match_status: MatchStatus = "approved"
    category: str | None = None
    polymarket_slug: str | None = None
    polymarket_yes_token_id: str | None = None
    polymarket_no_token_id: str | None = None
    match_notes: tuple[str, ...] = ()
    blocking_issues: tuple[str, ...] = ()
    kalshi_normalized: NormalizedMarket | None = None
    polymarket_normalized: NormalizedMarket | None = None
    date_validation: dict[str, Any] | None = None
    price_validation: dict[str, Any] | None = None


@dataclass(frozen=True)
class DiscoveryCandidate:
    polymarket_market: PolymarketDiscoveryMarket
    kalshi_market: KalshiDiscoveryMarket
    similarity: float


@dataclass(frozen=True)
class PriceValidation:
    passed: bool
    kalshi_mid: Decimal | None
    polymarket_mid: Decimal | None
    difference: Decimal | None
    reason: str


@dataclass(frozen=True)
class DateValidation:
    passed: bool
    kalshi_date: str | None
    polymarket_date: str | None
    difference_days: int | None
    reason: str


@dataclass(frozen=True)
class StructuralValidation:
    passed: bool
    kalshi_market_type: str
    polymarket_market_type: str
    kalshi_domain: str
    polymarket_domain: str
    kalshi_numbers: tuple[str, ...]
    polymarket_numbers: tuple[str, ...]
    shared_entities: tuple[str, ...]
    shared_proper_nouns: tuple[str, ...]
    reasons: tuple[str, ...]
    score: int = 0
    side_mapping: SideMapping = "unknown"
    match_status: MatchStatus = "rejected"
    match_notes: tuple[str, ...] = ()
    blocking_issues: tuple[str, ...] = ()
    kalshi_normalized: NormalizedMarket | None = None
    polymarket_normalized: NormalizedMarket | None = None
