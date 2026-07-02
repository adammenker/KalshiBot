from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from kalshibot.client import KalshiClient
from kalshibot.discovery.candidates import (
    build_discovery_candidates,
    build_polymarket_search_candidates,
)
from kalshibot.discovery.embeddings import load_or_build_kalshi_embedding_index
from kalshibot.discovery.flow import (
    build_candidate_review_records,
    build_filter_hit_counts,
    build_flow_samples,
    build_structural_reason_counts,
    polymarket_search_result_count,
    price_validation_summary,
)
from kalshibot.discovery.models import DiscoveryMatch
from kalshibot.discovery.sizing import (
    DEFAULT_KALSHI_SIZE_SORT,
    normalize_kalshi_size_sort,
    sort_kalshi_markets_by_size,
)
from kalshibot.discovery.sources import (
    list_kalshi_discovery_markets,
    list_polymarket_discovery_markets,
    unique_polymarket_markets_from_candidates,
)
from kalshibot.discovery.taxonomy import market_type
from kalshibot.discovery.utils import sorted_matches
from kalshibot.discovery.validation import (
    deterministic_candidate_match,
    validate_candidate_dates,
    validate_candidate_prices,
)
from kalshibot.market_matcher import (
    OllamaTitleMatcher,
    TitleMatcherLLM,
)
from kalshibot.market_urls import kalshi_market_url, polymarket_market_url
from kalshibot.polymarket import PolymarketClient
from kalshibot.utils import optional_string


