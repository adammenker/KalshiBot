from __future__ import annotations

import re

from kalshibot.market_matcher import important_terms, numeric_tokens

GENERIC_ENTITY_TERMS = {
    "champion",
    "championship",
    "extra",
    "extras",
    "inning",
    "innings",
    "league",
    "mlb",
    "more",
    "originally",
    "regular",
    "run",
    "runs",
    "scheduled",
    "season",
    "series",
    "there",
    "total",
}

PROPER_NOUN_STOP_TERMS = {
    "candidate",
    "champion",
    "congressional",
    "cup",
    "democratic",
    "district",
    "election",
    "fifa",
    "game",
    "governor",
    "heavyweight",
    "match",
    "nominee",
    "open",
    "primary",
    "republican",
    "round",
    "win",
    "winner",
    "world",
}

SPORTS_STAT_PATTERN = (
    r"\b(?:assists?|goals?|points?|rebounds?|shots?|hits?|rbis?|"
    r"strikeouts?|touchdowns?|yards?)\b"
)


def market_domain(
    title: str,
    *,
    ticker: str | None = None,
    tags: tuple[str, ...] = (),
) -> str:
    tag_text = " ".join(tags).lower()
    lowered = f"{ticker or ''} {title} {tag_text}".lower()
    if any(term in lowered for term in ("politic", "election", "primary", "senate", "governor", "trump")):
        return "politics"
    if any(
        term in lowered
        for term in (
            "sports",
            "sport",
            "football",
            "soccer",
            "world cup",
            "mlb",
            "tennis",
            "ufc",
            "nba",
            "nfl",
            "nhl",
            "match",
            " vs ",
            "winner?",
            "goals",
            "assists",
        )
    ):
        return "sports"
    if any(term in lowered for term in ("crypto", "bitcoin", "ethereum", "solana")):
        return "crypto"
    if any(term in lowered for term in ("fed", "inflation", "recession", "cpi", "gdp")):
        return "economics"
    return "unknown"


def market_type(title: str) -> str:
    lowered = title.lower()
    if is_mention_market(lowered):
        return "mention_market"
    if is_crypto_threshold_market(lowered):
        return "crypto_threshold"
    if "extra inning" in lowered or "extra innings" in lowered:
        return "extra_innings"
    if "first inning" in lowered:
        return "first_inning"
    if "1st 5 innings" in lowered:
        return "first_five_innings"
    if is_win_total_market(lowered):
        return "season_win_total"
    if "total runs" in lowered or "runs scored" in lowered or "o/u" in lowered:
        return "game_total"
    if re.search(r"wins? by over \d", lowered) or "more games than" in lowered or "spread:" in lowered:
        return "game_spread"
    if "score or assist" in lowered or "player to score" in lowered:
        return "player_prop"
    if re.search(SPORTS_STAT_PATTERN, lowered) and (
        meaningful_numeric_tokens(title)
        or re.search(r"\b(?:over|under|at least)\b", lowered)
        or re.search(r"\b(?:record|have|lead|finish with|most)\b", lowered)
    ):
        return "player_prop"
    if is_award_or_futures_winner_market(lowered):
        return "award_or_futures_winner"
    if is_game_winner_market(lowered):
        return "game_winner"
    return "unknown"


def is_award_or_futures_winner_market(lowered_title: str) -> bool:
    election_or_futures_terms = (
        "election",
        "primary",
        "nominee",
        "president",
        "governor",
        "senate",
        "mayor",
        "champion",
        "championship",
        "cup winner",
        "tournament winner",
    )
    return bool(
        (
            " win " in f" {lowered_title} "
            or "winner" in lowered_title
            or "elected" in lowered_title
        )
        and any(term in lowered_title for term in election_or_futures_terms)
    )


def is_mention_market(lowered_title: str) -> bool:
    return bool(
        ("announcer" in lowered_title and "say" in lowered_title)
        or "mention" in lowered_title
        or re.search(r"\bwhat will .+\bsay\b", lowered_title)
    )


