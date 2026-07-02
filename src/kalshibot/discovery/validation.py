from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import re

from kalshibot.client import KalshiClient
from kalshibot.discovery.models import (
    DateValidation,
    DiscoveryCandidate,
    KalshiDiscoveryMarket,
    NormalizedMarket,
    PolymarketDiscoveryMarket,
    PriceValidation,
    SideMapping,
    StructuralValidation,
)
from kalshibot.discovery.normalization import (
    normalized_deadline,
    normalize_kalshi_market,
    normalize_polymarket_market,
    side_mapping_for_normalized,
)
from kalshibot.discovery.taxonomy import (
    entity_terms,
    looks_like_month_or_time_term,
    market_domain,
    market_type,
    market_types_compatible,
    meaningful_numeric_tokens,
    proper_noun_overlap_is_too_weak,
    proper_noun_terms,
    requires_entity_overlap,
)
from kalshibot.polymarket import PolymarketClient
from kalshibot.spreads import parse_kalshi_top_of_book

NON_WINNER_OUTCOME_TERMS = {"draw", "tie", "event does not qualify"}
MATCHUP_PATTERN = re.compile(
    r"(?P<left>[^|?]+?)\s+vs\.?\s+(?P<right>[^|?]+)",
    re.IGNORECASE,
)
SUBGAME_SCOPE_PATTERN = re.compile(r"\b(?P<kind>map|set|game)\s*(?P<number>\d+)\b", re.IGNORECASE)
PERIOD_SCOPE_PATTERN = re.compile(
    r"\b(?P<number>1st|2nd|3rd|4th|first|second|third|fourth)\s+"
    r"(?P<kind>quarter|half|period|inning)\b",
    re.IGNORECASE,
)
EXACT_SCORE_SCOPE_PATTERN = re.compile(
    r"\b(?:set\s+score\s+of|score\s+of|wins?)\s+"
    r"(?P<score>\d+\s*[-\u2013]\s*\d+)\b",
    re.IGNORECASE,
)
KALSHI_CONTRACT_DATE_PATTERN = re.compile(
    r"(?<!\d)(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})",
    re.IGNORECASE,
)
POLYMARKET_CONTRACT_DATE_PATTERN = re.compile(r"(?<!\d)(20\d{2}-\d{2}-\d{2})(?!\d)")
KALSHI_MONTHS = {
    "JAN": "01",
    "FEB": "02",
    "MAR": "03",
    "APR": "04",
    "MAY": "05",
    "JUN": "06",
    "JUL": "07",
    "AUG": "08",
    "SEP": "09",
    "OCT": "10",
    "NOV": "11",
    "DEC": "12",
}
METADATA_DATE_ROLLOVER_DAYS = 1

__all__ = [
    "candidate_dates_match",
    "deterministic_candidate_match",
    "entity_terms",
    "game_winner_outcome_mismatch",
    "looks_like_month_or_time_term",
    "market_domain",
    "market_type",
    "market_types_compatible",
    "meaningful_numeric_tokens",
    "proper_noun_terms",
    "requires_entity_overlap",
    "safe_midpoint",
    "validate_candidate_prices",
    "validate_candidate_structure",
    "validate_candidate_dates",
]


def deterministic_candidate_match(
    candidate: DiscoveryCandidate,
) -> tuple[float, str, str] | None:
    structural = validate_candidate_structure(
        candidate.kalshi_market,
        candidate.polymarket_market,
    )
    if not structural.passed:
        return None
    if (
        structural.kalshi_market_type == "game_winner"
        and structural.polymarket_market_type == "game_winner"
        and len(structural.shared_proper_nouns) >= 2
        and game_winner_outcome_entities_overlap(candidate.kalshi_market, candidate.polymarket_market)
    ):
        return (
            0.92,
            "same game-winner market with shared matchup and outcome entities",
            "structural_game_winner",
        )
    return None


