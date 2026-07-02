from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from kalshibot.cli import build_parser
from kalshibot.commands.discovery import (
    approved_matches_for_review,
    format_discovery_cli_output,
    format_match_review_output,
    parse_market_type_set,
    resolve_max_match_date,
)
from kalshibot.discovery import (
    KalshiDiscoveryMarket,
    KalshiEmbeddingIndex,
    PolymarketDiscoveryMarket,
    build_discovery_candidates,
    candidate_passes_prefilter,
    discover_market_matches,
    kalshi_market_series,
    kalshi_discovery_market,
    lexical_overlap,
    list_kalshi_discovery_markets,
    list_polymarket_discovery_markets,
    market_type,
    market_domain,
    normalize_kalshi_market,
    promote_discovered_matches,
    proper_noun_terms,
    kalshi_polymarket_search_queries,
    ranked_kalshi_candidates,
    resolve_discovery_profile,
    sort_kalshi_markets_by_size,
    validate_candidate_dates,
    validate_candidate_structure,
    validate_candidate_prices,
)
from kalshibot.market_matcher import MarketTitleMatch


def test_parse_market_type_set_preserves_lowercase_type_names() -> None:
    assert parse_market_type_set("unknown,AWARD_OR_FUTURES_WINNER") == {
        "unknown",
        "award_or_futures_winner",
    }


def test_discover_matches_parser_defaults_to_daily_discovery_sample() -> None:
    args = build_parser().parse_args(["discover-matches"])

    assert args.kalshi_limit == 25
    assert args.kalshi_fetch_limit == 500
    assert args.kalshi_pages == 5
    assert args.kalshi_sort_by == "volume-24h"
    assert args.market_profile == "win-lose"
    assert args.max_candidates_per_polymarket == 3
    assert args.price_validation_mode == "warn"
    assert args.strategy == "polymarket-search"
    assert args.review_output == Path("logs/discovery_matches.json")
    assert args.approved_review_output == Path("logs/approved_market_pairs.json")
    assert args.pairs_output == Path("config/approved_market_pairs.json")
    assert args.min_match_date is None
    assert args.max_match_date is None
    assert args.include_past_contracts is False
    assert args.no_max_match_date is False


def test_promote_discovered_matches_parser_writes_approved_review_by_default() -> None:
    args = build_parser().parse_args(["promote-discovered-matches"])

    assert args.review_output == Path("logs/approved_market_pairs.json")


def test_discovery_cli_output_keeps_terminal_summary_compact() -> None:
    output = format_discovery_cli_output(
        {
            "summary": {
                "kalshi_markets": 5,
                "kalshi_markets_fetched": 100,
                "kalshi_markets_after_filters": 12,
                "kalshi_fetch_limit": 100,
                "polymarket_search_results": 25,
                "polymarket_markets": 3,
                "candidate_pairs": 2,
                "matches": 1,
                "filter_hits": {"date": 1, "structure": 4},
                "price_validation_rejections": 1,
                "llm_candidates": 2,
                "comparisons": 0,
            },
            "matches": [
                {
                    "kalshi_title": "Los Angeles vs Indiana winner? | Los Angeles",
                    "polymarket_title": (
                        "Los Angeles Sparks vs. Indiana Fever | "
                        "Los Angeles Sparks vs. Indiana Fever | Los Angeles Sparks"
                    ),
                }
            ],
        },
        output=Path("data/discovered_market_matches.json"),
        market_profile="win-lose",
        search_debug_output=Path("data/debug.json"),
        search_debug_rows=5,
        pairs_output=Path("config/generated_market_pairs.json"),
        approved_review_output=Path("logs/approved_market_pairs.json"),
        pairs_promoted=1,
    )

    assert "Matched pairs:" in output
    assert "Kalshi: Los Angeles vs Indiana winner? | Los Angeles" in output
    assert "Poly:   Los Angeles Sparks vs. Indiana Fever" in output
    assert '"kalshi_after_filters": 12' in output
    assert '"kalshi_selected": 5' in output
    assert '"filter_hits": {"date": 1, "structure": 4}' in output
    assert '"approved_review": "logs/approved_market_pairs.json"' in output
    assert "flow_samples" not in output
    assert len(output.split("Discovery stats:\n", maxsplit=1)[1].splitlines()) <= 10


def test_match_review_output_keeps_only_manual_review_fields() -> None:
    output = format_match_review_output(
        [
            {
                "confidence": 0.92,
                "side_mapping": "same",
                "method": "structural_game_winner",
                "kalshi_ticker": "KXWCGAME-ESP",
                "kalshi_title": "World Cup game: Spain vs Saudi Arabia | Spain",
                "polymarket_token_id": "token-spain",
                "polymarket_title": "Spain vs Saudi Arabia | Spain",
                "date_validation": {
                    "kalshi_date": "2026-06-21",
                    "polymarket_date": "2026-06-21",
                },
                "price_validation": {
                    "kalshi_mid": "0.49",
                    "polymarket_mid": "0.51",
                    "difference": "0.02",
                    "passed": True,
                    "reason": "midpoint difference within threshold",
                },
            }
        ]
    )

    assert output == (
        "[\n"
        "  {\n"
        "    \"confidence\": 0.92,\n"
        "    \"kalshi\": {\n"
        "      \"date\": \"2026-06-21\",\n"
        "      \"price\": \"0.49\",\n"
        "      \"ticker\": \"KXWCGAME-ESP\",\n"
        "      \"title\": \"World Cup game: Spain vs Saudi Arabia | Spain\",\n"
        "      \"url\": \"https://kalshi.com/search?query=KXWCGAME-ESP\"\n"
        "    },\n"
        "    \"method\": \"structural_game_winner\",\n"
        "    \"polymarket\": {\n"
        "      \"date\": \"2026-06-21\",\n"
        "      \"price\": \"0.51\",\n"
        "      \"title\": \"Spain vs Saudi Arabia | Spain\",\n"
        "      \"token_id\": \"token-spain\",\n"
        "      \"url\": \"https://polymarket.com/search?query=token-spain\"\n"
        "    },\n"
        "    \"price_gap\": \"0.02\",\n"
        "    \"price_reason\": \"midpoint difference within threshold\",\n"
        "    \"price_status\": \"passed\",\n"
        "    \"side_mapping\": \"same\"\n"
        "  }\n"
        "]\n"
    )


def test_approved_matches_for_review_keeps_only_promoted_pairs() -> None:
    approved = approved_matches_for_review(
        [
            {
                "confidence": 0.92,
                "kalshi_ticker": "KXGOOD",
                "polymarket_token_id": "raw-token",
                "polymarket_yes_token_id": "selected-token",
                "price_validation": {"passed": True},
            },
            {
                "confidence": 0.92,
                "kalshi_ticker": "KXGOOD",
                "polymarket_token_id": "raw-token",
                "polymarket_yes_token_id": "selected-token",
                "price_validation": {"passed": True},
            },
            {
                "confidence": 0.92,
                "kalshi_ticker": "KXWARNING",
                "polymarket_token_id": "token-warning",
                "price_validation": {"passed": False},
            },
            {
                "confidence": 0.82,
                "kalshi_ticker": "KXLOW",
                "polymarket_token_id": "token-low",
                "price_validation": {"passed": True},
            },
        ],
        min_confidence=0.9,
    )

    assert approved == [
        {
            "confidence": 0.92,
            "kalshi_ticker": "KXGOOD",
            "polymarket_token_id": "selected-token",
            "polymarket_yes_token_id": "selected-token",
            "price_validation": {"passed": True},
        }
    ]


def test_win_lose_profile_filters_to_winner_markets() -> None:
    profile = resolve_discovery_profile(
        market_profile="win-lose",
        max_polymarket_contracts_per_event=None,
        polymarket_outcome_filter=None,
        kalshi_market_types=None,
    )

    assert profile.max_polymarket_contracts_per_event is None
    assert profile.polymarket_outcome_filter == "any"
    assert profile.kalshi_market_types == {"award_or_futures_winner", "game_winner"}
    assert profile.max_match_days is None


def test_crypto_threshold_profile_targets_crypto_threshold_markets() -> None:
    profile = resolve_discovery_profile(
        market_profile="crypto-threshold",
        max_polymarket_contracts_per_event=None,
        polymarket_outcome_filter=None,
        kalshi_market_types=None,
    )

    assert profile.max_polymarket_contracts_per_event == 80
    assert profile.polymarket_outcome_filter == "any"
    assert profile.kalshi_market_types == {"crypto_threshold"}
    assert profile.max_match_days == 60


def test_sports_game_winner_profile_uses_short_future_window() -> None:
    profile = resolve_discovery_profile(
        market_profile="sports-game-winner",
        max_polymarket_contracts_per_event=None,
        polymarket_outcome_filter=None,
        kalshi_market_types=None,
    )

    assert profile.max_polymarket_contracts_per_event is None
    assert profile.polymarket_outcome_filter == "any"
    assert profile.kalshi_market_types == {"game_winner"}
    assert profile.max_match_days == 14


def test_unnested_profile_filters_to_simple_yes_no_markets() -> None:
    profile = resolve_discovery_profile(
        market_profile="unnested",
        max_polymarket_contracts_per_event=None,
        polymarket_outcome_filter=None,
        kalshi_market_types=None,
    )

    assert profile.max_polymarket_contracts_per_event == 2
    assert profile.polymarket_outcome_filter == "yes-no"
    assert profile.kalshi_market_types == {"unknown", "award_or_futures_winner"}
    assert profile.max_match_days is None


def test_resolve_max_match_date_uses_profile_window() -> None:
    assert (
        resolve_max_match_date(
            max_match_date=None,
            no_max_match_date=False,
            profile_max_match_days=14,
            today=date(2026, 6, 30),
        )
        == "2026-07-14"
    )


def test_resolve_max_match_date_can_be_disabled_or_overridden() -> None:
    today = date(2026, 6, 30)

    assert (
        resolve_max_match_date(
            max_match_date="2026-12-31",
            no_max_match_date=False,
            profile_max_match_days=14,
            today=today,
        )
        == "2026-12-31"
    )
    assert (
        resolve_max_match_date(
            max_match_date=None,
            no_max_match_date=True,
            profile_max_match_days=14,
            today=today,
        )
        is None
    )
    assert (
        resolve_max_match_date(
            max_match_date=None,
            no_max_match_date=False,
            profile_max_match_days=None,
            today=today,
        )
        is None
    )


