from __future__ import annotations

from decimal import Decimal
from typing import Any

from kalshibot.client import KalshiClient
from kalshibot.discovery.models import (
    DiscoveryCandidate,
    KalshiDiscoveryMarket,
    PolymarketDiscoveryMarket,
)
from kalshibot.discovery.utils import join_title_parts
from kalshibot.polymarket import PolymarketClient, extract_market_tokens
from kalshibot.utils import optional_decimal, optional_string


def list_polymarket_discovery_markets(
    client: PolymarketClient,
    *,
    event_limit: int,
) -> list[PolymarketDiscoveryMarket]:
    discovery_markets: list[PolymarketDiscoveryMarket] = []
    for event in client.list_events(limit=event_limit):
        if isinstance(event, dict):
            discovery_markets.extend(polymarket_event_discovery_markets(event))
    return unique_polymarket_markets(discovery_markets)


def is_tradeable_polymarket_market(market: dict[str, Any]) -> bool:
    if market.get("closed") is True:
        return False
    if market.get("active") is False:
        return False
    if market.get("accepting_orders") is False:
        return False
    return True


def list_kalshi_discovery_markets(
    client: KalshiClient,
    *,
    limit: int,
    pages: int,
    status: str,
    series_ticker: str | None,
    include_series: set[str] | None = None,
    exclude_series: set[str] | None = None,
) -> list[KalshiDiscoveryMarket]:
    markets: list[dict[str, Any]] = []
    series_queries = sorted(include_series) if include_series and series_ticker is None else [series_ticker]
    for query_series in series_queries:
        markets.extend(
            client.list_markets(
                status=status,
                limit=limit,
                max_pages=pages,
                series_ticker=query_series,
                mve_filter="exclude",
            )
        )
    discovery_markets = [kalshi_discovery_market(market) for market in markets]
    filtered = [
        market
        for market in discovery_markets
        if kalshi_market_passes_series_filters(
            market,
            include_series=include_series,
            exclude_series=exclude_series,
        )
    ]
    return unique_kalshi_markets(filtered)


def unique_kalshi_markets(markets: list[KalshiDiscoveryMarket]) -> list[KalshiDiscoveryMarket]:
    unique: list[KalshiDiscoveryMarket] = []
    seen: set[str] = set()
    for market in markets:
        if market.ticker in seen:
            continue
        seen.add(market.ticker)
        unique.append(market)
    return unique


def kalshi_market_passes_series_filters(
    market: KalshiDiscoveryMarket,
    *,
    include_series: set[str] | None,
    exclude_series: set[str] | None,
) -> bool:
    series = kalshi_market_series(market)
    if include_series and series not in include_series:
        return False
    if exclude_series and series in exclude_series:
        return False
    return True


def kalshi_market_series(market: KalshiDiscoveryMarket) -> str:
    if market.event_ticker:
        return market.event_ticker.split("-")[0]
    return market.ticker.split("-")[0]


def kalshi_discovery_market(market: dict[str, Any]) -> KalshiDiscoveryMarket:
    title = str(market.get("title") or market.get("subtitle") or market.get("ticker") or "")
    yes_sub_title = optional_string(market.get("yes_sub_title"))
    no_sub_title = optional_string(market.get("no_sub_title"))
    return KalshiDiscoveryMarket(
        ticker=str(market.get("ticker") or ""),
        event_ticker=optional_string(market.get("event_ticker")),
        title=title,
        yes_sub_title=yes_sub_title,
        no_sub_title=no_sub_title,
        close_time=optional_string(market.get("close_time")),
        full_title=join_title_parts(title, yes_sub_title),
        expected_expiration_time=optional_string(market.get("expected_expiration_time")),
        expiration_time=optional_string(market.get("expiration_time")),
        description=optional_string(market.get("description")),
        rules_text=optional_string(market.get("rules_primary") or market.get("rules_secondary")),
        volume=market_decimal(market, "volume_fp", "volume"),
        volume_24h=market_decimal(market, "volume_24h_fp", "volume_24h"),
        open_interest=market_decimal(market, "open_interest_fp", "open_interest"),
        liquidity=market_decimal(market, "liquidity_fp", "liquidity", "liquidity_dollars"),
        notional_value=market_decimal(
            market,
            "notional_value_fp",
            "notional_value",
            "notional_value_dollars",
        ),
    )


def market_decimal(market: dict[str, Any], *keys: str) -> Decimal | None:
    for key in keys:
        value = optional_decimal(market.get(key))
        if value is not None:
            return value
    return None