def discover_market_matches(
    *,
    polymarket_client: PolymarketClient,
    kalshi_client: KalshiClient,
    llm: TitleMatcherLLM | OllamaTitleMatcher | None,
    use_llm: bool,
    confidence_threshold: float,
    polymarket_event_limit: int,
    kalshi_limit: int,
    kalshi_pages: int,
    kalshi_status: str,
    kalshi_series_ticker: str | None,
    max_candidates_per_polymarket: int,
    max_comparisons: int | None,
    prefilter_threshold: float,
    kalshi_fetch_limit: int | None = None,
    index_path: Path | None = None,
    discovery_strategy: str = "broad",
    polymarket_search_limit: int = 10,
    max_polymarket_contracts_per_event: int | None = 40,
    polymarket_outcome_filter: str = "any",
    kalshi_include_series: set[str] | None = None,
    kalshi_exclude_series: set[str] | None = None,
    kalshi_market_types: set[str] | None = None,
    include_search_debug: bool = False,
    price_validation_threshold: Decimal | None = None,
    reject_on_price_validation: bool = False,
    flow_summary_limit: int = 0,
    flow_candidates_per_kalshi: int = 3,
    kalshi_size_sort_by: str = DEFAULT_KALSHI_SIZE_SORT,
    min_match_date: str | None = None,
    max_match_date: str | None = None,
) -> dict[str, Any]:
    effective_kalshi_fetch_limit = kalshi_fetch_limit or kalshi_limit
    normalized_kalshi_size_sort = normalize_kalshi_size_sort(kalshi_size_sort_by)
    kalshi_markets = list_kalshi_discovery_markets(
        kalshi_client,
        limit=effective_kalshi_fetch_limit,
        pages=kalshi_pages,
        status=kalshi_status,
        series_ticker=kalshi_series_ticker,
        include_series=kalshi_include_series,
        exclude_series=kalshi_exclude_series,
    )
    fetched_kalshi_markets = len(kalshi_markets)
    if kalshi_market_types:
        kalshi_markets = [
            kalshi_market
            for kalshi_market in kalshi_markets
            if market_type(kalshi_market.full_title) in kalshi_market_types
        ]
    filtered_kalshi_markets = len(kalshi_markets)
    kalshi_markets = sort_kalshi_markets_by_size(
        kalshi_markets,
        sort_by=normalized_kalshi_size_sort,
    )[:kalshi_limit]
    search_debug: list[dict[str, Any]] = []
    if discovery_strategy == "polymarket-search":
        candidates = build_polymarket_search_candidates(
            polymarket_client,
            kalshi_markets,
            max_candidates_per_kalshi=max_candidates_per_polymarket,
            prefilter_threshold=prefilter_threshold,
            search_limit=polymarket_search_limit,
            max_contracts_per_event=max_polymarket_contracts_per_event,
            outcome_filter=polymarket_outcome_filter,
            debug_rows=search_debug,
        )
        polymarket_markets = unique_polymarket_markets_from_candidates(candidates)
    elif discovery_strategy == "broad":
        polymarket_markets = list_polymarket_discovery_markets(
            polymarket_client,
            event_limit=polymarket_event_limit,
        )
        embedding_index = load_or_build_kalshi_embedding_index(
            kalshi_markets,
            index_path=index_path,
        )
        candidates = build_discovery_candidates(
            polymarket_markets,
            kalshi_markets,
            max_candidates_per_polymarket=max_candidates_per_polymarket,
            prefilter_threshold=prefilter_threshold,
            embedding_index=embedding_index,
        )
    else:
        raise ValueError("discovery_strategy must be 'polymarket-search' or 'broad'")
    matches: list[DiscoveryMatch] = []
    comparisons = 0
    llm_candidates = 0
    llm_skipped = 0
    price_validations = 0
    price_validation_rejections = 0
    stale_match_rejections = 0
    future_match_rejections = 0
    deterministic_matches = 0
    flow_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    matched_kalshi_tickers: set[str] = set()
    if use_llm and llm is None:
        raise ValueError("llm is required when use_llm is true")
    for candidate in candidates:
        if candidate.kalshi_market.ticker in matched_kalshi_tickers:
            continue
        if use_llm and max_comparisons is not None and comparisons >= max_comparisons:
            break
        flow_key = (candidate.polymarket_market.token_id, candidate.kalshi_market.ticker)
        flow_by_pair[flow_key] = {
            "reached_price_validation": price_validation_threshold is not None,
            "passed_price_validation": price_validation_threshold is None,
            "reached_llm_gate": False,
            "llm_called": False,
            "kept_as_match": False,
            "reject_on_price_validation": reject_on_price_validation,
        }
        date_validation = validate_candidate_dates(candidate.kalshi_market, candidate.polymarket_market)
        flow_by_pair[flow_key]["date_validation"] = asdict(date_validation)
        if match_is_before_min_date(date_validation.kalshi_date, date_validation.polymarket_date, min_match_date):
            stale_match_rejections += 1
            flow_by_pair[flow_key]["stale_match_rejection"] = True
            continue
        if match_is_after_max_date(date_validation.kalshi_date, date_validation.polymarket_date, max_match_date):
            future_match_rejections += 1
            flow_by_pair[flow_key]["future_match_rejection"] = True
            continue
        candidate_price_validation: dict[str, Any] | None = None
        if price_validation_threshold is not None:
            price_validations += 1
            price_validation = validate_candidate_prices(
                candidate,
                kalshi_client,
                polymarket_client,
                threshold=price_validation_threshold,
            )
            candidate_price_validation = price_validation_summary(price_validation)
            flow_by_pair[flow_key]["price_validation"] = candidate_price_validation
            if not price_validation.passed:
                price_validation_rejections += 1
                if reject_on_price_validation:
                    continue
            else:
                flow_by_pair[flow_key]["passed_price_validation"] = True
        else:
            flow_by_pair[flow_key]["passed_price_validation"] = True
        llm_candidates += 1
        flow_by_pair[flow_key]["reached_llm_gate"] = True
        deterministic_match = deterministic_candidate_match(candidate)
        if deterministic_match is not None:
            confidence, reason, method = deterministic_match
            deterministic_matches += 1
            flow_by_pair[flow_key]["kept_as_match"] = True
            flow_by_pair[flow_key]["deterministic_result"] = {
                "confidence": confidence,
                "method": method,
                "reason": reason,
            }
            matched_kalshi_tickers.add(candidate.kalshi_market.ticker)
            matches.append(
                discovery_match_from_candidate(
                    candidate,
                    confidence,
                    reason,
                    method,
                    price_validation=candidate_price_validation,
                    date_validation=asdict(date_validation),
                )
            )
            continue
        if not use_llm:
            llm_skipped += 1
            continue
        comparisons += 1
        flow_by_pair[flow_key]["llm_called"] = True
        result = llm.match_titles(
            candidate.polymarket_market.title,
            candidate.kalshi_market.full_title,
        )
        flow_by_pair[flow_key]["llm_result"] = {
            "same_market": result.same_market,
            "is_same_event": result.is_same_event,
            "confidence": result.confidence,
            "method": result.method,
            "reason": result.reason,
            "side_mapping": result.side_mapping,
            "blocking_issues": list(result.blocking_issues),
            "differences": list(result.differences),
        }
        if result.same_market and result.confidence >= confidence_threshold:
            flow_by_pair[flow_key]["kept_as_match"] = True
            matched_kalshi_tickers.add(candidate.kalshi_market.ticker)
            matches.append(
                discovery_match_from_candidate(
                    candidate,
                    result.confidence,
                    result.reason,
                    result.method,
                    price_validation=candidate_price_validation,
                    date_validation=asdict(date_validation),
                )
            )
    stopped_early = (
        use_llm
        and max_comparisons is not None
        and comparisons >= max_comparisons
        and comparisons < len(candidates)
    )
    filter_hits = build_filter_hit_counts(
        search_debug=search_debug,
        flow_by_pair=flow_by_pair,
    )
    structural_reason_hits = build_structural_reason_counts(search_debug)
    maybe_matches, rejected_matches = build_candidate_review_records(
        search_debug=search_debug,
        flow_by_pair=flow_by_pair,
    )

    result = {
        "summary": {
            "polymarket_markets": len(polymarket_markets),
            "polymarket_search_results": polymarket_search_result_count(search_debug),
            "kalshi_markets": len(kalshi_markets),
            "kalshi_markets_fetched": fetched_kalshi_markets,
            "kalshi_markets_after_filters": filtered_kalshi_markets,
            "kalshi_fetch_limit": effective_kalshi_fetch_limit,
            "kalshi_size_sort_by": normalized_kalshi_size_sort,
            "kalshi_include_series": sorted(kalshi_include_series or []),
            "kalshi_exclude_series": sorted(kalshi_exclude_series or []),
            "kalshi_market_types": sorted(kalshi_market_types or []),
            "candidate_pairs": len(candidates),
            "filter_hits": filter_hits,
            "structural_reason_hits": structural_reason_hits,
            "maybe_matches": len(maybe_matches),
            "rejected_matches": len(rejected_matches),
            "comparisons": comparisons,
            "matches": len(matches),
            "price_validation_threshold": (
                str(price_validation_threshold) if price_validation_threshold is not None else None
            ),
            "price_validations": price_validations,
            "price_validation_rejections": price_validation_rejections,
            "stale_match_rejections": stale_match_rejections,
            "future_match_rejections": future_match_rejections,
            "min_match_date": min_match_date,
            "max_match_date": max_match_date,
            "price_validation_mode": (
                "off"
                if price_validation_threshold is None
                else "reject"
                if reject_on_price_validation
                else "warn"
            ),
            "deterministic_matches": deterministic_matches,
            "discovery_strategy": discovery_strategy,
            "confidence_threshold": confidence_threshold,
            "prefilter_threshold": prefilter_threshold,
            "max_candidates_per_polymarket": max_candidates_per_polymarket,
            "max_comparisons": max_comparisons,
            "llm_enabled": use_llm,
            "llm_candidates": llm_candidates,
            "llm_skipped": llm_skipped,
            "polymarket_search_limit": polymarket_search_limit,
            "max_polymarket_contracts_per_event": max_polymarket_contracts_per_event,
            "polymarket_outcome_filter": polymarket_outcome_filter,
            "flow_candidates_per_kalshi": flow_candidates_per_kalshi,
            "stopped_early": stopped_early,
        },
        "matches": [asdict(match) for match in sorted_matches(matches)],
        "maybe_matches": maybe_matches,
        "rejected_matches": rejected_matches,
    }
    if include_search_debug:
        result["search_debug"] = search_debug
    if flow_summary_limit > 0:
        result["flow_samples"] = build_flow_samples(
            search_debug=search_debug,
            flow_by_pair=flow_by_pair,
            limit=flow_summary_limit,
            candidates_per_kalshi=flow_candidates_per_kalshi,
        )
    return result