class FakePolymarketClient:
    def list_events(self, *, limit: int):
        return [
            {
                "title": "Spain vs Saudi Arabia",
                "markets": [
                    {
                        "question": "Spain vs Saudi Arabia",
                        "outcomes": '["Spain", "Draw"]',
                        "clobTokenIds": '["token-spain", "token-draw"]',
                        "conditionId": "0xabc",
                    }
                ],
            }
        ][:limit]

    def public_search(self, query: str, **kwargs):
        return {
            "events": [
                {
                    "title": "Spain vs Saudi Arabia",
                    "markets": [
                        {
                            "question": "Spain vs Saudi Arabia",
                            "outcomes": '["Spain", "Draw"]',
                            "clobTokenIds": '["token-spain", "token-draw"]',
                            "conditionId": "0xabc",
                            "closed": False,
                            "active": True,
                            "accepting_orders": True,
                        }
                    ],
                }
            ]
        }


class FakeKalshiClient:
    def list_markets(self, **kwargs):
        return [
            {
                "ticker": "KXWCGAME-ESP",
                "event_ticker": "KXWCGAME",
                "title": "World Cup game: Spain vs Saudi Arabia",
                "yes_sub_title": "Spain",
                "close_time": "2026-06-21T20:00:00Z",
            },
            {
                "ticker": "KXWCGAME-TIE",
                "event_ticker": "KXWCGAME",
                "title": "World Cup game: Spain vs Saudi Arabia",
                "yes_sub_title": "Tie",
                "close_time": "2026-06-21T20:00:00Z",
            },
        ]


class NestedSearchPolymarketClient:
    def public_search(self, query: str, **kwargs):
        return {
            "events": [
                {
                    "title": "San Diego Padres vs. Texas Rangers",
                    "markets": [
                        {
                            "question": "San Diego Padres vs. Texas Rangers",
                            "outcomes": '["San Diego Padres", "Texas Rangers"]',
                            "clobTokenIds": '["token-padres", "token-rangers"]',
                            "conditionId": "0xgame",
                            "closed": False,
                            "active": True,
                            "accepting_orders": True,
                        },
                        {
                            "question": "San Diego Padres vs. Texas Rangers: O/U 7.5",
                            "outcomes": '["Over", "Under"]',
                            "clobTokenIds": '["token-over", "token-under"]',
                            "conditionId": "0xtotal",
                            "closed": False,
                            "active": True,
                            "accepting_orders": True,
                        },
                    ],
                }
            ]
        }


class GameWinnerKalshiClient:
    def list_markets(self, **kwargs):
        return [
            {
                "ticker": "KXMLBGAME-SDTEX-SD",
                "event_ticker": "KXMLBGAME",
                "title": "San Diego vs Texas Winner?",
                "yes_sub_title": "San Diego",
                "close_time": "2026-06-21T20:00:00Z",
            }
        ]


class MixedSeriesKalshiClient:
    def list_markets(self, **kwargs):
        return [
            {
                "ticker": "KXWCGAME-ESP",
                "event_ticker": "KXWCGAME",
                "title": "World Cup game: Spain vs Saudi Arabia",
                "yes_sub_title": "Spain",
                "close_time": "2026-06-21T20:00:00Z",
            },
            {
                "ticker": "KXMLBTEAMTOTAL-NYY8",
                "event_ticker": "KXMLBTEAMTOTAL-26JUN221810NYYDET",
                "title": "Will New York Y score over 7.5 runs?",
                "yes_sub_title": "New York Y over 7.5 runs scored",
                "close_time": "2026-06-22T22:10:00Z",
            },
            {
                "ticker": "KXDOTA2GAME-ABC",
                "event_ticker": "KXDOTA2GAME",
                "title": "Will Team A win the match?",
                "yes_sub_title": "Team A",
                "close_time": "2026-06-22T22:10:00Z",
            },
        ]


class FakeMatcher:
    def match_titles(self, polymarket_title: str, kalshi_title: str) -> MarketTitleMatch:
        same = polymarket_title.endswith("Spain") and kalshi_title.endswith("Spain")
        return MarketTitleMatch(
            same_market=same,
            confidence=0.92 if same else 0.4,
            reason="fake matcher",
            method="fake",
        )


class CountingMatcher:
    def __init__(self) -> None:
        self.calls = 0

    def match_titles(self, polymarket_title: str, kalshi_title: str) -> MarketTitleMatch:
        self.calls += 1
        return MarketTitleMatch(
            same_market=True,
            confidence=0.99,
            reason="counting matcher",
            method="fake",
        )


class RejectingCountingMatcher:
    def __init__(self) -> None:
        self.calls = 0

    def match_titles(self, polymarket_title: str, kalshi_title: str) -> MarketTitleMatch:
        self.calls += 1
        return MarketTitleMatch(
            same_market=False,
            confidence=0.0,
            reason="rejecting matcher",
            method="fake",
        )


class MultiEventPolymarketClient:
    def list_events(self, *, limit: int):
        return [
            {
                "title": "Crypto IPO",
                "markets": [
                    {
                        "question": "Crypto IPO by 2026?",
                        "outcomes": '["Yes"]',
                        "clobTokenIds": '["token-crypto"]',
                    }
                ],
            },
            {
                "title": "Spain vs Saudi Arabia",
                "markets": [
                    {
                        "question": "Spain vs Saudi Arabia",
                        "outcomes": '["Spain"]',
                        "clobTokenIds": '["token-spain"]',
                    }
                ],
            },
        ][:limit]


class MultiMarketKalshiClient:
    def list_markets(self, **kwargs):
        return [
            {
                "ticker": "KXCRYPTO",
                "title": "Company IPO before 2026?",
                "yes_sub_title": "Yes",
            },
            {
                "ticker": "KXWCGAME-ESP",
                "title": "World Cup game: Spain vs Saudi Arabia",
                "yes_sub_title": "Spain",
            },
        ]


class FakeEmbeddingEncoder:
    def encode(self, titles: list[str]):
        np = pytest.importorskip("numpy")
        vectors = []
        for title in titles:
            title = title.lower()
            if "spain" in title or "saudi" in title:
                vectors.append([1.0, 0.0, 0.0])
            elif "belgium" in title or "iran" in title:
                vectors.append([0.0, 1.0, 0.0])
            else:
                vectors.append([0.0, 0.0, 1.0])
        return np.asarray(vectors)


class FakePriceKalshiClient:
    def __init__(self, yes_bid: str, no_bid: str) -> None:
        self.yes_bid = yes_bid
        self.no_bid = no_bid

    def get_market_orderbook(self, ticker: str, depth: int = 100):
        return {
            "orderbook_fp": {
                "yes_dollars": [[self.yes_bid, "100"]],
                "no_dollars": [[self.no_bid, "100"]],
            }
        }


class FakePricePolymarketClient:
    def __init__(self, bid: str, ask: str) -> None:
        self.bid = bid
        self.ask = ask

    def get_order_book(self, token_id: str):
        return {
            "bids": [{"price": self.bid, "size": "100"}],
            "asks": [{"price": self.ask, "size": "100"}],
        }

    def top_of_book_from_order_book(self, token_id: str, book: dict):
        from kalshibot.config import PolymarketConfig
        from kalshibot.polymarket import PolymarketClient

        return PolymarketClient(
            PolymarketConfig(
                gamma_base_url="https://example.com",
                clob_base_url="https://example.com",
                data_base_url="https://example.com",
            )
        ).top_of_book_from_order_book(token_id, book)


class PriceValidatedKalshiClient(FakeKalshiClient, FakePriceKalshiClient):
    def __init__(self, yes_bid: str = "0.49", no_bid: str = "0.49") -> None:
        FakePriceKalshiClient.__init__(self, yes_bid=yes_bid, no_bid=no_bid)


class PriceValidatedPolymarketClient(FakePolymarketClient, FakePricePolymarketClient):
    def __init__(self, bid: str = "0.50", ask: str = "0.52") -> None:
        FakePricePolymarketClient.__init__(self, bid=bid, ask=ask)


class SimpleBinaryPolymarketClient:
    def public_search(self, query: str, **kwargs):
        return {
            "events": [
                {
                    "title": "Will Alice Smith win the 2026 mayoral election?",
                    "markets": [
                        {
                            "question": "Will Alice Smith win the 2026 mayoral election?",
                            "outcomes": '["Yes", "No"]',
                            "clobTokenIds": '["token-yes", "token-no"]',
                            "conditionId": "0xmayor",
                            "closed": False,
                            "active": True,
                            "accepting_orders": True,
                        }
                    ],
                }
            ]
        }


class SimpleBinaryKalshiClient:
    def list_markets(self, **kwargs):
        return [
            {
                "ticker": "KXELECTION-ALICE",
                "event_ticker": "KXELECTION",
                "title": "Will Alice Smith win the 2026 mayoral election?",
                "yes_sub_title": "Yes",
                "close_time": "2026-11-03T23:59:00Z",
            }
        ]


class FullTeamPolymarketClient:
    def public_search(self, query: str, **kwargs):
        return {
            "events": [
                {
                    "title": "Los Angeles Sparks vs. Indiana Fever",
                    "markets": [
                        {
                            "question": "Los Angeles Sparks vs. Indiana Fever",
                            "outcomes": '["Los Angeles Sparks", "Indiana Fever"]',
                            "clobTokenIds": '["token-sparks", "token-fever"]',
                            "conditionId": "0xwnba",
                            "closed": False,
                            "active": True,
                            "accepting_orders": True,
                        }
                    ],
                }
            ]
        }


class WrongDatePolymarketClient:
    def public_search(self, query: str, **kwargs):
        return {
            "events": [
                {
                    "title": "Los Angeles Sparks vs. Indiana Fever",
                    "slug": "wnba-los-angeles-sparks-indiana-fever-2026-07-09",
                    "endDate": "2026-07-10T02:00:00Z",
                    "markets": [
                        {
                            "question": "Los Angeles Sparks vs. Indiana Fever",
                            "outcomes": '["Los Angeles Sparks", "Indiana Fever"]',
                            "clobTokenIds": '["token-sparks", "token-fever"]',
                            "conditionId": "0xwnba",
                            "closed": False,
                            "active": True,
                            "accepting_orders": True,
                        }
                    ],
                }
            ]
        }


class CityOnlyKalshiClient:
    def list_markets(self, **kwargs):
        return [
            {
                "ticker": "KXWNBAGAME-LA",
                "event_ticker": "KXWNBAGAME",
                "title": "Los Angeles vs Indiana winner?",
                "yes_sub_title": "Los Angeles",
            }
        ]


class DatedCityOnlyKalshiClient:
    def list_markets(self, **kwargs):
        return [
            {
                "ticker": "KXWNBAGAME-26JUL08LAIND-LA",
                "event_ticker": "KXWNBAGAME-26JUL08LAIND",
                "title": "Los Angeles vs Indiana winner?",
                "yes_sub_title": "Los Angeles",
                "close_time": "2026-07-09T02:00:00Z",
                "expected_expiration_time": "2026-07-09T02:00:00Z",
            }
        ]


class EmptySearchPolymarketClient:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def public_search(self, query: str, **kwargs):
        self.queries.append(query)
        return {"events": [], "markets": []}


