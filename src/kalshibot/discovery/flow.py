from __future__ import annotations

from typing import Any

from kalshibot.utils import optional_string


FILTER_HIT_ORDER = (
    "nested_event",
    "outcome_filter",
    "prefilter",
    "date",
    "stale_date",
    "future_date",
    "structure",
    "price_validation",
    "llm",
    "llm_not_run",
    "not_selected",
    "matched",
)


def price_validation_summary(price_validation: Any) -> dict[str, Any]:
    return {
        "passed": price_validation.passed,
        "kalshi_mid": optional_string(price_validation.kalshi_mid),
        "polymarket_mid": optional_string(price_validation.polymarket_mid),
        "difference": optional_string(price_validation.difference),
        "reason": price_validation.reason,
    }


def polymarket_search_result_count(search_debug: list[dict[str, Any]]) -> int:
    return sum(
        int(row.get("polymarket_result_count") or len(row.get("polymarket_results", [])))
        for row in search_debug
    )


def build_filter_hit_counts(
    *,
    search_debug: list[dict[str, Any]],
    flow_by_pair: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in search_debug:
        kalshi = row.get("kalshi", {})
        kalshi_ticker = str(kalshi.get("ticker") or "")
        for result in row.get("polymarket_results", []):
            token_id = str(result.get("token_id") or "")
            pair_flow = flow_by_pair.get((token_id, kalshi_ticker), {})
            status = comparison_flow_status(result, pair_flow)
            key = filter_hit_key(status)
            counts[key] = counts.get(key, 0) + 1
    return ordered_nonzero_counts(counts)


def build_structural_reason_counts(search_debug: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in search_debug:
        for result in row.get("polymarket_results", []):
            if result.get("skipped_nested_event", False):
                continue
            if not result.get("outcome_filter_passed", True):
                continue
            if not result.get("passes_prefilter", False):
                continue
            structural = result.get("structural_validation", {})
            if structural.get("passed", False):
                continue
            for reason in structural.get("reasons", []):
                key = structural_reason_key(str(reason))
                counts[key] = counts.get(key, 0) + 1
    return ordered_nonzero_counts(counts)


def structural_reason_key(reason: str) -> str:
    return reason.split(":", 1)[0]


def build_candidate_review_records(
    *,
    search_debug: list[dict[str, Any]],
    flow_by_pair: dict[tuple[str, str], dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    maybe: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for row in search_debug:
        kalshi = row.get("kalshi", {})
        kalshi_ticker = str(kalshi.get("ticker") or "")
        for result in row.get("polymarket_results", []):
            token_id = str(result.get("token_id") or "")
            pair_flow = flow_by_pair.get((token_id, kalshi_ticker), {})
            status = comparison_flow_status(result, pair_flow)
            if status["matched"]:
                continue
            record = candidate_review_record(row, result, pair_flow, status)
            if status["status"] == "ready_for_llm" or status["filter"] == "llm_not_run":
                maybe.append(record)
            else:
                rejected.append(record)
    return maybe, rejected


def candidate_review_record(
    row: dict[str, Any],
    result: dict[str, Any],
    pair_flow: dict[str, Any],
    status: dict[str, Any],
) -> dict[str, Any]:
    kalshi = row.get("kalshi", {})
    structural = result.get("structural_validation", {})
    return {
        "status": status["status"],
        "filter": status["filter"],
        "kalshi_ticker": kalshi.get("ticker"),
        "kalshi_title": kalshi.get("full_title"),
        "polymarket_title": result.get("title"),
        "polymarket_token_id": result.get("token_id"),
        "polymarket_condition_id": result.get("condition_id"),
        "polymarket_slug": result.get("slug"),
        "structural_score": result.get("structural_score"),
        "side_mapping": result.get("side_mapping"),
        "blocking_issues": result.get("blocking_issues", []),
        "structural_reasons": structural.get("reasons", []),
        "price_validation": pair_flow.get("price_validation"),
        "kalshi_normalized": kalshi.get("normalized"),
        "polymarket_normalized": structural.get("polymarket_normalized"),
        "queries": row.get("queries", []),
    }


def filter_hit_key(status: dict[str, Any]) -> str:
    if status.get("matched"):
        return "matched"
    return str(status.get("filter") or status.get("status") or "unknown")


def ordered_nonzero_counts(counts: dict[str, int]) -> dict[str, int]:
    ordered: dict[str, int] = {
        key: counts[key]
        for key in FILTER_HIT_ORDER
        if counts.get(key, 0) > 0
    }
    for key in sorted(counts):
        if key not in ordered and counts[key] > 0:
            ordered[key] = counts[key]
    return ordered


def build_flow_samples(
    *,
    search_debug: list[dict[str, Any]],
    flow_by_pair: dict[tuple[str, str], dict[str, Any]],
    limit: int,
    candidates_per_kalshi: int = 3,
) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for row in search_debug[:limit]:
        kalshi = row.get("kalshi", {})
        kalshi_ticker = str(kalshi.get("ticker") or "")
        search_results: list[dict[str, Any]] = []
        selected_candidates: list[dict[str, Any]] = []
        for result in row.get("polymarket_results", []):
            token_id = str(result.get("token_id") or "")
            pair_flow = flow_by_pair.get((token_id, kalshi_ticker), {})
            status = comparison_flow_status(result, pair_flow)
            entry = {
                "rank": result.get("rank"),
                "polymarket": {
                    "title": result.get("title"),
                    "event_title": result.get("event_title"),
                    "outcome": result.get("outcome"),
                    "token_id": token_id,
                    "tags": result.get("tags", []),
                },
                "selected_for_llm": bool(pair_flow.get("reached_llm_gate")),
                "matched": status["matched"],
                "status": status["status"],
                "filter": status["filter"],
                "metadata": comparison_flow_metadata(row, result, pair_flow),
            }
            search_results.append(entry)
            if entry["selected_for_llm"] or entry["matched"]:
                selected_candidates.append(entry)
        samples.append(
            {
                "kalshi": {
                    "ticker": kalshi_ticker,
                    "title": kalshi.get("full_title"),
                    "size": kalshi.get("size", {}),
                },
                "query": row.get("query"),
                "polymarket_result_count": row.get("polymarket_result_count"),
                "candidate_count_for_kalshi": row.get("candidate_count"),
                "best_search_results": search_results[:candidates_per_kalshi],
                "best_candidates": selected_candidates[:candidates_per_kalshi],
            }
        )
    return samples


def comparison_flow_metadata(
    row: dict[str, Any],
    result: dict[str, Any],
    pair_flow: dict[str, Any],
) -> dict[str, Any]:
    structural = result.get("structural_validation", {})
    date_validation = result.get("date_validation", {})
    return {
        "polymarket_result_count": row.get("polymarket_result_count"),
        "polymarket_tags": result.get("tags", []),
        "candidate_count_for_kalshi": row.get("candidate_count"),
        "event_contract_count": result.get("event_contract_count"),
        "lexical_score": result.get("lexical_score"),
        "hybrid_similarity": result.get("hybrid_similarity"),
        "outcome_filter": result.get("outcome_filter"),
        "kalshi_market_type": structural.get("kalshi_market_type"),
        "polymarket_market_type": structural.get("polymarket_market_type"),
        "kalshi_domain": structural.get("kalshi_domain"),
        "polymarket_domain": structural.get("polymarket_domain"),
        "shared_proper_nouns": structural.get("shared_proper_nouns", []),
        "structural_reasons": structural.get("reasons", []),
        "date_validation": date_validation,
        "price_validation": pair_flow.get("price_validation"),
        "deterministic_result": pair_flow.get("deterministic_result"),
        "llm_result": pair_flow.get("llm_result"),
    }


def comparison_flow_status(
    result: dict[str, Any],
    pair_flow: dict[str, Any],
) -> dict[str, Any]:
    if result.get("skipped_nested_event", False):
        return {"matched": False, "status": "rejected", "filter": "nested_event"}
    if not result.get("outcome_filter_passed", True):
        return {"matched": False, "status": "rejected", "filter": "outcome_filter"}
    if not result.get("passes_prefilter", False):
        return {"matched": False, "status": "rejected", "filter": "prefilter"}
    structural = result.get("structural_validation", {})
    if not structural.get("passed", False):
        if any(str(reason).startswith("date_mismatch") for reason in structural.get("reasons", [])):
            return {"matched": False, "status": "rejected", "filter": "date"}
        return {"matched": False, "status": "rejected", "filter": "structure"}
    if pair_flow.get("stale_match_rejection", False):
        return {"matched": False, "status": "rejected", "filter": "stale_date"}
    if pair_flow.get("future_match_rejection", False):
        return {"matched": False, "status": "rejected", "filter": "future_date"}
    price_validation = pair_flow.get("price_validation")
    if (
        price_validation
        and not price_validation.get("passed", False)
        and pair_flow.get("reject_on_price_validation", True)
    ):
        return {"matched": False, "status": "rejected", "filter": "price_validation"}
    if pair_flow.get("kept_as_match", False):
        return {"matched": True, "status": "matched", "filter": None}
    if pair_flow.get("llm_called", False):
        return {"matched": False, "status": "rejected", "filter": "llm"}
    if pair_flow.get("reached_llm_gate", False):
        return {"matched": False, "status": "ready_for_llm", "filter": "llm_not_run"}
    return {"matched": False, "status": "rejected", "filter": "not_selected"}