def game_winner_outcome_entities_overlap(
    kalshi_market: KalshiDiscoveryMarket,
    polymarket_market: PolymarketDiscoveryMarket,
) -> bool:
    if not kalshi_market.yes_sub_title:
        return False
    if is_non_winner_outcome(kalshi_market.yes_sub_title) or is_non_winner_outcome(
        polymarket_market.outcome
    ):
        return False
    kalshi_outcome_terms = proper_noun_terms(kalshi_market.yes_sub_title)
    polymarket_outcome_terms = proper_noun_terms(polymarket_market.outcome)
    return bool(kalshi_outcome_terms and polymarket_outcome_terms and kalshi_outcome_terms & polymarket_outcome_terms)


def is_non_winner_outcome(outcome: str) -> bool:
    lowered = outcome.strip().lower()
    return lowered in NON_WINNER_OUTCOME_TERMS


def validate_candidate_prices(
    candidate: DiscoveryCandidate,
    kalshi_client: KalshiClient,
    polymarket_client: PolymarketClient,
    *,
    threshold: Decimal,
) -> PriceValidation:
    try:
        kalshi_book = kalshi_client.get_market_orderbook(candidate.kalshi_market.ticker, depth=100)
        polymarket_book = polymarket_client.get_order_book(candidate.polymarket_market.token_id)
        kalshi_top = parse_kalshi_top_of_book(candidate.kalshi_market.ticker, kalshi_book)
        polymarket_top = polymarket_client.top_of_book_from_order_book(
            candidate.polymarket_market.token_id,
            polymarket_book,
        )
    except Exception as exc:
        return PriceValidation(
            passed=False,
            kalshi_mid=None,
            polymarket_mid=None,
            difference=None,
            reason=f"orderbook fetch/parse failed: {type(exc).__name__}: {exc}",
        )

    kalshi_mid = safe_midpoint(kalshi_top.yes_ask, kalshi_top.yes_bid)
    polymarket_mid = safe_midpoint(
        polymarket_top.best_ask.price if polymarket_top.best_ask else None,
        polymarket_top.best_bid.price if polymarket_top.best_bid else None,
    )
    if kalshi_mid is None or polymarket_mid is None:
        return PriceValidation(
            passed=False,
            kalshi_mid=kalshi_mid,
            polymarket_mid=polymarket_mid,
            difference=None,
            reason="missing usable bid/ask midpoint",
        )

    difference = abs(polymarket_mid - kalshi_mid)
    if difference > threshold:
        return PriceValidation(
            passed=False,
            kalshi_mid=kalshi_mid,
            polymarket_mid=polymarket_mid,
            difference=difference,
            reason=f"midpoint difference {difference} exceeds threshold {threshold}",
        )
    return PriceValidation(
        passed=True,
        kalshi_mid=kalshi_mid,
        polymarket_mid=polymarket_mid,
        difference=difference,
        reason="midpoint difference within threshold",
    )


def safe_midpoint(ask: Decimal | None, bid: Decimal | None) -> Decimal | None:
    return (ask + bid) / Decimal("2") if ask is not None and bid is not None else None