def discovery_match_from_candidate(
    candidate: Any,
    confidence: float,
    reason: str,
    method: str,
    *,
    price_validation: dict[str, Any] | None = None,
    date_validation: dict[str, Any] | None = None,
) -> DiscoveryMatch:
    from kalshibot.discovery.validation import validate_candidate_structure

    structural = validate_candidate_structure(candidate.kalshi_market, candidate.polymarket_market)
    date_validation = date_validation or asdict(
        validate_candidate_dates(candidate.kalshi_market, candidate.polymarket_market)
    )
    polymarket_yes_token_id = candidate.polymarket_market.token_id
    polymarket_no_token_id = candidate.polymarket_market.sibling_token_id
    if structural.side_mapping == "inverse" and candidate.polymarket_market.sibling_token_id:
        polymarket_yes_token_id = candidate.polymarket_market.sibling_token_id
        polymarket_no_token_id = candidate.polymarket_market.token_id
    return DiscoveryMatch(
        polymarket_title=candidate.polymarket_market.title,
        polymarket_token_id=candidate.polymarket_market.token_id,
        polymarket_condition_id=candidate.polymarket_market.condition_id,
        kalshi_title=candidate.kalshi_market.full_title,
        kalshi_ticker=candidate.kalshi_market.ticker,
        confidence=confidence,
        reason=reason,
        method=method,
        side_mapping=structural.side_mapping,
        match_status="approved",
        category=structural.kalshi_market_type,
        polymarket_slug=candidate.polymarket_market.slug,
        polymarket_yes_token_id=polymarket_yes_token_id,
        polymarket_no_token_id=polymarket_no_token_id,
        match_notes=structural.match_notes,
        blocking_issues=structural.blocking_issues,
        kalshi_normalized=structural.kalshi_normalized,
        polymarket_normalized=structural.polymarket_normalized,
        date_validation=date_validation,
        price_validation=price_validation,
    )


