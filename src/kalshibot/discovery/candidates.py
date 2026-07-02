from __future__ import annotations

from dataclasses import asdict
from typing import Any

from kalshibot.discovery.embeddings import KalshiEmbeddingIndex
from kalshibot.discovery.models import (
    DiscoveryCandidate,
    KalshiDiscoveryMarket,
    PolymarketDiscoveryMarket,
)
from kalshibot.discovery.scoring import (
    candidate_passes_prefilter,
    hybrid_similarity,
    lexical_overlap,
)
from kalshibot.discovery.normalization import normalize_kalshi_market
from kalshibot.discovery.queries import kalshi_polymarket_search_queries
from kalshibot.discovery.sizing import kalshi_market_size_summary
from kalshibot.discovery.sources import search_polymarket_discovery_markets
from kalshibot.discovery.validation import validate_candidate_dates, validate_candidate_structure
from kalshibot.polymarket import PolymarketClient


def ranked_kalshi_candidates(
    polymarket_market: PolymarketDiscoveryMarket,
    kalshi_markets: list[KalshiDiscoveryMarket],
    *,
    embedding_index: KalshiEmbeddingIndex | None = None,
) -> list[tuple[KalshiDiscoveryMarket, float]]:
    if embedding_index is not None:
        return embedding_index.search(polymarket_market, kalshi_markets)
    ranked = [
        (kalshi_market, lexical_overlap(polymarket_market.title, kalshi_market.full_title))
        for kalshi_market in kalshi_markets
    ]
    return sorted(ranked, key=lambda item: item[1], reverse=True)


def build_discovery_candidates(
    polymarket_markets: list[PolymarketDiscoveryMarket],
    kalshi_markets: list[KalshiDiscoveryMarket],
    *,
    max_candidates_per_polymarket: int,
    prefilter_threshold: float,
    embedding_index: KalshiEmbeddingIndex | None = None,
) -> list[DiscoveryCandidate]:
    candidates: list[DiscoveryCandidate] = []
    for poly_market in polymarket_markets:
        poly_candidate_count = 0
        ranked = ranked_kalshi_candidates(
            poly_market,
            kalshi_markets,
            embedding_index=embedding_index,
        )
        for kalshi_market, score in ranked:
            lexical_score = lexical_overlap(poly_market.title, kalshi_market.full_title)
            structural = validate_candidate_structure(kalshi_market, poly_market)
            if not candidate_passes_prefilter(score, lexical_score, prefilter_threshold):
                continue
            if not structural.passed:
                continue
            candidates.append(
                DiscoveryCandidate(
                    polymarket_market=poly_market,
                    kalshi_market=kalshi_market,
                    similarity=hybrid_similarity(score, lexical_score),
                )
            )
            poly_candidate_count += 1
            if poly_candidate_count >= max_candidates_per_polymarket:
                break

    return sorted(
        candidates,
        key=lambda candidate: (
            -candidate.similarity,
            candidate.polymarket_market.title,
            candidate.kalshi_market.ticker,
        ),
    )


