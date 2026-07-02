from __future__ import annotations

import json

from kalshibot.config import load_local_llm_config, load_polymarket_config
from kalshibot.market_matcher import OllamaTitleMatcher, match_market_titles
from kalshibot.polymarket import PolymarketClient, extract_market_tokens, format_top_of_book


def add_polymarket_parsers(subparsers) -> None:
    poly_market = subparsers.add_parser(
        "poly-market",
        help="Fetch a Polymarket market by slug and show its CLOB token IDs",
    )
    poly_market.add_argument("slug", help="Polymarket market slug")

    poly_event = subparsers.add_parser(
        "poly-event",
        help="Fetch a Polymarket event by slug and summarize its markets",
    )
    poly_event.add_argument("slug", help="Polymarket event slug")
    poly_event.add_argument("--raw", action="store_true", help="Print the full Polymarket response")

    poly_events = subparsers.add_parser(
        "poly-events",
        help="List active Polymarket events with market token IDs",
    )
    poly_events.add_argument("--limit", type=int, default=10, help="Maximum events to fetch")

    poly_book = subparsers.add_parser(
        "poly-book",
        help="Fetch Polymarket top-of-book prices for a CLOB token ID",
    )
    poly_book.add_argument("token_id", help="Polymarket CLOB token ID")

    poly_price = subparsers.add_parser(
        "poly-price",
        help="Fetch Polymarket CLOB /price for a token and side",
    )
    poly_price.add_argument("token_id", help="Polymarket CLOB token ID")
    poly_price.add_argument("side", choices=["BUY", "SELL", "buy", "sell"])

    match_titles = subparsers.add_parser(
        "match-titles",
        help="Use a local LLM to decide whether Polymarket and Kalshi titles are the same market",
    )
    match_titles.add_argument("--polymarket-title", required=True)
    match_titles.add_argument("--kalshi-title", required=True)
    match_titles.add_argument(
        "--no-llm",
        action="store_true",
        help="Use only the conservative lexical fallback matcher",
    )


def run_poly_market(slug: str) -> int:
    client = PolymarketClient(load_polymarket_config())
    market = client.get_market_by_slug(slug)
    summary = summarize_polymarket_market(market)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def run_poly_event(slug: str, raw: bool = False) -> int:
    client = PolymarketClient(load_polymarket_config())
    event = client.get_event_by_slug(slug)
    if raw:
        print(json.dumps(event, indent=2, sort_keys=True))
        return 0

    summary = {
        "id": event.get("id"),
        "title": event.get("title"),
        "slug": event.get("slug"),
        "active": event.get("active"),
        "closed": event.get("closed"),
        "markets": [
            summarize_polymarket_market(market)
            for market in event.get("markets", [])
            if isinstance(market, dict)
        ],
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def run_poly_events(limit: int) -> int:
    client = PolymarketClient(load_polymarket_config())
    events = client.list_events(limit=limit)
    summary = [
        {
            "id": event.get("id"),
            "title": event.get("title"),
            "slug": event.get("slug"),
            "market_count": len(event.get("markets", [])),
            "markets": [
                summarize_polymarket_market(market)
                for market in event.get("markets", [])
                if isinstance(market, dict)
            ],
        }
        for event in events
    ]
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def run_poly_book(token_id: str) -> int:
    client = PolymarketClient(load_polymarket_config())
    top = client.get_top_of_book(token_id)
    print(json.dumps(format_top_of_book(top), indent=2, sort_keys=True))
    return 0


def run_poly_price(token_id: str, side: str) -> int:
    client = PolymarketClient(load_polymarket_config())
    price = client.get_price(token_id, side)
    print(json.dumps(price, indent=2, sort_keys=True))
    return 0


def run_match_titles(polymarket_title: str, kalshi_title: str, *, no_llm: bool = False) -> int:
    llm = None if no_llm else OllamaTitleMatcher(load_local_llm_config())
    match = match_market_titles(
        polymarket_title,
        kalshi_title,
        llm=llm,
        use_llm=not no_llm,
    )
    print(
        json.dumps(
            {
                "same_market": match.same_market,
                "confidence": match.confidence,
                "reason": match.reason,
                "method": match.method,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def summarize_polymarket_market(market: dict[str, object]) -> dict[str, object]:
    summary: dict[str, object] = {
        "id": market.get("id"),
        "question": market.get("question"),
        "slug": market.get("slug"),
        "active": market.get("active"),
        "closed": market.get("closed"),
        "accepting_orders": market.get("acceptingOrders"),
    }
    try:
        summary["tokens"] = extract_market_tokens(market)
    except ValueError as exc:
        summary["tokens"] = []
        summary["token_parse_error"] = str(exc)
    return summary