def match_is_before_min_date(
    kalshi_date: str | None,
    polymarket_date: str | None,
    min_match_date: str | None,
) -> bool:
    if min_match_date is None:
        return False
    match_date = kalshi_date or polymarket_date
    return bool(match_date and match_date < min_match_date)


def match_is_after_max_date(
    kalshi_date: str | None,
    polymarket_date: str | None,
    max_match_date: str | None,
) -> bool:
    if max_match_date is None:
        return False
    match_date = kalshi_date or polymarket_date
    return bool(match_date and match_date > max_match_date)


def promote_discovered_matches(
    discovery_payload: dict[str, Any],
    *,
    min_confidence: float,
    require_price_validation: bool = True,
) -> dict[str, Any]:
    promoted: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    matches = discovery_payload.get("matches", [])
    if not isinstance(matches, list):
        raise ValueError("Discovery payload must contain a 'matches' list")

    for match in matches:
        if not isinstance(match, dict):
            continue
        if str(match.get("match_status") or "approved") != "approved":
            continue
        confidence = float(match.get("confidence") or 0)
        if confidence < min_confidence:
            continue
        price_validation = match.get("price_validation")
        if require_price_validation and not (
            isinstance(price_validation, dict) and price_validation.get("passed") is True
        ):
            continue
        kalshi_ticker = str(match.get("kalshi_ticker") or "")
        polymarket_token_id = str(
            match.get("polymarket_yes_token_id") or match.get("polymarket_token_id") or ""
        )
        if not kalshi_ticker or not polymarket_token_id:
            continue
        key = (kalshi_ticker, polymarket_token_id)
        if key in seen:
            continue
        seen.add(key)
        promoted.append(
            {
                "label": str(match.get("polymarket_title") or match.get("kalshi_title") or kalshi_ticker),
                "id": market_pair_id(match, kalshi_ticker),
                "kalshi_ticker": kalshi_ticker,
                "kalshi_url": kalshi_market_url(kalshi_ticker),
                "polymarket_token_id": polymarket_token_id,
                "polymarket_url": polymarket_market_url(
                    slug=optional_string(match.get("polymarket_slug")),
                    token_id=polymarket_token_id,
                ),
                "polymarket_slug": optional_string(match.get("polymarket_slug")),
                "polymarket_condition_id": optional_string(match.get("polymarket_condition_id")),
                "polymarket_yes_token_id": optional_string(
                    match.get("polymarket_yes_token_id") or polymarket_token_id
                ),
                "polymarket_no_token_id": optional_string(match.get("polymarket_no_token_id")),
                "side_mapping": str(match.get("side_mapping") or "same"),
                "category": optional_string(match.get("category")),
                "confidence": confidence,
                "match_status": "approved",
                "target_size": "1",
                "match_notes": list(match.get("match_notes") or []),
                "blocking_issues": list(match.get("blocking_issues") or []),
                "kalshi_normalized": match.get("kalshi_normalized"),
                "polymarket_normalized": match.get("polymarket_normalized"),
                "outcome": "yes",
            }
        )

    return {"markets": promoted}


def market_pair_id(match: dict[str, Any], kalshi_ticker: str) -> str:
    base = str(match.get("polymarket_slug") or match.get("kalshi_title") or kalshi_ticker)
    normalized = "".join(character.lower() if character.isalnum() else "_" for character in base)
    return "_".join(part for part in normalized.split("_") if part)[:120] or kalshi_ticker.lower()