def search_polymarket_discovery_markets(
    client: PolymarketClient,
    *,
    query: str,
    limit: int,
) -> list[PolymarketDiscoveryMarket]:
    payload = client.public_search(query, limit_per_type=limit, keep_closed_markets=0)
    discovery_markets: list[PolymarketDiscoveryMarket] = []
    for event in payload.get("events", []):
        if isinstance(event, dict):
            discovery_markets.extend(polymarket_event_discovery_markets(event))
    for market in payload.get("markets", []):
        if isinstance(market, dict):
            discovery_markets.extend(
                polymarket_market_discovery_markets(
                    market,
                    event_title=str(market.get("title") or market.get("question") or market.get("slug") or ""),
                )
            )
    return unique_polymarket_markets(discovery_markets)


def polymarket_event_discovery_markets(event: dict[str, Any]) -> list[PolymarketDiscoveryMarket]:
    event_title = str(event.get("title") or event.get("slug") or "")
    event_slug = optional_string(event.get("slug"))
    event_tags = extract_polymarket_tags(event)
    event_start_date = optional_string(event.get("startDate") or event.get("start_date"))
    event_end_date = optional_string(event.get("endDate") or event.get("end_date"))
    event_description = optional_string(event.get("description"))
    discovery_markets: list[PolymarketDiscoveryMarket] = []
    for market in event.get("markets", []):
        if isinstance(market, dict):
            discovery_markets.extend(
                polymarket_market_discovery_markets(
                    market,
                    event_title,
                    tags=event_tags,
                    event_slug=event_slug,
                    event_start_date=event_start_date,
                    event_end_date=event_end_date,
                    event_description=event_description,
                )
            )
    return discovery_markets


def polymarket_market_discovery_markets(
    market: dict[str, Any],
    event_title: str,
    *,
    tags: tuple[str, ...] = (),
    event_slug: str | None = None,
    event_start_date: str | None = None,
    event_end_date: str | None = None,
    event_description: str | None = None,
) -> list[PolymarketDiscoveryMarket]:
    if not is_tradeable_polymarket_market(market):
        return []
    question = str(market.get("question") or market.get("title") or event_title)
    condition_id = optional_string(market.get("conditionId") or market.get("condition_id"))
    slug = optional_string(market.get("slug")) or event_slug
    description = optional_string(market.get("description")) or event_description
    rules_text = optional_string(
        market.get("rules")
        or market.get("resolutionSource")
        or market.get("resolution_source")
        or market.get("clarification")
    )
    try:
        tokens = extract_market_tokens(market)
    except ValueError:
        return []
    outcome_token_ids = tuple((token["outcome"], token["token_id"]) for token in tokens)
    return [
        PolymarketDiscoveryMarket(
            event_title=event_title,
            market_question=question,
            outcome=token["outcome"],
            token_id=token["token_id"],
            condition_id=condition_id,
            title=join_title_parts(event_title, question, token["outcome"]),
            tags=tags or extract_polymarket_tags(market),
            start_date=optional_string(market.get("startDate") or market.get("start_date"))
            or event_start_date,
            end_date=optional_string(market.get("endDate") or market.get("end_date"))
            or event_end_date,
            slug=slug,
            outcome_token_ids=outcome_token_ids,
            sibling_token_id=sibling_token_id(token["token_id"], outcome_token_ids),
            description=description,
            rules_text=rules_text,
        )
        for token in tokens
    ]


def sibling_token_id(
    token_id: str,
    outcome_token_ids: tuple[tuple[str, str], ...],
) -> str | None:
    for _, candidate_token_id in outcome_token_ids:
        if candidate_token_id != token_id:
            return candidate_token_id
    return None


def extract_polymarket_tags(payload: dict[str, Any]) -> tuple[str, ...]:
    tags: list[str] = []
    for tag in payload.get("tags") or []:
        if not isinstance(tag, dict):
            continue
        for key in ("label", "slug"):
            value = optional_string(tag.get(key))
            if value:
                tags.append(value.strip().lower())
    return tuple(dict.fromkeys(tags))


def unique_polymarket_markets_from_candidates(
    candidates: list[DiscoveryCandidate],
) -> list[PolymarketDiscoveryMarket]:
    return unique_polymarket_markets([candidate.polymarket_market for candidate in candidates])


def unique_polymarket_markets(
    markets: list[PolymarketDiscoveryMarket],
) -> list[PolymarketDiscoveryMarket]:
    unique: list[PolymarketDiscoveryMarket] = []
    seen: set[str] = set()
    for market in markets:
        if market.token_id in seen:
            continue
        seen.add(market.token_id)
        unique.append(market)
    return unique