class SizeSortedKalshiClient:
    def list_markets(self, **kwargs):
        return [
            {
                "ticker": "KXSMALL",
                "title": "Will small market happen?",
                "yes_sub_title": "Yes",
                "volume_24h_fp": "1",
                "volume_fp": "15",
                "open_interest_fp": "3",
            },
            {
                "ticker": "KXLARGE",
                "title": "Will large market happen?",
                "yes_sub_title": "Yes",
                "volume_24h_fp": "100",
                "volume_fp": "500",
                "open_interest_fp": "200",
            },
            {
                "ticker": "KXMEDIUM",
                "title": "Will medium market happen?",
                "yes_sub_title": "Yes",
                "volume_24h_fp": "10",
                "volume_fp": "80",
                "open_interest_fp": "20",
            },
        ]


class WinLoseMixedKalshiClient:
    def list_markets(self, **kwargs):
        return [
            {
                "ticker": "KXMLBSEASON-NYY",
                "event_ticker": "KXMLBSEASON",
                "title": "Will the New York Yankees win more than 86.5 games?",
                "yes_sub_title": "Yes",
            },
            {
                "ticker": "KXELECTION-ALICE",
                "event_ticker": "KXELECTION",
                "title": "Will Alice Smith win the 2026 mayoral election?",
                "yes_sub_title": "Yes",
            },
            {
                "ticker": "KXGAME-BOS",
                "event_ticker": "KXGAME",
                "title": "Boston vs Colorado Winner?",
                "yes_sub_title": "Boston",
            },
        ]


def test_list_polymarket_discovery_markets_expands_outcome_tokens() -> None:
    markets = list_polymarket_discovery_markets(FakePolymarketClient(), event_limit=1)

    assert [market.outcome for market in markets] == ["Spain", "Draw"]
    assert markets[0].token_id == "token-spain"
    assert markets[0].condition_id == "0xabc"


def test_list_polymarket_discovery_markets_skips_closed_or_inactive_markets() -> None:
    class Client:
        def list_events(self, *, limit: int):
            return [
                {
                    "title": "Mixed event",
                    "markets": [
                        {
                            "question": "Closed market",
                            "outcomes": '["Yes"]',
                            "clobTokenIds": '["token-closed"]',
                            "closed": True,
                        },
                        {
                            "question": "Paused market",
                            "outcomes": '["Yes"]',
                            "clobTokenIds": '["token-paused"]',
                            "accepting_orders": False,
                        },
                        {
                            "question": "Open market",
                            "outcomes": '["Yes"]',
                            "clobTokenIds": '["token-open"]',
                            "closed": False,
                            "active": True,
                            "accepting_orders": True,
                        },
                    ],
                }
            ][:limit]

    markets = list_polymarket_discovery_markets(Client(), event_limit=1)

    assert [market.token_id for market in markets] == ["token-open"]


def test_kalshi_discovery_market_uses_yes_subtitle() -> None:
    market = kalshi_discovery_market(
        {
            "ticker": "KXTEST",
            "title": "World Cup game",
            "yes_sub_title": "Spain",
            "no_sub_title": "Not Spain",
        }
    )

    assert market.full_title == "World Cup game | Spain"


def test_kalshi_discovery_market_captures_size_fields() -> None:
    market = kalshi_discovery_market(
        {
            "ticker": "KXTEST",
            "title": "Will test market happen?",
            "yes_sub_title": "Yes",
            "volume_fp": "12.50",
            "volume_24h_fp": "3.25",
            "open_interest_fp": "7",
            "liquidity_dollars": "120.00",
            "notional_value_dollars": "55.50",
        }
    )

    assert market.volume == Decimal("12.50")
    assert market.volume_24h == Decimal("3.25")
    assert market.open_interest == Decimal("7")
    assert market.liquidity == Decimal("120.00")
    assert market.notional_value == Decimal("55.50")


def test_sort_kalshi_markets_by_size_is_opt_in() -> None:
    markets = [
        KalshiDiscoveryMarket("KXSMALL", None, "Small", "Yes", None, None, "Small", volume_24h=Decimal("1")),
        KalshiDiscoveryMarket("KXLARGE", None, "Large", "Yes", None, None, "Large", volume_24h=Decimal("100")),
    ]

    assert [market.ticker for market in sort_kalshi_markets_by_size(markets, sort_by="none")] == [
        "KXSMALL",
        "KXLARGE",
    ]
    assert [
        market.ticker for market in sort_kalshi_markets_by_size(markets, sort_by="volume_24h")
    ] == [
        "KXLARGE",
        "KXSMALL",
    ]


def test_discover_market_matches_keeps_only_threshold_matches() -> None:
    result = discover_market_matches(
        polymarket_client=FakePolymarketClient(),
        kalshi_client=FakeKalshiClient(),
        llm=FakeMatcher(),
        use_llm=True,
        confidence_threshold=0.85,
        polymarket_event_limit=1,
        kalshi_limit=100,
        kalshi_pages=1,
        kalshi_status="open",
        kalshi_series_ticker=None,
        max_candidates_per_polymarket=2,
        max_comparisons=None,
        prefilter_threshold=0.1,
    )

    assert result["summary"]["matches"] == 1
    assert result["matches"][0]["polymarket_token_id"] == "token-spain"
    assert result["matches"][0]["kalshi_ticker"] == "KXWCGAME-ESP"


def test_discover_market_matches_stops_at_max_comparisons() -> None:
    result = discover_market_matches(
        polymarket_client=FakePolymarketClient(),
        kalshi_client=FakeKalshiClient(),
        llm=FakeMatcher(),
        use_llm=True,
        confidence_threshold=0.85,
        polymarket_event_limit=1,
        kalshi_limit=100,
        kalshi_pages=1,
        kalshi_status="open",
        kalshi_series_ticker=None,
        max_candidates_per_polymarket=2,
        max_comparisons=1,
        prefilter_threshold=0.1,
    )

    assert result["summary"]["comparisons"] == 1
    assert result["summary"]["stopped_early"] is True


def test_discover_market_matches_can_use_polymarket_search_strategy() -> None:
    result = discover_market_matches(
        polymarket_client=FakePolymarketClient(),
        kalshi_client=FakeKalshiClient(),
        llm=FakeMatcher(),
        use_llm=True,
        confidence_threshold=0.85,
        polymarket_event_limit=1,
        kalshi_limit=100,
        kalshi_pages=1,
        kalshi_status="open",
        kalshi_series_ticker=None,
        max_candidates_per_polymarket=2,
        max_comparisons=None,
        prefilter_threshold=0.1,
        discovery_strategy="polymarket-search",
        polymarket_search_limit=5,
    )

    assert result["summary"]["discovery_strategy"] == "polymarket-search"
    assert result["summary"]["candidate_pairs"] == 2
    assert result["summary"]["matches"] == 1
    assert result["matches"][0]["polymarket_token_id"] == "token-spain"


def test_discover_market_matches_without_llm_keeps_deterministic_matches() -> None:
    matcher = CountingMatcher()

    result = discover_market_matches(
        polymarket_client=FakePolymarketClient(),
        kalshi_client=FakeKalshiClient(),
        llm=matcher,
        use_llm=False,
        confidence_threshold=0.85,
        polymarket_event_limit=1,
        kalshi_limit=100,
        kalshi_pages=1,
        kalshi_status="open",
        kalshi_series_ticker=None,
        max_candidates_per_polymarket=2,
        max_comparisons=None,
        prefilter_threshold=0.1,
        discovery_strategy="polymarket-search",
        polymarket_search_limit=5,
    )

    assert matcher.calls == 0
    assert result["summary"]["llm_enabled"] is False
    assert result["summary"]["candidate_pairs"] == 2
    assert result["summary"]["llm_candidates"] == 2
    assert result["summary"]["llm_skipped"] == 1
    assert result["summary"]["comparisons"] == 0
    assert result["summary"]["matches"] == 1
    assert result["matches"][0]["polymarket_token_id"] == "token-spain"


def test_discover_market_matches_can_filter_stale_contract_dates() -> None:
    result = discover_market_matches(
        polymarket_client=FakePolymarketClient(),
        kalshi_client=FakeKalshiClient(),
        llm=None,
        use_llm=False,
        confidence_threshold=0.85,
        polymarket_event_limit=1,
        kalshi_limit=100,
        kalshi_pages=1,
        kalshi_status="open",
        kalshi_series_ticker=None,
        max_candidates_per_polymarket=2,
        max_comparisons=None,
        prefilter_threshold=0.1,
        discovery_strategy="polymarket-search",
        polymarket_search_limit=5,
        min_match_date="2026-06-22",
    )

    assert result["summary"]["candidate_pairs"] == 2
    assert result["summary"]["matches"] == 0
    assert result["summary"]["stale_match_rejections"] == 2
    assert result["summary"]["filter_hits"]["stale_date"] == 2


def test_discover_market_matches_can_filter_future_contract_dates() -> None:
    result = discover_market_matches(
        polymarket_client=FakePolymarketClient(),
        kalshi_client=FakeKalshiClient(),
        llm=None,
        use_llm=False,
        confidence_threshold=0.85,
        polymarket_event_limit=1,
        kalshi_limit=100,
        kalshi_pages=1,
        kalshi_status="open",
        kalshi_series_ticker=None,
        max_candidates_per_polymarket=2,
        max_comparisons=None,
        prefilter_threshold=0.1,
        discovery_strategy="polymarket-search",
        polymarket_search_limit=5,
        max_match_date="2026-06-20",
    )

    assert result["summary"]["candidate_pairs"] == 2
    assert result["summary"]["matches"] == 0
    assert result["summary"]["future_match_rejections"] == 2
    assert result["summary"]["filter_hits"]["future_date"] == 2


def test_discover_market_matches_structurally_accepts_city_only_game_winner() -> None:
    matcher = RejectingCountingMatcher()

    result = discover_market_matches(
        polymarket_client=FullTeamPolymarketClient(),
        kalshi_client=CityOnlyKalshiClient(),
        llm=matcher,
        use_llm=True,
        confidence_threshold=0.85,
        polymarket_event_limit=1,
        kalshi_limit=100,
        kalshi_pages=1,
        kalshi_status="open",
        kalshi_series_ticker=None,
        max_candidates_per_polymarket=2,
        max_comparisons=None,
        prefilter_threshold=0.1,
        discovery_strategy="polymarket-search",
        polymarket_search_limit=5,
        max_polymarket_contracts_per_event=2,
        price_validation_threshold=None,
        flow_summary_limit=1,
    )

    assert matcher.calls == 0
    assert result["summary"]["deterministic_matches"] == 1
    assert result["summary"]["comparisons"] == 0
    assert result["summary"]["matches"] == 1
    assert result["matches"][0]["method"] == "structural_game_winner"
    assert result["matches"][0]["polymarket_token_id"] == "token-sparks"
    sample = result["flow_samples"][0]["best_candidates"][0]
    assert sample["matched"] is True
    assert sample["metadata"]["deterministic_result"]["method"] == "structural_game_winner"