def build_polymarket_search_candidates(
    client: PolymarketClient,
    kalshi_markets: list[KalshiDiscoveryMarket],
    *,
    max_candidates_per_kalshi: int,
    prefilter_threshold: float,
    search_limit: int,
    max_contracts_per_event: int | None,
    outcome_filter: str = "any",
    debug_rows: list[dict[str, Any]] | None = None,
) -> list[DiscoveryCandidate]:
    candidates: list[DiscoveryCandidate] = []
    seen_pairs: set[tuple[str, str]] = set()
    for kalshi_market in kalshi_markets:
        queries = kalshi_polymarket_search_queries(kalshi_market)
        search_markets = search_polymarket_candidates_for_queries(
            client,
            queries=queries,
            limit=search_limit,
        )
        event_contract_counts = polymarket_event_contract_counts(search_markets)
        search_debug_row = search_debug_row_for_kalshi_market(kalshi_market, search_markets, queries)
        kalshi_candidate_count = 0
        for rank, poly_market in enumerate(search_markets):
            event_contract_count = event_contract_counts.get(poly_market.event_title, 1)
            nested_event_skipped = (
                max_contracts_per_event is not None
                and event_contract_count > max_contracts_per_event
            )
            outcome_filter_passed = polymarket_outcome_passes_filter(
                poly_market,
                outcome_filter=outcome_filter,
            )
            rank_score = max(0.0, 1.0 - (rank * 0.03))
            lexical_score = lexical_overlap(poly_market.title, kalshi_market.full_title)
            structural = validate_candidate_structure(kalshi_market, poly_market)
            date_validation = validate_candidate_dates(kalshi_market, poly_market)
            passes_prefilter = candidate_passes_prefilter(
                rank_score,
                lexical_score,
                prefilter_threshold,
            )
            result_debug = search_debug_row["polymarket_results"][rank]
            result_debug["rank_score"] = round(rank_score, 4)
            result_debug["lexical_score"] = round(lexical_score, 4)
            result_debug["hybrid_similarity"] = round(
                hybrid_similarity(rank_score, lexical_score),
                4,
            )
            result_debug["passes_prefilter"] = passes_prefilter
            result_debug["structural_validation"] = asdict(structural)
            result_debug["date_validation"] = asdict(date_validation)
            result_debug["structural_score"] = structural.score
            result_debug["side_mapping"] = structural.side_mapping
            result_debug["blocking_issues"] = list(structural.blocking_issues)
            result_debug["event_contract_count"] = event_contract_count
            result_debug["skipped_nested_event"] = nested_event_skipped
            result_debug["outcome_filter"] = outcome_filter
            result_debug["outcome_filter_passed"] = outcome_filter_passed
            if nested_event_skipped:
                result_debug["nesting_reason"] = (
                    f"event has {event_contract_count} contracts; cap is {max_contracts_per_event}"
                )
            if (
                nested_event_skipped
                or not outcome_filter_passed
                or not passes_prefilter
                or not structural.passed
            ):
                continue
            if kalshi_candidate_count >= max_candidates_per_kalshi:
                continue
            key = (poly_market.token_id, kalshi_market.ticker)
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            candidates.append(
                DiscoveryCandidate(
                    polymarket_market=poly_market,
                    kalshi_market=kalshi_market,
                    similarity=hybrid_similarity(rank_score, lexical_score),
                )
            )
            kalshi_candidate_count += 1
        search_debug_row["candidate_count"] = kalshi_candidate_count
        if debug_rows is not None:
            debug_rows.append(search_debug_row)

    return sorted(
        candidates,
        key=lambda candidate: (
            -candidate.similarity,
            candidate.kalshi_market.ticker,
            candidate.polymarket_market.title,
        ),
    )


def search_polymarket_candidates_for_queries(
    client: PolymarketClient,
    *,
    queries: list[str],
    limit: int,
) -> list[PolymarketDiscoveryMarket]:
    markets: list[PolymarketDiscoveryMarket] = []
    seen: set[str] = set()
    for query in queries:
        for market in search_polymarket_discovery_markets(client, query=query, limit=limit):
            if market.token_id in seen:
                continue
            seen.add(market.token_id)
            markets.append(market)
    return markets


def polymarket_outcome_passes_filter(
    market: PolymarketDiscoveryMarket,
    *,
    outcome_filter: str,
) -> bool:
    if outcome_filter == "any":
        return True
    if outcome_filter == "yes-no":
        return market.outcome.strip().lower() in {"yes", "no"}
    raise ValueError("outcome_filter must be 'any' or 'yes-no'")


def polymarket_event_contract_counts(
    markets: list[PolymarketDiscoveryMarket],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for market in markets:
        counts[market.event_title] = counts.get(market.event_title, 0) + 1
    return counts


def search_debug_row_for_kalshi_market(
    kalshi_market: KalshiDiscoveryMarket,
    search_markets: list[PolymarketDiscoveryMarket],
    queries: list[str] | None = None,
) -> dict[str, Any]:
    queries = queries or [kalshi_market.full_title]
    return {
        "kalshi": {
            "ticker": kalshi_market.ticker,
            "event_ticker": kalshi_market.event_ticker,
            "title": kalshi_market.title,
            "yes_sub_title": kalshi_market.yes_sub_title,
            "no_sub_title": kalshi_market.no_sub_title,
            "full_title": kalshi_market.full_title,
            "expected_expiration_time": kalshi_market.expected_expiration_time,
            "expiration_time": kalshi_market.expiration_time,
            "close_time": kalshi_market.close_time,
            "size": kalshi_market_size_summary(kalshi_market),
            "normalized": asdict(normalize_kalshi_market(kalshi_market)),
        },
        "query": queries[0],
        "queries": queries,
        "polymarket_result_count": len(search_markets),
        "candidate_count": 0,
        "polymarket_results": [
            {
                "rank": index + 1,
                "title": market.title,
                "event_title": market.event_title,
                "market_question": market.market_question,
                "outcome": market.outcome,
                "token_id": market.token_id,
                "condition_id": market.condition_id,
                "start_date": market.start_date,
                "end_date": market.end_date,
                "slug": market.slug,
                "sibling_token_id": market.sibling_token_id,
                "outcome_token_ids": [list(item) for item in market.outcome_token_ids],
                "tags": list(market.tags),
            }
            for index, market in enumerate(search_markets)
        ],
    }