def validate_candidate_structure(
    kalshi_market: KalshiDiscoveryMarket,
    polymarket_market: PolymarketDiscoveryMarket,
) -> StructuralValidation:
    kalshi_normalized = normalize_kalshi_market(kalshi_market)
    polymarket_normalized = normalize_polymarket_market(polymarket_market)
    kalshi_type = market_type(kalshi_market.full_title)
    polymarket_type = market_type(polymarket_market.title)
    kalshi_domain = market_domain(kalshi_market.full_title, ticker=kalshi_market.ticker)
    polymarket_domain = market_domain(
        polymarket_market.title,
        tags=polymarket_market.tags,
    )
    kalshi_numbers = meaningful_numeric_tokens(kalshi_market.full_title)
    polymarket_numbers = meaningful_numeric_tokens(polymarket_market.title)
    shared_entities = tuple(
        sorted(entity_terms(kalshi_market.full_title) & entity_terms(polymarket_market.title))
    )
    kalshi_proper_nouns = proper_noun_terms(kalshi_market.full_title)
    polymarket_proper_nouns = proper_noun_terms(polymarket_market.title)
    shared_proper_nouns = tuple(sorted(kalshi_proper_nouns & polymarket_proper_nouns))
    reasons: list[str] = []

    if kalshi_domain != "unknown" and polymarket_domain != "unknown" and kalshi_domain != polymarket_domain:
        reasons.append(f"domain_mismatch:{kalshi_domain}!={polymarket_domain}")

    if kalshi_numbers and polymarket_numbers and not set(kalshi_numbers) <= set(polymarket_numbers):
        reasons.append("numeric_threshold_mismatch")

    if not market_types_compatible(kalshi_type, polymarket_type):
        reasons.append(f"market_type_mismatch:{kalshi_type}!={polymarket_type}")

    if requires_entity_overlap(kalshi_type, polymarket_type) and not shared_entities:
        reasons.append("missing_entity_overlap")

    if game_winner_outcome_mismatch(kalshi_market, polymarket_market):
        reasons.append("outcome_entity_mismatch")

    if game_winner_matchup_mismatch(kalshi_market, polymarket_market):
        reasons.append("matchup_entity_mismatch")

    if game_winner_scope_mismatch(kalshi_market, polymarket_market):
        reasons.append("match_scope_mismatch")

    date_validation = validate_candidate_dates(kalshi_market, polymarket_market)
    if not date_validation.passed:
        reasons.append(f"date_mismatch:{date_validation.reason}")

    if proper_noun_overlap_is_too_weak(
        kalshi_proper_nouns,
        polymarket_proper_nouns,
        shared_proper_nouns,
    ):
        reasons.append("weak_proper_noun_overlap")

    score, side_mapping, match_notes, blocking_issues = structured_match_score(
        kalshi_normalized,
        polymarket_normalized,
    )
    for issue in blocking_issues:
        if issue not in reasons:
            reasons.append(issue)
    match_status = "rejected" if reasons else "approved" if score >= 70 else "maybe"

    return StructuralValidation(
        passed=not reasons,
        kalshi_market_type=kalshi_type,
        polymarket_market_type=polymarket_type,
        kalshi_domain=kalshi_domain,
        polymarket_domain=polymarket_domain,
        kalshi_numbers=kalshi_numbers,
        polymarket_numbers=polymarket_numbers,
        shared_entities=shared_entities,
        shared_proper_nouns=shared_proper_nouns,
        reasons=tuple(reasons),
        score=score,
        side_mapping=side_mapping,
        match_status=match_status,
        match_notes=tuple(match_notes),
        blocking_issues=tuple(blocking_issues),
        kalshi_normalized=kalshi_normalized,
        polymarket_normalized=polymarket_normalized,
    )


