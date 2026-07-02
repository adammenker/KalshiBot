from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DiscoveryProfile:
    kalshi_market_types: str | None
    max_polymarket_contracts_per_event: int | None
    polymarket_outcome_filter: str
    max_match_days: int | None = None


@dataclass(frozen=True)
class ResolvedDiscoveryProfile:
    kalshi_market_types: set[str]
    max_polymarket_contracts_per_event: int | None
    polymarket_outcome_filter: str
    max_match_days: int | None


DISCOVERY_PROFILE_DEFAULTS = {
    "general": DiscoveryProfile(
        kalshi_market_types=None,
        max_polymarket_contracts_per_event=40,
        polymarket_outcome_filter="any",
    ),
    "crypto-threshold": DiscoveryProfile(
        kalshi_market_types="crypto_threshold",
        max_polymarket_contracts_per_event=80,
        polymarket_outcome_filter="any",
        max_match_days=60,
    ),
    "sports-game-winner": DiscoveryProfile(
        kalshi_market_types="game_winner",
        max_polymarket_contracts_per_event=None,
        polymarket_outcome_filter="any",
        max_match_days=14,
    ),
    "event-winner": DiscoveryProfile(
        kalshi_market_types="award_or_futures_winner,game_winner",
        max_polymarket_contracts_per_event=None,
        polymarket_outcome_filter="any",
    ),
    "economic-release": DiscoveryProfile(
        kalshi_market_types="unknown",
        max_polymarket_contracts_per_event=40,
        polymarket_outcome_filter="yes-no",
        max_match_days=90,
    ),
    "win-lose": DiscoveryProfile(
        kalshi_market_types="award_or_futures_winner,game_winner",
        max_polymarket_contracts_per_event=None,
        polymarket_outcome_filter="any",
    ),
    "unnested": DiscoveryProfile(
        kalshi_market_types="unknown,award_or_futures_winner",
        max_polymarket_contracts_per_event=2,
        polymarket_outcome_filter="yes-no",
    ),
    "simple-binary": DiscoveryProfile(
        kalshi_market_types="unknown,award_or_futures_winner",
        max_polymarket_contracts_per_event=2,
        polymarket_outcome_filter="yes-no",
    ),
}


def parse_market_type_set(value: str | None) -> set[str]:
    if not value:
        return set()
    return {part.strip().lower() for part in value.split(",") if part.strip()}


def resolve_discovery_profile(
    *,
    market_profile: str,
    max_polymarket_contracts_per_event: int | None,
    polymarket_outcome_filter: str | None,
    kalshi_market_types: str | None,
) -> ResolvedDiscoveryProfile:
    if market_profile not in DISCOVERY_PROFILE_DEFAULTS:
        allowed = ", ".join(sorted(DISCOVERY_PROFILE_DEFAULTS))
        raise ValueError(f"--market-profile must be one of: {allowed}")
    defaults = DISCOVERY_PROFILE_DEFAULTS[market_profile]
    resolved_market_types = (
        defaults.kalshi_market_types if kalshi_market_types is None else kalshi_market_types
    )
    return ResolvedDiscoveryProfile(
        kalshi_market_types=parse_market_type_set(resolved_market_types),
        max_polymarket_contracts_per_event=(
            defaults.max_polymarket_contracts_per_event
            if max_polymarket_contracts_per_event is None
            else max_polymarket_contracts_per_event
        ),
        polymarket_outcome_filter=(
            defaults.polymarket_outcome_filter
            if polymarket_outcome_filter is None
            else polymarket_outcome_filter
        ),
        max_match_days=defaults.max_match_days,
    )