def test_discover_market_matches_rejects_same_game_winner_on_wrong_date() -> None:
    result = discover_market_matches(
        polymarket_client=WrongDatePolymarketClient(),
        kalshi_client=DatedCityOnlyKalshiClient(),
        llm=None,
        use_llm=False,
        confidence_threshold=0.85,
        polymarket_event_limit=1,
        kalshi_limit=100,
        kalshi_pages=1,
        kalshi_status="open",
        kalshi_series_ticker=None,
        max_candidates_per_polymarket=2,
        max_comparisons=None,
        prefilter_threshold=0.1,
        discovery_strategy="polymarket-search",
        polymarket_search_limit=5,
        max_polymarket_contracts_per_event=2,
        price_validation_threshold=None,
        flow_summary_limit=1,
    )

    assert result["summary"]["candidate_pairs"] == 0
    first_result = result["flow_samples"][0]["best_search_results"][0]
    assert first_result["status"] == "rejected"
    assert first_result["filter"] == "date"
    assert first_result["metadata"]["date_validation"] == {
        "passed": False,
        "kalshi_date": "2026-07-08",
        "polymarket_date": "2026-07-09",
        "difference_days": 1,
        "reason": "contract dates differ 2026-07-08!=2026-07-09",
    }


def test_discover_market_matches_can_include_compact_flow_samples() -> None:
    result = discover_market_matches(
        polymarket_client=FakePolymarketClient(),
        kalshi_client=FakeKalshiClient(),
        llm=None,
        use_llm=False,
        confidence_threshold=0.85,
        polymarket_event_limit=1,
        kalshi_limit=100,
        kalshi_pages=1,
        kalshi_status="open",
        kalshi_series_ticker=None,
        max_candidates_per_polymarket=2,
        max_comparisons=None,
        prefilter_threshold=0.1,
        discovery_strategy="polymarket-search",
        polymarket_search_limit=5,
        flow_summary_limit=1,
    )

    assert "search_debug" not in result
    assert len(result["flow_samples"]) == 1
    sample = result["flow_samples"][0]
    assert sample["kalshi"]["ticker"] == "KXWCGAME-ESP"
    assert sample["kalshi"]["title"] == "World Cup game: Spain vs Saudi Arabia | Spain"
    assert sample["polymarket_result_count"] == 2
    assert len(sample["best_search_results"]) == 2
    assert len(sample["best_candidates"]) == 1
    first_result = sample["best_candidates"][0]
    assert first_result["polymarket"]["title"] == "Spain vs Saudi Arabia | Spain vs Saudi Arabia | Spain"
    assert first_result["matched"] is True
    assert first_result["status"] == "matched"
    assert first_result["filter"] is None
    assert first_result["metadata"]["candidate_count_for_kalshi"] == 1


def test_flow_samples_show_one_comparison_per_kalshi_market() -> None:
    result = discover_market_matches(
        polymarket_client=FakePolymarketClient(),
        kalshi_client=FakeKalshiClient(),
        llm=None,
        use_llm=False,
        confidence_threshold=0.85,
        polymarket_event_limit=1,
        kalshi_limit=100,
        kalshi_pages=1,
        kalshi_status="open",
        kalshi_series_ticker=None,
        max_candidates_per_polymarket=2,
        max_comparisons=None,
        prefilter_threshold=0.1,
        discovery_strategy="polymarket-search",
        polymarket_search_limit=5,
        flow_summary_limit=2,
    )

    assert [sample["kalshi"]["ticker"] for sample in result["flow_samples"]] == [
        "KXWCGAME-ESP",
        "KXWCGAME-TIE",
    ]


def test_discover_market_matches_can_filter_to_simple_yes_no_outcomes() -> None:
    result = discover_market_matches(
        polymarket_client=FakePolymarketClient(),
        kalshi_client=FakeKalshiClient(),
        llm=None,
        use_llm=False,
        confidence_threshold=0.85,
        polymarket_event_limit=1,
        kalshi_limit=100,
        kalshi_pages=1,
        kalshi_status="open",
        kalshi_series_ticker=None,
        max_candidates_per_polymarket=2,
        max_comparisons=None,
        prefilter_threshold=0.1,
        discovery_strategy="polymarket-search",
        polymarket_search_limit=5,
        max_polymarket_contracts_per_event=2,
        polymarket_outcome_filter="yes-no",
        flow_summary_limit=1,
    )

    assert result["summary"]["candidate_pairs"] == 0
    assert result["flow_samples"][0]["best_candidates"] == []
    first_result = result["flow_samples"][0]["best_search_results"][0]
    assert first_result["polymarket"]["outcome"] == "Spain"
    assert first_result["metadata"]["outcome_filter"] == "yes-no"
    assert first_result["status"] == "rejected"
    assert first_result["filter"] == "outcome_filter"


def test_discover_market_matches_keeps_simple_binary_yes_no_candidates() -> None:
    result = discover_market_matches(
        polymarket_client=SimpleBinaryPolymarketClient(),
        kalshi_client=SimpleBinaryKalshiClient(),
        llm=None,
        use_llm=False,
        confidence_threshold=0.85,
        polymarket_event_limit=1,
        kalshi_limit=100,
        kalshi_pages=1,
        kalshi_status="open",
        kalshi_series_ticker=None,
        max_candidates_per_polymarket=2,
        max_comparisons=None,
        prefilter_threshold=0.1,
        discovery_strategy="polymarket-search",
        polymarket_search_limit=5,
        max_polymarket_contracts_per_event=2,
        polymarket_outcome_filter="yes-no",
        kalshi_market_types={"unknown", "award_or_futures_winner"},
        flow_summary_limit=1,
    )

    assert result["summary"]["kalshi_markets"] == 1
    assert result["summary"]["kalshi_market_types"] == ["award_or_futures_winner", "unknown"]
    assert result["summary"]["candidate_pairs"] == 2
    assert result["summary"]["llm_candidates"] == 2
    first_result = result["flow_samples"][0]["best_candidates"][0]
    assert first_result["polymarket"]["outcome"] == "Yes"
    assert first_result["metadata"]["event_contract_count"] == 2
    assert first_result["status"] == "ready_for_llm"
    assert first_result["filter"] == "llm_not_run"


def test_discover_market_matches_can_filter_kalshi_game_markets_before_search() -> None:
    result = discover_market_matches(
        polymarket_client=NestedSearchPolymarketClient(),
        kalshi_client=GameWinnerKalshiClient(),
        llm=None,
        use_llm=False,
        confidence_threshold=0.85,
        polymarket_event_limit=1,
        kalshi_limit=100,
        kalshi_pages=1,
        kalshi_status="open",
        kalshi_series_ticker=None,
        max_candidates_per_polymarket=2,
        max_comparisons=None,
        prefilter_threshold=0.1,
        discovery_strategy="polymarket-search",
        polymarket_search_limit=5,
        kalshi_market_types={"unknown", "award_or_futures_winner"},
        max_polymarket_contracts_per_event=2,
        polymarket_outcome_filter="yes-no",
        flow_summary_limit=1,
    )

    assert result["summary"]["kalshi_markets"] == 0
    assert result["summary"]["candidate_pairs"] == 0
    assert result["summary"]["llm_candidates"] == 0
    assert result["flow_samples"] == []


def test_discover_market_matches_can_filter_kalshi_series() -> None:
    result = discover_market_matches(
        polymarket_client=FakePolymarketClient(),
        kalshi_client=MixedSeriesKalshiClient(),
        llm=FakeMatcher(),
        use_llm=True,
        confidence_threshold=0.85,
        polymarket_event_limit=1,
        kalshi_limit=100,
        kalshi_pages=1,
        kalshi_status="open",
        kalshi_series_ticker=None,
        kalshi_include_series={"KXWCGAME", "KXMLBTEAMTOTAL"},
        kalshi_exclude_series={"KXMLBTEAMTOTAL"},
        max_candidates_per_polymarket=2,
        max_comparisons=None,
        prefilter_threshold=0.1,
        discovery_strategy="polymarket-search",
        polymarket_search_limit=5,
    )

    assert result["summary"]["kalshi_markets"] == 1
    assert result["summary"]["kalshi_include_series"] == ["KXMLBTEAMTOTAL", "KXWCGAME"]
    assert result["summary"]["kalshi_exclude_series"] == ["KXMLBTEAMTOTAL"]


def test_list_kalshi_discovery_markets_filters_by_series_prefix() -> None:
    markets = list_kalshi_discovery_markets(
        MixedSeriesKalshiClient(),
        limit=100,
        pages=1,
        status="open",
        series_ticker=None,
        include_series={"KXWCGAME", "KXDOTA2GAME"},
        exclude_series=None,
    )

    assert [kalshi_market_series(market) for market in markets] == [
        "KXWCGAME",
        "KXDOTA2GAME",
    ]


def test_discover_market_matches_can_optionally_rank_kalshi_by_size() -> None:
    polymarket_client = EmptySearchPolymarketClient()

    result = discover_market_matches(
        polymarket_client=polymarket_client,
        kalshi_client=SizeSortedKalshiClient(),
        llm=None,
        use_llm=False,
        confidence_threshold=0.85,
        polymarket_event_limit=1,
        kalshi_limit=2,
        kalshi_fetch_limit=3,
        kalshi_pages=1,
        kalshi_status="open",
        kalshi_series_ticker=None,
        max_candidates_per_polymarket=2,
        max_comparisons=None,
        prefilter_threshold=0.1,
        discovery_strategy="polymarket-search",
        polymarket_search_limit=5,
        include_search_debug=True,
        kalshi_size_sort_by="volume-24h",
    )

    assert result["summary"]["kalshi_markets_fetched"] == 3
    assert result["summary"]["kalshi_markets"] == 2
    assert result["summary"]["kalshi_size_sort_by"] == "volume_24h"
    assert [row["kalshi"]["ticker"] for row in result["search_debug"]] == [
        "KXLARGE",
        "KXMEDIUM",
    ]
    assert "Will large market happen? | Yes" in polymarket_client.queries
    assert "Will medium market happen? | Yes" in polymarket_client.queries
    assert len(polymarket_client.queries) > 2