def structured_match_score(
    kalshi_market: NormalizedMarket,
    polymarket_market: NormalizedMarket,
) -> tuple[int, SideMapping, list[str], list[str]]:
    score = 0
    notes: list[str] = []
    blockers: list[str] = []
    side_mapping = side_mapping_for_normalized(kalshi_market, polymarket_market)

    if kalshi_market.event_type and kalshi_market.event_type == polymarket_market.event_type:
        score += 15
        notes.append("same event type")
    elif kalshi_market.event_type and polymarket_market.event_type:
        blockers.append(f"event_type_mismatch:{kalshi_market.event_type}!={polymarket_market.event_type}")

    kalshi_entities = set(kalshi_market.entities)
    polymarket_entities = set(polymarket_market.entities)
    if kalshi_entities and polymarket_entities and kalshi_entities & polymarket_entities:
        score += 20
        notes.append("overlapping key entities")
    elif entities_required_for_structured_match(kalshi_market, polymarket_market):
        blockers.append("entity_mismatch")

    if thresholds_match(kalshi_market.threshold, polymarket_market.threshold):
        if kalshi_market.threshold is not None:
            score += 20
            notes.append("same threshold")
    elif kalshi_market.threshold is not None and polymarket_market.threshold is not None:
        blockers.append("threshold_mismatch")

    if comparators_match(kalshi_market.comparator, polymarket_market.comparator):
        if kalshi_market.comparator is not None:
            score += 10
            notes.append("same comparator")
    elif kalshi_market.comparator is not None and polymarket_market.comparator is not None:
        blockers.append("comparator_mismatch")

    kalshi_deadline = normalized_deadline(kalshi_market)
    polymarket_deadline = normalized_deadline(polymarket_market)
    if kalshi_deadline and polymarket_deadline and deadlines_compatible(kalshi_deadline, polymarket_deadline):
        score += 15
        notes.append("compatible deadline")

    if resolution_sources_compatible(kalshi_market.resolution_source, polymarket_market.resolution_source):
        if kalshi_market.resolution_source and polymarket_market.resolution_source:
            score += 10
            notes.append("compatible resolution source")
    else:
        blockers.append("resolution_source_mismatch")

    if side_mapping in {"same", "inverse"}:
        score += 10
        notes.append(f"{side_mapping} side mapping")
    elif side_mapping_required(kalshi_market, polymarket_market):
        blockers.append("side_mapping_unclear")

    if settlement_timing_mismatch(kalshi_market, polymarket_market):
        blockers.append("settlement_timing_mismatch")

    return score, side_mapping, notes, blockers


def entities_required_for_structured_match(
    kalshi_market: NormalizedMarket,
    polymarket_market: NormalizedMarket,
) -> bool:
    return bool(
        kalshi_market.event_type in {"crypto_threshold", "game_winner"}
        or polymarket_market.event_type in {"crypto_threshold", "game_winner"}
    )


