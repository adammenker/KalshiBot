from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from kalshibot.discovery.models import (
    KalshiDiscoveryMarket,
    NormalizedMarket,
    PolymarketDiscoveryMarket,
    SideMapping,
)
from kalshibot.discovery.taxonomy import (
    entity_terms,
    market_domain,
    market_type,
    proper_noun_terms,
)

CRYPTO_ASSETS = {
    "bitcoin": ("bitcoin", "btc"),
    "ethereum": ("ethereum", "eth"),
    "solana": ("solana", "sol"),
}
MONTH_PATTERN = re.compile(
    r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?)\s+(\d{1,2})\b",
    re.IGNORECASE,
)
MONTHS = {
    "jan": "01",
    "january": "01",
    "feb": "02",
    "february": "02",
    "mar": "03",
    "march": "03",
    "apr": "04",
    "april": "04",
    "may": "05",
    "jun": "06",
    "june": "06",
    "jul": "07",
    "july": "07",
    "aug": "08",
    "august": "08",
    "sep": "09",
    "september": "09",
    "oct": "10",
    "october": "10",
    "nov": "11",
    "november": "11",
    "dec": "12",
    "december": "12",
}
ISO_DATE_PATTERN = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
THRESHOLD_PATTERN = re.compile(
    r"(?:\$|usd\s*)?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?\s*k|\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
THRESHOLD_EVENT_TYPES = {
    "crypto_threshold",
    "game_total",
    "game_spread",
    "player_prop",
    "season_win_total",
    "first_five_innings",
}


def normalize_kalshi_market(market: KalshiDiscoveryMarket) -> NormalizedMarket:
    text = " ".join(
        part
        for part in (
            market.full_title,
            market.description,
            market.rules_text,
        )
        if part
    )
    event_type = market_type(market.full_title)
    metric_text = " ".join(
        part
        for part in (
            market.full_title,
            market.title,
            market.yes_sub_title,
        )
        if part
    )
    return NormalizedMarket(
        venue="kalshi",
        ticker_or_token_id=market.ticker,
        title=market.title,
        subtitle=market.yes_sub_title,
        question=market.title,
        description=market.description,
        rules_text=market.rules_text,
        category=market_domain(market.full_title, ticker=market.ticker),
        tags=(),
        event_type=event_type,
        entities=normalized_entities(text, event_type=event_type),
        target_metric=target_metric_for_text(text, event_type),
        comparator=extract_comparator(metric_text, event_type=event_type),
        threshold=extract_threshold(metric_text, event_type=event_type),
        unit=extract_unit(text),
        start_time=None,
        close_time=market.close_time,
        expiration_time=market.expiration_time or market.expected_expiration_time,
        resolution_time=market.expected_expiration_time or market.expiration_time or market.close_time,
        resolution_source=extract_resolution_source(text),
        yes_condition_text=market.yes_sub_title,
        no_condition_text=market.no_sub_title,
        settlement_timing=extract_settlement_timing(text),
    )


def normalize_polymarket_market(market: PolymarketDiscoveryMarket) -> NormalizedMarket:
    text = " ".join(
        part
        for part in (
            market.title,
            market.description,
            market.rules_text,
        )
        if part
    )
    event_type = market_type(market.title)
    metric_text = " ".join(
        part
        for part in (
            market.title,
            market.event_title,
            market.market_question,
            market.outcome,
        )
        if part
    )
    return NormalizedMarket(
        venue="polymarket",
        ticker_or_token_id=market.token_id,
        title=market.event_title,
        subtitle=market.outcome,
        question=market.market_question,
        description=market.description,
        rules_text=market.rules_text,
        category=market_domain(market.title, tags=market.tags),
        tags=market.tags,
        event_type=event_type,
        entities=normalized_entities(text, event_type=event_type),
        target_metric=target_metric_for_text(text, event_type),
        comparator=extract_comparator(metric_text, event_type=event_type),
        threshold=extract_threshold(metric_text, event_type=event_type),
        unit=extract_unit(text),
        start_time=market.start_date,
        close_time=None,
        expiration_time=market.end_date,
        resolution_time=market.end_date or market.start_date,
        resolution_source=extract_resolution_source(text),
        yes_condition_text=market.outcome,
        no_condition_text=sibling_outcome(market),
        settlement_timing=extract_settlement_timing(text),
    )


def normalized_entities(text: str, *, event_type: str | None) -> tuple[str, ...]:
    crypto = crypto_entities(text)
    if crypto:
        return crypto
    terms = proper_noun_terms(text) if event_type == "game_winner" else entity_terms(text)
    return tuple(sorted(terms))


def crypto_entities(text: str) -> tuple[str, ...]:
    lowered = text.lower()
    found = [
        asset
        for asset, aliases in CRYPTO_ASSETS.items()
        if any(re.search(rf"\b{re.escape(alias)}\b", lowered) for alias in aliases)
    ]
    return tuple(found)


def target_metric_for_text(text: str, event_type: str | None) -> str | None:
    if event_type == "crypto_threshold" or crypto_entities(text):
        return "price"
    return None


def extract_comparator(text: str, *, event_type: str | None = None) -> str | None:
    if event_type not in THRESHOLD_EVENT_TYPES:
        return None
    lowered = text.lower()
    if re.search(r"\b(?:hit|hits|reach|reaches|touch|touches)\b", lowered):
        return "hit"
    if re.search(r"\b(?:above|over|higher than|greater than|exceed|exceeds)\b", lowered):
        return "above"
    if re.search(r"\b(?:below|under|lower than|less than)\b", lowered):
        return "below"
    return None


def extract_threshold(text: str, *, event_type: str | None = None) -> float | None:
    if event_type not in THRESHOLD_EVENT_TYPES:
        return None
    candidates: list[Decimal] = []
    for match in THRESHOLD_PATTERN.finditer(text):
        parsed = parse_threshold_number(match.group(1))
        if parsed is not None and not looks_like_year(parsed):
            candidates.append(parsed)
    if not candidates:
        return None
    # Crypto thresholds are usually the largest value; sports thresholds are usually the final line.
    chosen = max(candidates) if event_type == "crypto_threshold" else candidates[-1]
    return float(chosen)


def looks_like_year(value: Decimal) -> bool:
    return value == value.to_integral_value() and Decimal("1900") <= value <= Decimal("2099")


def parse_threshold_number(value: str) -> Decimal | None:
    normalized = value.lower().replace(",", "").replace(" ", "")
    multiplier = Decimal("1000") if normalized.endswith("k") else Decimal("1")
    if normalized.endswith("k"):
        normalized = normalized[:-1]
    try:
        return Decimal(normalized) * multiplier
    except InvalidOperation:
        return None


def extract_unit(text: str) -> str | None:
    lowered = text.lower()
    if "$" in text or "usd" in lowered or crypto_entities(text):
        return "usd"
    return None


def extract_resolution_source(text: str) -> str | None:
    lowered = text.lower()
    for source in ("coinbase", "coindesk", "binance", "kraken", "pce", "cpi", "federal reserve"):
        if source in lowered:
            return source
    return None


def extract_settlement_timing(text: str) -> str | None:
    lowered = text.lower()
    if re.search(r"\b(?:hit|hits|reach|reaches|touch|touches)\b", lowered):
        return "any_time"
    if re.search(r"\b(?:before|by)\b", lowered) and re.search(r"\b(?:hit|reach|touch)\b", lowered):
        return "any_time"
    if re.search(r"\b(?:at|on|as of|end of|close of)\b", lowered):
        return "at_deadline"
    return None


def normalized_deadline(market: NormalizedMarket) -> str | None:
    for value in (market.resolution_time, market.expiration_time, market.close_time, market.start_time):
        parsed = parse_datetime_date(value)
        if parsed is not None:
            return parsed
    text_date = parse_text_date(" ".join(part for part in (market.title, market.question) if part))
    return text_date


def parse_datetime_date(value: str | None) -> str | None:
    if not value:
        return None
    if match := ISO_DATE_PATTERN.search(value):
        return match.group(1)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def parse_text_date(text: str) -> str | None:
    if match := ISO_DATE_PATTERN.search(text):
        return match.group(1)
    match = MONTH_PATTERN.search(text)
    if match is None:
        return None
    month_text, day_text = match.groups()
    year_match = re.search(r"\b(20\d{2})\b", text)
    year = year_match.group(1) if year_match else str(datetime.now().year)
    return f"{year}-{MONTHS[month_text.lower()]}-{int(day_text):02d}"


def side_mapping_for_normalized(
    kalshi_market: NormalizedMarket,
    polymarket_market: NormalizedMarket,
) -> SideMapping:
    kalshi_yes = set(normalized_side_terms(kalshi_market.yes_condition_text or kalshi_market.title))
    kalshi_no = set(normalized_side_terms(kalshi_market.no_condition_text or ""))
    poly_yes = set(normalized_side_terms(polymarket_market.yes_condition_text or polymarket_market.title))
    if not kalshi_yes or not poly_yes:
        return "unknown"
    if kalshi_yes & poly_yes:
        return "same"
    if kalshi_no and kalshi_no & poly_yes:
        return "inverse"
    if kalshi_market.event_type == "crypto_threshold" and polymarket_market.event_type == "crypto_threshold":
        if kalshi_market.comparator and kalshi_market.comparator == polymarket_market.comparator:
            return "same"
    return "unknown"


def normalized_side_terms(text: str) -> tuple[str, ...]:
    lowered = text.strip().lower()
    if lowered in {"yes", "y"}:
        return ("yes",)
    if lowered in {"no", "n"}:
        return ("no",)
    if lowered in {"draw", "tie"}:
        return ("draw",)
    crypto = crypto_entities(text)
    if crypto:
        return crypto
    return tuple(sorted(proper_noun_terms(text) or entity_terms(text)))


def sibling_outcome(market: PolymarketDiscoveryMarket) -> str | None:
    for outcome, token_id in market.outcome_token_ids:
        if token_id != market.token_id:
            return outcome
    return None


def normalized_market_dict(market: NormalizedMarket | None) -> dict[str, Any] | None:
    return asdict(market) if market is not None else None