def test_discover_market_matches_keeps_original_kalshi_order_without_size_sort() -> None:
    result = discover_market_matches(
        polymarket_client=EmptySearchPolymarketClient(),
        kalshi_client=SizeSortedKalshiClient(),
        llm=None,
        use_llm=False,
        confidence_threshold=0.85,
        polymarket_event_limit=1,
        kalshi_limit=2,
        kalshi_fetch_limit=3,
        kalshi_pages=1,
        kalshi_status="open",
        kalshi_series_ticker=None,
        max_candidates_per_polymarket=2,
        max_comparisons=None,
        prefilter_threshold=0.1,
        discovery_strategy="polymarket-search",
        polymarket_search_limit=5,
        include_search_debug=True,
        kalshi_size_sort_by="none",
    )

    assert result["summary"]["kalshi_size_sort_by"] == "none"
    assert [row["kalshi"]["ticker"] for row in result["search_debug"]] == [
        "KXSMALL",
        "KXLARGE",
    ]


def test_win_lose_market_type_filter_skips_season_win_totals() -> None:
    result = discover_market_matches(
        polymarket_client=EmptySearchPolymarketClient(),
        kalshi_client=WinLoseMixedKalshiClient(),
        llm=None,
        use_llm=False,
        confidence_threshold=0.85,
        polymarket_event_limit=1,
        kalshi_limit=5,
        kalshi_fetch_limit=10,
        kalshi_pages=1,
        kalshi_status="open",
        kalshi_series_ticker=None,
        max_candidates_per_polymarket=2,
        max_comparisons=None,
        prefilter_threshold=0.1,
        discovery_strategy="polymarket-search",
        polymarket_search_limit=5,
        include_search_debug=True,
        kalshi_market_types={"award_or_futures_winner", "game_winner"},
    )

    assert result["summary"]["kalshi_markets_fetched"] == 3
    assert result["summary"]["kalshi_markets_after_filters"] == 2
    assert [row["kalshi"]["ticker"] for row in result["search_debug"]] == [
        "KXELECTION-ALICE",
        "KXGAME-BOS",
    ]


def test_discover_market_matches_can_include_search_debug_rows() -> None:
    result = discover_market_matches(
        polymarket_client=FakePolymarketClient(),
        kalshi_client=FakeKalshiClient(),
        llm=FakeMatcher(),
        use_llm=True,
        confidence_threshold=0.85,
        polymarket_event_limit=1,
        kalshi_limit=100,
        kalshi_pages=1,
        kalshi_status="open",
        kalshi_series_ticker=None,
        max_candidates_per_polymarket=2,
        max_comparisons=1,
        prefilter_threshold=0.1,
        discovery_strategy="polymarket-search",
        polymarket_search_limit=5,
        include_search_debug=True,
    )

    assert len(result["search_debug"]) == 2
    row = result["search_debug"][0]
    assert row["kalshi"]["ticker"] == "KXWCGAME-ESP"
    assert row["query"] == "Spain Saudi Arabia"
    assert "World Cup game: Spain vs Saudi Arabia | Spain" in row["queries"]
    assert row["polymarket_result_count"] == 2
    assert row["polymarket_results"][0]["token_id"] == "token-spain"
    assert row["polymarket_results"][0]["passes_prefilter"] is True


def test_discover_market_matches_skips_overly_nested_polymarket_events() -> None:
    matcher = CountingMatcher()

    result = discover_market_matches(
        polymarket_client=NestedSearchPolymarketClient(),
        kalshi_client=GameWinnerKalshiClient(),
        llm=matcher,
        use_llm=True,
        confidence_threshold=0.85,
        polymarket_event_limit=1,
        kalshi_limit=100,
        kalshi_pages=1,
        kalshi_status="open",
        kalshi_series_ticker=None,
        max_candidates_per_polymarket=5,
        max_comparisons=None,
        prefilter_threshold=0.1,
        discovery_strategy="polymarket-search",
        polymarket_search_limit=5,
        max_polymarket_contracts_per_event=2,
        include_search_debug=True,
        price_validation_threshold=None,
    )

    assert matcher.calls == 0
    assert result["summary"]["candidate_pairs"] == 0
    assert result["summary"]["max_polymarket_contracts_per_event"] == 2
    first_result = result["search_debug"][0]["polymarket_results"][0]
    assert first_result["event_contract_count"] == 4
    assert first_result["skipped_nested_event"] is True


def test_discover_market_matches_price_validation_warns_without_blocking_by_default() -> None:
    matcher = CountingMatcher()

    result = discover_market_matches(
        polymarket_client=PriceValidatedPolymarketClient(bid="0.58", ask="0.60"),
        kalshi_client=PriceValidatedKalshiClient(yes_bid="0.49", no_bid="0.49"),
        llm=matcher,
        use_llm=True,
        confidence_threshold=0.85,
        polymarket_event_limit=1,
        kalshi_limit=100,
        kalshi_pages=1,
        kalshi_status="open",
        kalshi_series_ticker=None,
        max_candidates_per_polymarket=1,
        max_comparisons=None,
        prefilter_threshold=0.1,
        discovery_strategy="polymarket-search",
        polymarket_search_limit=5,
        price_validation_threshold=Decimal("0.03"),
    )

    assert matcher.calls == 1
    assert result["summary"]["price_validations"] == 2
    assert result["summary"]["price_validation_rejections"] == 2
    assert result["summary"]["price_validation_mode"] == "warn"
    assert result["summary"]["comparisons"] == 1
    assert result["summary"]["matches"] == 2


def test_discover_market_matches_can_still_reject_on_price_validation() -> None:
    matcher = CountingMatcher()

    result = discover_market_matches(
        polymarket_client=PriceValidatedPolymarketClient(bid="0.58", ask="0.60"),
        kalshi_client=PriceValidatedKalshiClient(yes_bid="0.49", no_bid="0.49"),
        llm=matcher,
        use_llm=True,
        confidence_threshold=0.85,
        polymarket_event_limit=1,
        kalshi_limit=100,
        kalshi_pages=1,
        kalshi_status="open",
        kalshi_series_ticker=None,
        max_candidates_per_polymarket=1,
        max_comparisons=None,
        prefilter_threshold=0.1,
        discovery_strategy="polymarket-search",
        polymarket_search_limit=5,
        price_validation_threshold=Decimal("0.03"),
        reject_on_price_validation=True,
    )

    assert matcher.calls == 0
    assert result["summary"]["price_validation_mode"] == "reject"
    assert result["summary"]["price_validation_rejections"] == 2
    assert result["summary"]["matches"] == 0


def test_discover_market_matches_can_warn_on_price_validation_without_rejecting() -> None:
    result = discover_market_matches(
        polymarket_client=PriceValidatedPolymarketClient(bid="0.58", ask="0.60"),
        kalshi_client=PriceValidatedKalshiClient(yes_bid="0.49", no_bid="0.49"),
        llm=None,
        use_llm=False,
        confidence_threshold=0.85,
        polymarket_event_limit=1,
        kalshi_limit=100,
        kalshi_pages=1,
        kalshi_status="open",
        kalshi_series_ticker=None,
        max_candidates_per_polymarket=1,
        max_comparisons=None,
        prefilter_threshold=0.1,
        discovery_strategy="polymarket-search",
        polymarket_search_limit=5,
        price_validation_threshold=Decimal("0.03"),
        reject_on_price_validation=False,
    )

    assert result["summary"]["price_validation_mode"] == "warn"
    assert result["summary"]["price_validations"] == 2
    assert result["summary"]["price_validation_rejections"] == 2
    assert result["summary"]["llm_candidates"] == 2
    assert result["summary"]["llm_skipped"] == 1
    assert result["summary"]["matches"] == 1


def test_discover_market_matches_spends_limited_comparisons_on_best_global_candidate() -> None:
    result = discover_market_matches(
        polymarket_client=MultiEventPolymarketClient(),
        kalshi_client=MultiMarketKalshiClient(),
        llm=FakeMatcher(),
        use_llm=True,
        confidence_threshold=0.85,
        polymarket_event_limit=2,
        kalshi_limit=100,
        kalshi_pages=1,
        kalshi_status="open",
        kalshi_series_ticker=None,
        max_candidates_per_polymarket=1,
        max_comparisons=1,
        prefilter_threshold=0.1,
    )

    assert result["summary"]["comparisons"] == 1
    assert result["summary"]["candidate_pairs"] == 2
    assert result["matches"][0]["polymarket_token_id"] == "token-spain"


def test_promote_discovered_matches_writes_heartbeat_pair_shape() -> None:
    promoted = promote_discovered_matches(
        {
            "matches": [
                {
                    "polymarket_title": "Spain vs Saudi Arabia | Spain",
                    "polymarket_token_id": "token-spain",
                    "polymarket_condition_id": "0xabc",
                    "kalshi_title": "World Cup game: Spain vs Saudi Arabia | Spain",
                    "kalshi_ticker": "KXWCGAME-ESPKSA-ESP",
                    "confidence": 0.93,
                    "price_validation": {"passed": True},
                },
                {
                    "polymarket_title": "Spain vs Saudi Arabia | Draw",
                    "polymarket_token_id": "token-draw",
                    "polymarket_condition_id": "0xdef",
                    "kalshi_title": "World Cup game: Spain vs Saudi Arabia | Tie",
                    "kalshi_ticker": "KXWCGAME-ESPKSA-TIE",
                    "confidence": 0.6,
                    "price_validation": {"passed": True},
                },
                {
                    "polymarket_title": "Spain vs Saudi Arabia | Spain duplicate",
                    "polymarket_token_id": "token-spain",
                    "polymarket_condition_id": "0xabc",
                    "kalshi_title": "World Cup game: Spain vs Saudi Arabia | Spain",
                    "kalshi_ticker": "KXWCGAME-ESPKSA-ESP",
                    "confidence": 0.95,
                    "price_validation": {"passed": True},
                },
            ]
        },
        min_confidence=0.9,
    )

    assert {
        "id",
        "polymarket_slug",
        "polymarket_yes_token_id",
        "polymarket_no_token_id",
        "category",
        "match_notes",
        "blocking_issues",
        "kalshi_normalized",
        "polymarket_normalized",
    } <= promoted["markets"][0].keys()
    assert promoted["markets"][0]["label"] == "Spain vs Saudi Arabia | Spain"
    assert promoted["markets"][0]["kalshi_ticker"] == "KXWCGAME-ESPKSA-ESP"
    assert promoted["markets"][0]["polymarket_token_id"] == "token-spain"
    assert promoted["markets"][0]["polymarket_condition_id"] == "0xabc"
    assert promoted["markets"][0]["side_mapping"] == "same"
    assert promoted["markets"][0]["confidence"] == 0.93
    assert promoted["markets"][0]["match_status"] == "approved"
    assert promoted["markets"][0]["outcome"] == "yes"