def thresholds_match(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return False
    return abs(left - right) < 0.000001


def comparators_match(left: str | None, right: str | None) -> bool:
    if left is None or right is None:
        return False
    return left == right


def deadline_is_hard_requirement(
    kalshi_market: NormalizedMarket,
    polymarket_market: NormalizedMarket,
) -> bool:
    return bool(
        kalshi_market.event_type in {"crypto_threshold", "game_winner"}
        or polymarket_market.event_type in {"crypto_threshold", "game_winner"}
    )


def deadlines_compatible(left: str, right: str) -> bool:
    return date_difference_days(left, right) <= METADATA_DATE_ROLLOVER_DAYS


def resolution_sources_compatible(left: str | None, right: str | None) -> bool:
    return left is None or right is None or left == right


def side_mapping_required(
    kalshi_market: NormalizedMarket,
    polymarket_market: NormalizedMarket,
) -> bool:
    return bool(
        kalshi_market.event_type in {"crypto_threshold", "game_winner"}
        or polymarket_market.event_type in {"crypto_threshold", "game_winner"}
    )


def settlement_timing_mismatch(
    kalshi_market: NormalizedMarket,
    polymarket_market: NormalizedMarket,
) -> bool:
    return bool(
        kalshi_market.settlement_timing
        and polymarket_market.settlement_timing
        and kalshi_market.settlement_timing != polymarket_market.settlement_timing
    )


def validate_candidate_dates(
    kalshi_market: KalshiDiscoveryMarket,
    polymarket_market: PolymarketDiscoveryMarket,
) -> DateValidation:
    if market_type(kalshi_market.full_title) not in {"crypto_threshold", "game_winner"}:
        return DateValidation(True, None, None, None, "date check not required")
    if market_type(polymarket_market.title) not in {"crypto_threshold", "game_winner"}:
        return DateValidation(True, None, None, None, "date check not required")

    kalshi_contract_date = kalshi_encoded_contract_date(kalshi_market)
    polymarket_contract_date = polymarket_encoded_contract_date(polymarket_market)
    if kalshi_contract_date is not None and polymarket_contract_date is not None:
        difference_days = date_difference_days(kalshi_contract_date, polymarket_contract_date)
        if difference_days != 0:
            return DateValidation(
                False,
                kalshi_contract_date,
                polymarket_contract_date,
                difference_days,
                f"contract dates differ {kalshi_contract_date}!={polymarket_contract_date}",
            )
        return DateValidation(
            True,
            kalshi_contract_date,
            polymarket_contract_date,
            difference_days,
            "contract dates aligned",
        )

    kalshi_date = kalshi_contract_date or discovery_date_from_values(
        kalshi_market.expected_expiration_time,
        kalshi_market.expiration_time,
        kalshi_market.close_time,
    )
    polymarket_date = polymarket_contract_date or discovery_date_from_values(
        polymarket_market.end_date,
        polymarket_market.start_date,
    )
    if kalshi_date is None or polymarket_date is None:
        return DateValidation(
            True,
            kalshi_date,
            polymarket_date,
            None,
            "missing date metadata",
        )

    difference_days = date_difference_days(kalshi_date, polymarket_date)
    if difference_days > METADATA_DATE_ROLLOVER_DAYS:
        return DateValidation(
            False,
            kalshi_date,
            polymarket_date,
            difference_days,
            f"metadata dates differ {kalshi_date}!={polymarket_date}",
        )
    return DateValidation(
        True,
        kalshi_date,
        polymarket_date,
        difference_days,
        "metadata dates aligned within UTC rollover",
    )


def candidate_dates_match(
    kalshi_market: KalshiDiscoveryMarket,
    polymarket_market: PolymarketDiscoveryMarket,
) -> bool:
    return validate_candidate_dates(kalshi_market, polymarket_market).passed


def discovery_date_from_values(*values: str | None) -> str | None:
    for value in values:
        parsed = parse_datetime(value)
        if parsed is not None:
            return parsed.date().isoformat()
    return None


def kalshi_encoded_contract_date(market: KalshiDiscoveryMarket) -> str | None:
    text = " ".join(
        value
        for value in (
            market.ticker,
            market.event_ticker,
        )
        if value
    )
    match = KALSHI_CONTRACT_DATE_PATTERN.search(text)
    if match is None:
        return None
    year_prefix, month_code, day = match.groups()
    return f"20{year_prefix}-{KALSHI_MONTHS[month_code.upper()]}-{day}"


def polymarket_encoded_contract_date(market: PolymarketDiscoveryMarket) -> str | None:
    text = " ".join(
        value
        for value in (
            market.slug,
            market.event_title,
            market.market_question,
            market.title,
        )
        if value
    )
    match = POLYMARKET_CONTRACT_DATE_PATTERN.search(text)
    return match.group(1) if match is not None else None


def date_difference_days(left: str, right: str) -> int:
    return abs((date.fromisoformat(left) - date.fromisoformat(right)).days)


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def game_winner_outcome_mismatch(
    kalshi_market: KalshiDiscoveryMarket,
    polymarket_market: PolymarketDiscoveryMarket,
) -> bool:
    if market_type(kalshi_market.full_title) != "game_winner":
        return False
    if market_type(polymarket_market.title) != "game_winner":
        return False
    if not kalshi_market.yes_sub_title:
        return False
    kalshi_outcome_terms = proper_noun_terms(kalshi_market.yes_sub_title)
    polymarket_outcome_terms = proper_noun_terms(polymarket_market.outcome)
    if not kalshi_outcome_terms or not polymarket_outcome_terms:
        return False
    return not bool(kalshi_outcome_terms & polymarket_outcome_terms)


def game_winner_matchup_mismatch(
    kalshi_market: KalshiDiscoveryMarket,
    polymarket_market: PolymarketDiscoveryMarket,
) -> bool:
    if market_type(kalshi_market.full_title) != "game_winner":
        return False
    if market_type(polymarket_market.title) != "game_winner":
        return False
    kalshi_sides = matchup_sides_from_text(kalshi_market.title) or matchup_sides_from_text(
        kalshi_market.full_title
    )
    polymarket_sides = matchup_sides_from_text(
        polymarket_market.market_question
    ) or matchup_sides_from_text(polymarket_market.event_title)
    if kalshi_sides is None or polymarket_sides is None:
        return False
    return not matchup_sides_compatible(kalshi_sides, polymarket_sides)


def game_winner_scope_mismatch(
    kalshi_market: KalshiDiscoveryMarket,
    polymarket_market: PolymarketDiscoveryMarket,
) -> bool:
    if market_type(kalshi_market.full_title) != "game_winner":
        return False
    if market_type(polymarket_market.title) != "game_winner":
        return False
    kalshi_scope = game_winner_scope(kalshi_market.full_title)
    polymarket_scope = game_winner_scope(polymarket_market.title)
    return not game_winner_scopes_compatible(kalshi_scope, polymarket_scope)


def matchup_sides_from_text(text: str | None) -> tuple[set[str], set[str]] | None:
    if not text:
        return None
    match = MATCHUP_PATTERN.search(text)
    if match is None:
        return None
    left = clean_matchup_side(match.group("left"), is_left=True)
    right = clean_matchup_side(match.group("right"), is_left=False)
    left_terms = proper_noun_terms(left)
    right_terms = proper_noun_terms(right)
    if not left_terms or not right_terms:
        return None
    return left_terms, right_terms


def clean_matchup_side(value: str, *, is_left: bool) -> str:
    cleaned = value.strip()
    cleaned = re.sub(r"^.*\bin\s+the\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^.*\bbetween\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^.*\bwin\s+(?:the\s+)?", "", cleaned, flags=re.IGNORECASE)
    if is_left and ":" in cleaned:
        cleaned = cleaned.rsplit(":", 1)[-1]
    else:
        cleaned = re.split(r"\s+\|\s+|\s+-\s+|\(|\?|:", cleaned)[0]
    cleaned = re.sub(
        r"\b(?:match|game|winner|final|semifinal|quarterfinal)\b.*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned.strip()


def matchup_sides_compatible(
    left: tuple[set[str], set[str]],
    right: tuple[set[str], set[str]],
) -> bool:
    return (
        participant_terms_compatible(left[0], right[0])
        and participant_terms_compatible(left[1], right[1])
    ) or (
        participant_terms_compatible(left[0], right[1])
        and participant_terms_compatible(left[1], right[0])
    )


def participant_terms_compatible(left: set[str], right: set[str]) -> bool:
    shared = left & right
    if not shared:
        return False
    return not (left - shared and right - shared)


def game_winner_scope(text: str | None) -> tuple[str, str | None]:
    if not text:
        return ("full", None)
    if match := EXACT_SCORE_SCOPE_PATTERN.search(text):
        return ("exact_score", re.sub(r"\s+", "", match.group("score")).replace("\u2013", "-"))
    if match := SUBGAME_SCOPE_PATTERN.search(text):
        kind = normalized_subgame_kind(match.group("kind"))
        return (kind, match.group("number"))
    if match := PERIOD_SCOPE_PATTERN.search(text):
        return (match.group("kind").lower(), ordinal_to_number(match.group("number")))
    return ("full", None)


def normalized_subgame_kind(kind: str) -> str:
    lowered = kind.lower()
    if lowered == "game":
        return "map"
    return lowered


def ordinal_to_number(value: str) -> str:
    return {
        "1st": "1",
        "2nd": "2",
        "3rd": "3",
        "4th": "4",
        "first": "1",
        "second": "2",
        "third": "3",
        "fourth": "4",
    }[value.lower()]


def game_winner_scopes_compatible(
    left: tuple[str, str | None],
    right: tuple[str, str | None],
) -> bool:
    if left == right:
        return True
    if left[0] == "full" or right[0] == "full":
        return False
    return left[0] == right[0] and left[1] == right[1]
