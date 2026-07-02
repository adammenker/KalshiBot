from __future__ import annotations

from kalshibot.discovery.models import DiscoveryMatch


def sorted_matches(matches: list[DiscoveryMatch]) -> list[DiscoveryMatch]:
    return sorted(
        matches,
        key=lambda match: (-match.confidence, match.polymarket_title, match.kalshi_ticker),
    )


def join_title_parts(*parts: str | None) -> str:
    return " | ".join(part.strip() for part in parts if part and part.strip())