def test_promote_discovered_matches_skips_price_warnings_by_default() -> None:
    payload = {
        "matches": [
            {
                "polymarket_title": "Good match",
                "polymarket_token_id": "token-good",
                "kalshi_title": "Good match",
                "kalshi_ticker": "KXGOOD",
                "confidence": 0.93,
                "price_validation": {"passed": True},
            },
            {
                "polymarket_title": "Warning match",
                "polymarket_token_id": "token-warning",
                "kalshi_title": "Warning match",
                "kalshi_ticker": "KXWARNING",
                "confidence": 0.93,
                "price_validation": {"passed": False, "reason": "missing usable bid/ask midpoint"},
            },
        ]
    }

    strict_promoted = promote_discovered_matches(payload, min_confidence=0.9)
    warning_promoted = promote_discovered_matches(
        payload,
        min_confidence=0.9,
        require_price_validation=False,
    )

    assert [market["kalshi_ticker"] for market in strict_promoted["markets"]] == ["KXGOOD"]
    assert [market["kalshi_ticker"] for market in warning_promoted["markets"]] == [
        "KXGOOD",
        "KXWARNING",
    ]


def test_embedding_ranker_scores_semantic_titles_above_unrelated_titles() -> None:
    pytest.importorskip("numpy")
    markets = [
        KalshiDiscoveryMarket(
            ticker="KXWCGAME-BELIRN",
            event_ticker="KXWCGAME",
            title="World Cup game: Belgium vs IR Iran",
            yes_sub_title="Belgium",
            no_sub_title=None,
            close_time=None,
            full_title="World Cup game: Belgium vs IR Iran | Belgium",
        ),
        KalshiDiscoveryMarket(
            ticker="KXWCGAME-ESPKSA",
            event_ticker="KXWCGAME",
            title="World Cup game: Spain vs Saudi Arabia",
            yes_sub_title="Spain",
            no_sub_title=None,
            close_time=None,
            full_title="World Cup game: Spain vs Saudi Arabia | Spain",
        ),
    ]
    polymarket_market = PolymarketDiscoveryMarket(
        event_title="Spain vs Saudi Arabia",
        market_question="Spain vs Saudi Arabia",
        outcome="Spain",
        token_id="token-spain",
        condition_id=None,
        title="Spain vs Saudi Arabia | Spain",
    )
    index = KalshiEmbeddingIndex.build(markets, encoder=FakeEmbeddingEncoder())

    ranked = ranked_kalshi_candidates(polymarket_market, markets, embedding_index=index)

    assert ranked[0][0].ticker == "KXWCGAME-ESPKSA"
    assert ranked[0][1] > ranked[1][1]


def test_build_discovery_candidates_sorts_by_similarity_globally() -> None:
    poly_markets = list_polymarket_discovery_markets(MultiEventPolymarketClient(), event_limit=2)
    kalshi_markets = [kalshi_discovery_market(market) for market in MultiMarketKalshiClient().list_markets()]

    candidates = build_discovery_candidates(
        poly_markets,
        kalshi_markets,
        max_candidates_per_polymarket=1,
        prefilter_threshold=0.1,
    )

    assert candidates[0].polymarket_market.token_id == "token-spain"
    assert candidates[0].kalshi_market.ticker == "KXWCGAME-ESP"
    assert candidates[0].similarity > candidates[1].similarity


def test_candidate_prefilter_rejects_low_semantic_score_without_term_overlap() -> None:
    assert candidate_passes_prefilter(
        semantic_score=0.36,
        lexical_score=0,
        prefilter_threshold=0.05,
    ) is False
    assert candidate_passes_prefilter(
        semantic_score=0.36,
        lexical_score=0.2,
        prefilter_threshold=0.05,
    ) is True


def test_structural_validation_rejects_game_spread_vs_season_win_total() -> None:
    validation = validate_candidate_structure(
        KalshiDiscoveryMarket(
            ticker="KXMLBSPREAD-TEX6",
            event_ticker=None,
            title="Rangers wins by over 5.5 runs?",
            yes_sub_title="Rangers wins by over 5.5 runs",
            no_sub_title=None,
            close_time=None,
            full_title="Rangers wins by over 5.5 runs? | Rangers wins by over 5.5 runs",
        ),
        PolymarketDiscoveryMarket(
            event_title="MLB: 2026 Regular Season Win Totals",
            market_question="Will the New York Yankees win more than 86.5 games in the 2026 MLB Regular Season?",
            outcome="O 86.5",
            token_id="token",
            condition_id=None,
            title=(
                "MLB: 2026 Regular Season Win Totals | Will the New York Yankees win more "
                "than 86.5 games in the 2026 MLB Regular Season? | O 86.5"
            ),
        ),
    )

    assert validation.passed is False
    assert "numeric_threshold_mismatch" in validation.reasons
    assert "market_type_mismatch:game_spread!=season_win_total" in validation.reasons


def test_market_type_detects_polymarket_spread_and_total_contracts() -> None:
    assert (
        market_type("San Diego Padres vs. Texas Rangers | Spread: Texas Rangers (-1.5)")
        == "game_spread"
    )
    assert (
        market_type("San Diego Padres vs. Texas Rangers | San Diego Padres vs. Texas Rangers: O/U 7.5")
        == "game_total"
    )
    assert (
        market_type(
            "San Diego Padres vs. Texas Rangers | San Diego Padres vs. Texas Rangers: "
            "1st 5 Innings O/U 4.5"
        )
        == "first_five_innings"
    )


def test_market_type_does_not_treat_mentions_as_game_winners() -> None:
    assert (
        market_type("What will the announcers say during South Africa vs Canada? | Visa")
        == "mention_market"
    )
    assert market_type("Boston Red Sox vs. Colorado Rockies | Colorado Rockies") == "game_winner"


def test_market_type_detects_season_win_total_without_regular_season_phrase() -> None:
    assert market_type("Will the New York Yankees win more than 86.5 games? | Yes") == "season_win_total"
    assert market_type("Will the New York Yankees win at least 90 games? | Yes") == "season_win_total"
    assert market_type("MLB win total: Yankees over 86.5") == "season_win_total"


def test_market_type_detects_player_prop_ladders() -> None:
    assert market_type("Luka Modric: 2+ assists? | Luka Modric: 2+") == "player_prop"
    assert market_type("Ivan Perisic: score or assist? | Ivan Perisic") == "player_prop"
    assert market_type("World Cup: Player to score | Will Michael Olise score a goal?") == "player_prop"
    assert (
        market_type("Will Ermedin Demirovic record the most assists at the 2026 FIFA World Cup?")
        == "player_prop"
    )


def test_market_type_detects_crypto_threshold_contracts() -> None:
    assert market_type("Will Bitcoin be above $100,000 on June 30? | Yes") == "crypto_threshold"
    assert market_type("BTC hits $120k before July 1") == "crypto_threshold"


def test_crypto_threshold_normalization_extracts_key_fields_and_queries() -> None:
    market = KalshiDiscoveryMarket(
        ticker="KXBTCD-26JUN30-B100K",
        event_ticker="KXBTCD-26JUN30",
        title="Will Bitcoin be above $100,000 on Jun 30?",
        yes_sub_title="Yes",
        no_sub_title="No",
        close_time="2026-06-30T16:00:00Z",
        full_title="Will Bitcoin be above $100,000 on Jun 30? | Yes",
    )

    normalized = normalize_kalshi_market(market)
    queries = kalshi_polymarket_search_queries(market)

    assert normalized.event_type == "crypto_threshold"
    assert normalized.entities == ("bitcoin",)
    assert normalized.comparator == "above"
    assert normalized.threshold == 100000.0
    assert normalized.target_metric == "price"
    assert "bitcoin above 100k" in queries
    assert market.full_title in queries


def test_game_winner_normalization_ignores_tournament_codes_as_thresholds() -> None:
    market = KalshiDiscoveryMarket(
        ticker="KXITFMATCH-26JUN28SANKRU-KRU",
        event_ticker="KXITFMATCH-26JUN28SANKRU",
        title="Will Oleksii Krutykh win the Sanchez Martinez vs Krutykh: M15 Kamen Final match?",
        yes_sub_title="Oleksii Krutykh",
        no_sub_title="Benito Sanchez Martinez",
        close_time="2026-07-12T08:00:00Z",
        expected_expiration_time="2026-06-28T14:00:00Z",
        full_title=(
            "Will Oleksii Krutykh win the Sanchez Martinez vs Krutykh: "
            "M15 Kamen Final match? | Oleksii Krutykh"
        ),
        rules_text=(
            "If Oleksii Krutykh wins the Sanchez Martinez vs Krutykh professional "
            "tennis match in the 2026 M15 Kamen Final after a ball has been played, "
            "then the market resolves to Yes."
        ),
    )

    normalized = normalize_kalshi_market(market)

    assert normalized.event_type == "game_winner"
    assert normalized.threshold is None


def test_structural_validation_accepts_same_match_with_later_settlement_deadline() -> None:
    validation = validate_candidate_structure(
        KalshiDiscoveryMarket(
            ticker="KXITFMATCH-26JUN28SANKRU-KRU",
            event_ticker="KXITFMATCH-26JUN28SANKRU",
            title=(
                "Will Oleksii Krutykh win the Sanchez Martinez vs Krutykh: "
                "M15 Kamen Final match?"
            ),
            yes_sub_title="Oleksii Krutykh",
            no_sub_title="Benito Sanchez Martinez",
            close_time="2026-07-12T08:00:00Z",
            expected_expiration_time="2026-06-28T14:00:00Z",
            full_title=(
                "Will Oleksii Krutykh win the Sanchez Martinez vs Krutykh: "
                "M15 Kamen Final match? | Oleksii Krutykh"
            ),
            rules_text=(
                "If Oleksii Krutykh wins the Sanchez Martinez vs Krutykh professional "
                "tennis match in the 2026 M15 Kamen Final after a ball has been played, "
                "then the market resolves to Yes."
            ),
        ),
        PolymarketDiscoveryMarket(
            event_title="ITF Kamen: Benito Sanchez Martinez vs Oleksii Krutykh",
            market_question="ITF Kamen: Benito Sanchez Martinez vs Oleksii Krutykh",
            outcome="Oleksii Krutykh",
            token_id="token-krutykh",
            condition_id="0xabc",
            title=(
                "ITF Kamen: Benito Sanchez Martinez vs Oleksii Krutykh | "
                "ITF Kamen: Benito Sanchez Martinez vs Oleksii Krutykh | Oleksii Krutykh"
            ),
            slug="itf-benitos-krutykh-2026-06-28",
            end_date="2026-07-05T08:00:00Z",
            description=(
                "This market refers to the tennis match between Benito Sanchez Martinez "
                "and Oleksii Krutykh, originally scheduled for June 28, 2026."
            ),
            tags=("tennis", "sports", "games"),
        ),
    )

    assert validation.passed is True
    assert validation.side_mapping == "same"


