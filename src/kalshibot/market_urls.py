from __future__ import annotations

from urllib.parse import quote

KALSHI_BASE_URL = "https://kalshi.com"
POLYMARKET_BASE_URL = "https://polymarket.com"


def kalshi_market_url(ticker: str | None) -> str:
    query = quote(str(ticker or "").strip())
    return f"{KALSHI_BASE_URL}/search?query={query}"


def polymarket_market_url(
    *,
    slug: str | None = None,
    token_id: str | None = None,
) -> str:
    if slug:
        return f"{POLYMARKET_BASE_URL}/event/{quote(slug.strip('/'))}"
    query = quote(str(token_id or "").strip())
    return f"{POLYMARKET_BASE_URL}/search?query={query}"
