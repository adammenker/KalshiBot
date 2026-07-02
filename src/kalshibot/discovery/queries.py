from __future__ import annotations

import re

from kalshibot.discovery.models import KalshiDiscoveryMarket, NormalizedMarket
from kalshibot.discovery.normalization import normalize_kalshi_market, parse_text_date


def kalshi_polymarket_search_queries(market: KalshiDiscoveryMarket) -> list[str]:
    normalized = normalize_kalshi_market(market)
    queries = canonical_queries(normalized)
    queries.append(market.full_title)
    return unique_queries(queries)


def canonical_queries(market: NormalizedMarket) -> list[str]:
    if market.event_type == "crypto_threshold":
        return crypto_threshold_queries(market)
    if market.event_type == "game_winner":
        return game_winner_queries(market)
    return generic_queries(market)


def crypto_threshold_queries(market: NormalizedMarket) -> list[str]:
    asset = market.entities[0] if market.entities else None
    threshold = format_threshold(market.threshold)
    comparator = market.comparator
    date = market_date_text(market)
    queries = []
    if asset and threshold and date:
        queries.append(f"{asset} {threshold} {date}")
    if asset and comparator and threshold:
        queries.append(f"{asset} {comparator} {threshold}")
    if asset and threshold:
        queries.append(f"{asset} {threshold}")
    if asset and date:
        queries.append(f"{asset} {date}")
    if asset:
        queries.append(asset)
    return queries


def game_winner_queries(market: NormalizedMarket) -> list[str]:
    queries = []
    matchup = matchup_query_from_text(" ".join(part for part in (market.title, market.question) if part))
    if matchup:
        queries.append(matchup)
        queries.append(f"{matchup} winner")
        date = market_date_text(market)
        if date:
            queries.append(f"{matchup} {date}")
    if market.yes_condition_text:
        queries.append(market.yes_condition_text)
    return queries


def matchup_query_from_text(text: str) -> str | None:
    cleaned = re.sub(r"\bwill\s+.+?\s+win\s+(?:the\s+)?", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:winner|match|game)\??\b", "", cleaned, flags=re.IGNORECASE)
    if ":" in cleaned:
        cleaned = cleaned.split(":")[-1]
    match = re.search(r"([A-Za-z0-9 .'-]+?)\s+vs\.?\s+([A-Za-z0-9 .'-]+)", cleaned, flags=re.IGNORECASE)
    if match is None:
        return None
    left, right = match.groups()
    return " ".join(f"{left} {right}".replace("|", " ").split())


def generic_queries(market: NormalizedMarket) -> list[str]:
    terms = list(market.entities)
    queries = []
    if terms:
        queries.append(" ".join(terms[:4]))
    if market.threshold is not None:
        threshold = format_threshold(market.threshold)
        if terms and threshold:
            queries.append(f"{' '.join(terms[:2])} {threshold}")
    if market.title:
        queries.append(market.title)
    return queries


def market_date_text(market: NormalizedMarket) -> str | None:
    for value in (market.resolution_time, market.expiration_time, market.close_time, market.start_time):
        if value:
            parsed = parse_text_date(value)
            if parsed:
                return parsed
            return value[:10]
    return parse_text_date(" ".join(part for part in (market.title, market.question) if part))


def format_threshold(value: float | None) -> str | None:
    if value is None:
        return None
    if value >= 1000 and value % 1000 == 0:
        return f"{int(value / 1000)}k"
    if value.is_integer():
        return str(int(value))
    return str(value)


def unique_queries(queries: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for query in queries:
        cleaned = " ".join(str(query).split())
        key = cleaned.lower()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        unique.append(cleaned)
    return unique