def test_structural_validation_requires_same_game_winner_matchup_sides() -> None:
    validation = validate_candidate_structure(
        KalshiDiscoveryMarket(
            ticker="KXCODMAP-26JUN280800LATFAZ-4-FAZ",
            event_ticker="KXCODMAP-26JUN280800LATFAZ",
            title="Will FaZe Vegas win map 4 in the Los Angeles Thieves vs. FaZe Vegas match?",
            yes_sub_title="FaZe Vegas",
            no_sub_title="Los Angeles Thieves",
            close_time="2026-06-28T20:00:00Z",
            full_title=(
                "Will FaZe Vegas win map 4 in the Los Angeles Thieves vs. FaZe Vegas match? | "
                "FaZe Vegas"
            ),
        ),
        PolymarketDiscoveryMarket(
            event_title=(
                "Call of Duty: FaZe Vegas vs Toronto KOI (BO5) - "
                "Call of Duty League Stage 4 Major Playoffs"
            ),
            market_question="Call of Duty: FaZe Vegas vs Toronto KOI - Game 1 Winner",
            outcome="FaZe Vegas",
            token_id="token-faze",
            condition_id="0xabc",
            title=(
                "Call of Duty: FaZe Vegas vs Toronto KOI (BO5) - Call of Duty League "
                "Stage 4 Major Playoffs | Call of Duty: FaZe Vegas vs Toronto KOI - "
                "Game 1 Winner | FaZe Vegas"
            ),
            slug="cod-faze-vegas-toronto-koi-2026-06-28",
            end_date="2026-06-28T22:00:00Z",
        ),
    )

    assert validation.passed is False
    assert "matchup_entity_mismatch" in validation.reasons
    assert "match_scope_mismatch" in validation.reasons


def test_structural_validation_accepts_matching_esports_map_scope() -> None:
    validation = validate_candidate_structure(
        KalshiDiscoveryMarket(
            ticker="KXCODMAP-26JUN280800FAZTOR-1-FAZ",
            event_ticker="KXCODMAP-26JUN280800FAZTOR",
            title="Will FaZe Vegas win map 1 in the FaZe Vegas vs. Toronto KOI match?",
            yes_sub_title="FaZe Vegas",
            no_sub_title="Toronto KOI",
            close_time="2026-06-28T20:00:00Z",
            full_title=(
                "Will FaZe Vegas win map 1 in the FaZe Vegas vs. Toronto KOI match? | "
                "FaZe Vegas"
            ),
        ),
        PolymarketDiscoveryMarket(
            event_title=(
                "Call of Duty: FaZe Vegas vs Toronto KOI (BO5) - "
                "Call of Duty League Stage 4 Major Playoffs"
            ),
            market_question="Call of Duty: FaZe Vegas vs Toronto KOI - Game 1 Winner",
            outcome="FaZe Vegas",
            token_id="token-faze",
            condition_id="0xabc",
            title=(
                "Call of Duty: FaZe Vegas vs Toronto KOI (BO5) - Call of Duty League "
                "Stage 4 Major Playoffs | Call of Duty: FaZe Vegas vs Toronto KOI - "
                "Game 1 Winner | FaZe Vegas"
            ),
            slug="cod-faze-vegas-toronto-koi-2026-06-28",
            end_date="2026-06-28T22:00:00Z",
        ),
    )

    assert validation.passed is True


def test_structural_validation_rejects_map_contract_against_full_match() -> None:
    validation = validate_candidate_structure(
        KalshiDiscoveryMarket(
            ticker="KXDOTA2MAP-26JUN28NIGYEL-2-NIG",
            event_ticker="KXDOTA2MAP-26JUN28NIGYEL",
            title="Will Nigma Galaxy win map 2 in the Yellow Submarine vs. Nigma Galaxy match?",
            yes_sub_title="Nigma Galaxy",
            no_sub_title="Yellow Submarine",
            close_time="2026-06-28T20:00:00Z",
            full_title=(
                "Will Nigma Galaxy win map 2 in the Yellow Submarine vs. Nigma Galaxy match? | "
                "Nigma Galaxy"
            ),
        ),
        PolymarketDiscoveryMarket(
            event_title=(
                "Dota 2: Nigma Galaxy vs Yellow Submarine (BO3) - "
                "The International Europe Closed Qualifier Playoffs"
            ),
            market_question=(
                "Dota 2: Nigma Galaxy vs Yellow Submarine (BO3) - "
                "The International Europe Closed Qualifier Playoffs"
            ),
            outcome="Nigma Galaxy",
            token_id="token-nigma",
            condition_id="0xabc",
            title=(
                "Dota 2: Nigma Galaxy vs Yellow Submarine (BO3) - The International Europe "
                "Closed Qualifier Playoffs | Dota 2: Nigma Galaxy vs Yellow Submarine "
                "(BO3) - The International Europe Closed Qualifier Playoffs | Nigma Galaxy"
            ),
            slug="dota2-nigma-yellow-submarine-2026-06-28",
            end_date="2026-06-28T22:00:00Z",
        ),
    )

    assert validation.passed is False
    assert "match_scope_mismatch" in validation.reasons


def test_structural_validation_rejects_first_half_winner_against_full_game() -> None:
    validation = validate_candidate_structure(
        KalshiDiscoveryMarket(
            ticker="KXWNBA1HWINNER-26JUL02DALCONN-DAL",
            event_ticker="KXWNBA1HWINNER-26JUL02DALCONN",
            title="Dallas vs Connecticut: First Half Winner?",
            yes_sub_title="Dallas wins 1st half",
            no_sub_title="Connecticut wins 1st half",
            close_time="2026-07-17T00:00:00Z",
            expected_expiration_time="2026-07-03T03:00:00Z",
            full_title="Dallas vs Connecticut: First Half Winner? | Dallas wins 1st half",
        ),
        PolymarketDiscoveryMarket(
            event_title="Dallas Wings vs. Connecticut Sun",
            market_question="Dallas Wings vs. Connecticut Sun",
            outcome="Dallas Wings",
            token_id="token-dallas",
            condition_id="0xwnba",
            title=(
                "Dallas Wings vs. Connecticut Sun | Dallas Wings vs. "
                "Connecticut Sun | Dallas Wings"
            ),
            slug="wnba-dal-conn-2026-07-02",
            end_date="2026-07-03T03:00:00Z",
        ),
    )

    assert validation.passed is False
    assert "match_scope_mismatch" in validation.reasons


def test_structural_validation_rejects_exact_set_score_against_match_winner() -> None:
    validation = validate_candidate_structure(
        KalshiDiscoveryMarket(
            ticker="KXATPEXACTMATCH-26JUL04TIABUB-TIA32",
            event_ticker="KXATPEXACTMATCH-26JUL04TIABUB",
            title=(
                "Will Frances Tiafoe win the Frances Tiafoe vs Alexander Bublik match "
                "by a set score of 3-2?"
            ),
            yes_sub_title="Frances Tiafoe wins 3-2",
            no_sub_title=None,
            close_time="2026-07-18T10:00:00Z",
            expected_expiration_time="2026-07-04T13:00:00Z",
            full_title=(
                "Will Frances Tiafoe win the Frances Tiafoe vs Alexander Bublik match "
                "by a set score of 3-2? | Frances Tiafoe wins 3-2"
            ),
        ),
        PolymarketDiscoveryMarket(
            event_title="Wimbledon ATP: Frances Tiafoe vs Alexander Bublik",
            market_question="Wimbledon ATP: Frances Tiafoe vs Alexander Bublik",
            outcome="Frances Tiafoe",
            token_id="token-tiafoe",
            condition_id="0xtennis",
            title=(
                "Wimbledon ATP: Frances Tiafoe vs Alexander Bublik | Wimbledon ATP: "
                "Frances Tiafoe vs Alexander Bublik | Frances Tiafoe"
            ),
            slug="atp-tiafoe-bublik-2026-07-04",
            end_date="2026-07-04T15:00:00Z",
        ),
    )

    assert validation.passed is False
    assert "match_scope_mismatch" in validation.reasons


def test_game_winner_query_generation_uses_matchup_phrase() -> None:
    market = KalshiDiscoveryMarket(
        ticker="KXCODGAME-26JUN28LATOPT-OPT",
        event_ticker="KXCODGAME-26JUN28LATOPT",
        title="Will OpTic Texas win the Los Angeles Thieves vs. OpTic Texas match?",
        yes_sub_title="OpTic Texas",
        no_sub_title=None,
        close_time="2026-06-28T20:00:00Z",
        full_title=(
            "Will OpTic Texas win the Los Angeles Thieves vs. OpTic Texas match? | "
            "OpTic Texas"
        ),
    )

    assert kalshi_polymarket_search_queries(market)[:2] == [
        "Los Angeles Thieves OpTic Texas",
        "Los Angeles Thieves OpTic Texas winner",
    ]


def test_game_winner_query_generation_keeps_matchup_before_colon_scope() -> None:
    market = KalshiDiscoveryMarket(
        ticker="KXWTAMATCH-26JUL04CIRNOS-NOS",
        event_ticker="KXWTAMATCH-26JUL04CIRNOS",
        title="Will Linda Noskova win the Cirstea vs Noskova: Round Of 32 match?",
        yes_sub_title="Linda Noskova",
        no_sub_title="Sorana Cirstea",
        close_time="2026-07-18T10:00:00Z",
        full_title=(
            "Will Linda Noskova win the Cirstea vs Noskova: Round Of 32 match? | "
            "Linda Noskova"
        ),
    )

    assert kalshi_polymarket_search_queries(market)[:3] == [
        "Cirstea Noskova",
        "Cirstea Noskova winner",
        "Cirstea Noskova 2026-07-18",
    ]


def test_structural_validation_rejects_crypto_hit_anytime_vs_deadline() -> None:
    validation = validate_candidate_structure(
        KalshiDiscoveryMarket(
            ticker="KXBTCD-26JUN30-B100K",
            event_ticker="KXBTCD-26JUN30",
            title="Will Bitcoin be above $100,000 on Jun 30?",
            yes_sub_title="Yes",
            no_sub_title="No",
            close_time="2026-06-30T16:00:00Z",
            full_title="Will Bitcoin be above $100,000 on Jun 30? | Yes",
        ),
        PolymarketDiscoveryMarket(
            event_title="Bitcoin reaches $100k before June 30",
            market_question="Will Bitcoin hit $100,000 before June 30?",
            outcome="Yes",
            token_id="token-yes",
            condition_id="0xbtc",
            title="Bitcoin reaches $100k before June 30 | Will Bitcoin hit $100,000 before June 30? | Yes",
            tags=("crypto",),
            end_date="2026-06-30T16:00:00Z",
            slug="bitcoin-100k-2026-06-30",
            outcome_token_ids=(("Yes", "token-yes"), ("No", "token-no")),
        ),
    )

    assert validation.passed is False
    assert "comparator_mismatch" in validation.reasons
    assert "settlement_timing_mismatch" in validation.reasons