def is_game_winner_market(lowered_title: str) -> bool:
    has_matchup = bool(
        re.search(r"\bvs\.?\b", lowered_title)
        or " match" in lowered_title
        or " game" in lowered_title
    )
    if "winner" in lowered_title and has_matchup:
        return True
    if re.search(r"\bwill .+\bwin\b", lowered_title) and has_matchup:
        return True
    if re.search(r"\b(?:beats?|defeats?)\b", lowered_title):
        return True
    return bool(re.search(r"\bvs\.?\b", lowered_title) and is_bare_matchup_title(lowered_title))


def is_bare_matchup_title(lowered_title: str) -> bool:
    if not re.search(r"\bvs\.?\b", lowered_title):
        return False
    disqualifiers = (
        "what will",
        "will there",
        "how many",
        "say",
        "mention",
        "announcer",
        "score",
        "assist",
        "total",
        "over",
        "under",
        "spread",
        "points",
        "runs",
        "goals",
    )
    return not any(term in lowered_title for term in disqualifiers)


def is_crypto_threshold_market(lowered_title: str) -> bool:
    crypto_terms = ("bitcoin", "btc", "ethereum", "eth", "solana", "sol")
    comparator_terms = (
        "above",
        "below",
        "over",
        "under",
        "higher than",
        "lower than",
        "greater than",
        "less than",
        "hit",
        "hits",
        "reach",
        "reaches",
    )
    return bool(
        any(term in lowered_title for term in crypto_terms)
        and any(term in lowered_title for term in comparator_terms)
        and (
            "$" in lowered_title
            or re.search(r"\b\d{2,3}(?:,\d{3})+\b", lowered_title)
            or re.search(r"\b\d+(?:\.\d+)?k\b", lowered_title)
        )
    )


def is_win_total_market(lowered_title: str) -> bool:
    return bool(
        "win total" in lowered_title
        or "total wins" in lowered_title
        or re.search(r"\bwins?\s+(?:more|less|fewer)\s+than\b", lowered_title)
        or re.search(r"\bwins?\s+at\s+least\b", lowered_title)
        or re.search(r"\bwins?\s+(?:over|under)\b", lowered_title)
        or re.search(r"\bwins?\s+\d+(?:\.\d+)?\+?\s+(?:games?|matches?)\b", lowered_title)
    )


def market_types_compatible(kalshi_type: str, polymarket_type: str) -> bool:
    if kalshi_type == "unknown" and polymarket_type == "unknown":
        return True
    return kalshi_type == polymarket_type


def requires_entity_overlap(kalshi_type: str, polymarket_type: str) -> bool:
    return kalshi_type != "unknown" or polymarket_type != "unknown"


def meaningful_numeric_tokens(title: str) -> tuple[str, ...]:
    lowered = title.lower()
    values: list[str] = []
    for value in numeric_tokens(title):
        if re.search(rf"\b{re.escape(value)}\s*(?:am|pm|edt|est|utc|pt|et)\b", lowered):
            continue
        if len(value) == 4 and value.startswith("20"):
            continue
        values.append(value)
    return tuple(values)


def entity_terms(title: str) -> set[str]:
    return {
        term
        for term in important_terms(title)
        if term not in GENERIC_ENTITY_TERMS and not looks_like_month_or_time_term(term)
    }


def proper_noun_terms(title: str) -> set[str]:
    terms = entity_terms(title)
    return {
        term
        for term in terms
        if term not in PROPER_NOUN_STOP_TERMS
        and not re.fullmatch(r"\d+(?:\.\d+)?", term)
        and len(term) > 2
    }


def proper_noun_overlap_is_too_weak(
    kalshi_terms: set[str],
    polymarket_terms: set[str],
    shared_terms: tuple[str, ...],
) -> bool:
    if len(kalshi_terms) < 2 or len(polymarket_terms) < 2:
        return False
    if len(shared_terms) >= 2:
        return False
    return True


def looks_like_month_or_time_term(term: str) -> bool:
    return term in {
        "jan",
        "feb",
        "mar",
        "apr",
        "may",
        "jun",
        "jul",
        "aug",
        "sep",
        "oct",
        "nov",
        "dec",
        "edt",
        "est",
        "utc",
    }