def test_market_domain_uses_polymarket_tags_and_kalshi_ticker_context() -> None:
    assert market_domain("Will Eric Barlow win?", tags=("Politics", "Elections")) == "politics"
    assert market_domain("Will Joseph win the Zagreb match?", ticker="KXITFMATCH-ABC") == "sports"


def test_proper_noun_terms_ignore_generic_market_words() -> None:
    assert proper_noun_terms("Will Joseph hernandez win the hernandez vs Facek match?") >= {
        "joseph",
        "hernandez",
        "facek",
    }
    assert "winner" not in proper_noun_terms("Wyoming Governor Republican Primary Winner")


def test_structural_validation_rejects_cross_domain_search_result() -> None:
    validation = validate_candidate_structure(
        KalshiDiscoveryMarket(
            ticker="KXITFMATCH-26JUN22HERFAC-HER",
            event_ticker="KXITFMATCH",
            title="Will Joseph hernandez win the hernandez vs Facek: M25 Zagreb Round of 16 match?",
            yes_sub_title="Joseph hernandez",
            no_sub_title=None,
            close_time=None,
            full_title=(
                "Will Joseph hernandez win the hernandez vs Facek: M25 Zagreb Round of 16 "
                "match? | Joseph hernandez"
            ),
        ),
        PolymarketDiscoveryMarket(
            event_title="Wyoming Governor Republican Primary Winner",
            market_question="Will Eric Barlow win the 2026 Wyoming Governor Republican primary election?",
            outcome="Yes",
            token_id="token",
            condition_id=None,
            title=(
                "Wyoming Governor Republican Primary Winner | Will Eric Barlow win the "
                "2026 Wyoming Governor Republican primary election? | Yes"
            ),
            tags=("politics", "elections"),
        ),
    )

    assert validation.passed is False
    assert "domain_mismatch:sports!=politics" in validation.reasons


def test_structural_validation_rejects_first_name_only_overlap() -> None:
    validation = validate_candidate_structure(
        KalshiDiscoveryMarket(
            ticker="KXITFMATCH-26JUN22HERFAC-HER",
            event_ticker="KXITFMATCH",
            title="Will Joseph hernandez win the hernandez vs Facek: M25 Zagreb Round of 16 match?",
            yes_sub_title="Joseph hernandez",
            no_sub_title=None,
            close_time=None,
            full_title=(
                "Will Joseph hernandez win the hernandez vs Facek: M25 Zagreb Round of 16 "
                "match? | Joseph hernandez"
            ),
        ),
        PolymarketDiscoveryMarket(
            event_title="Wyoming Governor Republican Primary Winner",
            market_question="Will Joseph Kibler win the 2026 Wyoming Governor Republican primary election?",
            outcome="Yes",
            token_id="token",
            condition_id=None,
            title=(
                "Wyoming Governor Republican Primary Winner | Will Joseph Kibler win the "
                "2026 Wyoming Governor Republican primary election? | Yes"
            ),
            tags=("sports",),
        ),
    )

    assert validation.passed is False
    assert "weak_proper_noun_overlap" in validation.reasons


def test_structural_validation_rejects_wrong_game_winner_outcome_side() -> None:
    polymarket_market = PolymarketDiscoveryMarket(
        event_title="Houston Astros vs. Toronto Blue Jays",
        market_question="Houston Astros vs. Toronto Blue Jays",
        outcome="Houston Astros",
        token_id="token",
        condition_id=None,
        title="Houston Astros vs. Toronto Blue Jays | Houston Astros vs. Toronto Blue Jays | Houston Astros",
        tags=("sports", "mlb", "baseball"),
    )

    toronto_validation = validate_candidate_structure(
        KalshiDiscoveryMarket(
            ticker="KXMLBGAME-TOR",
            event_ticker="KXMLBGAME",
            title="Houston vs Toronto Winner?",
            yes_sub_title="Toronto",
            no_sub_title=None,
            close_time=None,
            full_title="Houston vs Toronto Winner? | Toronto",
        ),
        polymarket_market,
    )
    houston_validation = validate_candidate_structure(
        KalshiDiscoveryMarket(
            ticker="KXMLBGAME-HOU",
            event_ticker="KXMLBGAME",
            title="Houston vs Toronto Winner?",
            yes_sub_title="Houston",
            no_sub_title=None,
            close_time=None,
            full_title="Houston vs Toronto Winner? | Houston",
        ),
        polymarket_market,
    )

    assert "outcome_entity_mismatch" in toronto_validation.reasons
    assert houston_validation.passed is True


def test_date_validation_prefers_exact_dates_encoded_in_market_ids() -> None:
    validation = validate_candidate_dates(
        KalshiDiscoveryMarket(
            ticker="KXWNBAGAME-26JUL08LAIND-LA",
            event_ticker="KXWNBAGAME-26JUL08LAIND",
            title="Los Angeles vs Indiana winner?",
            yes_sub_title="Los Angeles",
            no_sub_title=None,
            close_time="2026-07-09T02:00:00Z",
            expected_expiration_time="2026-07-09T02:00:00Z",
            full_title="Los Angeles vs Indiana winner? | Los Angeles",
        ),
        PolymarketDiscoveryMarket(
            event_title="Los Angeles Sparks vs. Indiana Fever",
            market_question="Los Angeles Sparks vs. Indiana Fever",
            outcome="Los Angeles Sparks",
            token_id="token",
            condition_id=None,
            title=(
                "Los Angeles Sparks vs. Indiana Fever | Los Angeles Sparks vs. "
                "Indiana Fever | Los Angeles Sparks"
            ),
            slug="wnba-los-angeles-sparks-indiana-fever-2026-07-08",
            end_date="2026-07-09T02:00:00Z",
        ),
    )

    assert validation.passed is True
    assert validation.kalshi_date == "2026-07-08"
    assert validation.polymarket_date == "2026-07-08"
    assert validation.reason == "contract dates aligned"


def test_structural_validation_rejects_same_matchup_on_different_contract_date() -> None:
    validation = validate_candidate_structure(
        KalshiDiscoveryMarket(
            ticker="KXWNBAGAME-26JUL08LAIND-LA",
            event_ticker="KXWNBAGAME-26JUL08LAIND",
            title="Los Angeles vs Indiana winner?",
            yes_sub_title="Los Angeles",
            no_sub_title=None,
            close_time="2026-07-09T02:00:00Z",
            full_title="Los Angeles vs Indiana winner? | Los Angeles",
        ),
        PolymarketDiscoveryMarket(
            event_title="Los Angeles Sparks vs. Indiana Fever",
            market_question="Los Angeles Sparks vs. Indiana Fever",
            outcome="Los Angeles Sparks",
            token_id="token",
            condition_id=None,
            title=(
                "Los Angeles Sparks vs. Indiana Fever | Los Angeles Sparks vs. "
                "Indiana Fever | Los Angeles Sparks"
            ),
            slug="wnba-los-angeles-sparks-indiana-fever-2026-07-09",
            end_date="2026-07-09T02:00:00Z",
        ),
    )

    assert validation.passed is False
    assert (
        "date_mismatch:contract dates differ 2026-07-08!=2026-07-09"
        in validation.reasons
    )


def test_structural_validation_rejects_extra_innings_vs_other_prop() -> None:
    validation = validate_candidate_structure(
        KalshiDiscoveryMarket(
            ticker="KXMLBEXTRAS",
            event_ticker=None,
            title="Will there be extra innings in the Boston vs Colorado game?",
            yes_sub_title="Game goes to extra innings",
            no_sub_title=None,
            close_time=None,
            full_title=(
                "Will there be extra innings in the Boston vs Colorado game? | "
                "Game goes to extra innings"
            ),
        ),
        PolymarketDiscoveryMarket(
            event_title="Will there be a hole in one at the 2026 U.S. Open?",
            market_question="Will there be a hole in one at the 2026 U.S. Open?",
            outcome="Yes",
            token_id="token",
            condition_id=None,
            title=(
                "Will there be a hole in one at the 2026 U.S. Open? | "
                "Will there be a hole in one at the 2026 U.S. Open? | Yes"
            ),
        ),
    )

    assert validation.passed is False
    assert "market_type_mismatch:extra_innings!=unknown" in validation.reasons


def test_structural_validation_allows_matching_game_winner_entities() -> None:
    validation = validate_candidate_structure(
        KalshiDiscoveryMarket(
            ticker="KXMLBGAME-COL",
            event_ticker=None,
            title="Boston vs Colorado Winner?",
            yes_sub_title="Colorado",
            no_sub_title=None,
            close_time=None,
            full_title="Boston vs Colorado Winner? | Colorado",
        ),
        PolymarketDiscoveryMarket(
            event_title="Boston Red Sox vs. Colorado Rockies",
            market_question="Boston Red Sox vs. Colorado Rockies",
            outcome="Colorado Rockies",
            token_id="token",
            condition_id=None,
            title=(
                "Boston Red Sox vs. Colorado Rockies | Boston Red Sox vs. "
                "Colorado Rockies | Colorado Rockies"
            ),
        ),
    )

    assert validation.passed is True
    assert "boston" in validation.shared_entities
    assert "colorado" in validation.shared_entities


def test_validate_candidate_prices_passes_when_midpoints_are_close() -> None:
    candidate = build_discovery_candidates(
        list_polymarket_discovery_markets(FakePolymarketClient(), event_limit=1),
        [kalshi_discovery_market(FakeKalshiClient().list_markets()[0])],
        max_candidates_per_polymarket=1,
        prefilter_threshold=0.1,
    )[0]

    validation = validate_candidate_prices(
        candidate,
        FakePriceKalshiClient(yes_bid="0.49", no_bid="0.49"),
        FakePricePolymarketClient(bid="0.50", ask="0.52"),
        threshold=Decimal("0.03"),
    )

    assert validation.passed is True
    assert validation.difference == Decimal("0.01")


def test_validate_candidate_prices_rejects_when_midpoints_are_far_apart() -> None:
    candidate = build_discovery_candidates(
        list_polymarket_discovery_markets(FakePolymarketClient(), event_limit=1),
        [kalshi_discovery_market(FakeKalshiClient().list_markets()[0])],
        max_candidates_per_polymarket=1,
        prefilter_threshold=0.1,
    )[0]

    validation = validate_candidate_prices(
        candidate,
        FakePriceKalshiClient(yes_bid="0.49", no_bid="0.49"),
        FakePricePolymarketClient(bid="0.58", ask="0.60"),
        threshold=Decimal("0.03"),
    )

    assert validation.passed is False
    assert validation.difference == Decimal("0.09")


def test_lexical_overlap_scores_related_titles_above_unrelated_titles() -> None:
    related = lexical_overlap("Spain vs Saudi Arabia Spain", "World Cup Spain Saudi Arabia")
    unrelated = lexical_overlap("Spain vs Saudi Arabia", "Belgium vs Iran")

    assert related > unrelated
