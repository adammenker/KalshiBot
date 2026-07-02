from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

from kalshibot.client import KalshiClient
from kalshibot.config import load_config, load_local_llm_config, load_polymarket_config
from kalshibot.defaults import DEFAULT_DISCOVERY_PRICE_VALIDATION_THRESHOLD
from kalshibot.discovery import discover_market_matches, promote_discovered_matches
from kalshibot.discovery.profiles import (
    DISCOVERY_PROFILE_DEFAULTS,
    parse_market_type_set,
    resolve_discovery_profile,
)
from kalshibot.market_urls import kalshi_market_url, polymarket_market_url
from kalshibot.market_matcher import OllamaTitleMatcher
from kalshibot.polymarket import PolymarketClient
from kalshibot.utils import optional_string, parse_csv_set

__all__ = [
    "add_discovery_parsers",
    "approved_matches_for_review",
    "format_discovery_cli_output",
    "format_match_review_output",
    "parse_market_type_set",
    "run_discover_matches",
    "run_promote_discovered_matches",
]


def add_discovery_parsers(subparsers: Any) -> None:
    discover_matches = subparsers.add_parser(
        "discover-matches",
        help="One-shot pull of Polymarket and Kalshi markets, writing likely title matches to JSON",
    )
    discover_matches.add_argument(
        "--output",
        type=Path,
        default=Path("data/discovered_market_matches.json"),
        help="Compact output JSON path with summary and kept matches",
    )
    discover_matches.add_argument(
        "--pairs-output",
        type=Path,
        default=Path("config/approved_market_pairs.json"),
        help="Optional market-pair JSON path to write from promoted discovered matches",
    )
    discover_matches.add_argument(
        "--review-output",
        type=Path,
        default=Path("logs/discovery_matches.json"),
        help="Compact JSON match review file with only titles, dates, prices, and ids.",
    )
    discover_matches.add_argument(
        "--approved-review-output",
        type=Path,
        default=Path("logs/approved_market_pairs.json"),
        help="Compact JSON review file containing only promoted heartbeat pairs.",
    )
    discover_matches.add_argument(
        "--maybe-output",
        type=Path,
        help="Optional JSON path for candidates needing manual review",
    )
    discover_matches.add_argument(
        "--rejected-output",
        type=Path,
        help="Optional JSON path for rejected candidates and blocker reasons",
    )
    discover_matches.add_argument(
        "--diagnostics-output",
        type=Path,
        help="Optional JSON path for the full discovery diagnostics report",
    )
    discover_matches.add_argument(
        "--search-debug-output",
        type=Path,
        help="Optional JSON path for Kalshi queries and raw Polymarket search candidates",
    )
    discover_matches.add_argument(
        "--flow-summary-limit",
        type=int,
        default=0,
        help="Collect this many compact Kalshi-market flow samples for in-process diagnostics.",
    )
    discover_matches.add_argument(
        "--flow-candidates-per-kalshi",
        type=int,
        default=3,
        help="Polymarket search candidates to show for each flow-summary Kalshi market.",
    )
    discover_matches.add_argument(
        "--pairs-min-confidence",
        type=float,
        default=0.9,
        help="Minimum discovered-match confidence to write to --pairs-output",
    )
    discover_matches.add_argument(
        "--confidence-threshold",
        type=float,
        default=0.85,
        help="Minimum matcher confidence to keep a discovered match",
    )
    discover_matches.add_argument(
        "--price-validation-threshold",
        type=Decimal,
        default=DEFAULT_DISCOVERY_PRICE_VALIDATION_THRESHOLD,
        help="Maximum allowed Kalshi/Polymarket midpoint difference for discovered matches",
    )
    discover_matches.add_argument(
        "--no-price-validation",
        action="store_true",
        help="Disable orderbook midpoint validation for discovered matches",
    )
    discover_matches.add_argument(
        "--price-validation-mode",
        choices=["warn", "reject"],
        default="warn",
        help="Warn records price gaps as diagnostics; reject drops candidates outside --price-validation-threshold.",
    )
    discover_matches.add_argument(
        "--prefilter-threshold",
        type=float,
        default=0.18,
        help="Minimum candidate similarity before calling the matcher",
    )
    discover_matches.add_argument(
        "--max-candidates-per-polymarket",
        type=int,
        default=3,
        help="Maximum Kalshi candidates to compare for each Polymarket outcome",
    )
    discover_matches.add_argument(
        "--max-comparisons",
        type=int,
        default=50,
        help="Maximum LLM/title comparisons for one discovery run. Use 0 for no cap.",
    )
    discover_matches.add_argument(
        "--strategy",
        choices=["polymarket-search", "broad"],
        default="polymarket-search",
        help="Discovery strategy: search Polymarket per Kalshi market, or broad-rank both universes",
    )
    discover_matches.add_argument(
        "--market-profile",
        choices=sorted(DISCOVERY_PROFILE_DEFAULTS),
        default="win-lose",
        help="Preset discovery filters. win-lose is the default project profile.",
    )
    discover_matches.add_argument(
        "--polymarket-search-limit",
        type=int,
        default=10,
        help="Polymarket public-search results per Kalshi market when --strategy polymarket-search",
    )
    discover_matches.add_argument(
        "--max-polymarket-contracts-per-event",
        type=int,
        help="Skip Polymarket search-result events with more token contracts than this. Use 0 to disable.",
    )
    discover_matches.add_argument(
        "--polymarket-outcome-filter",
        choices=["any", "yes-no"],
        help="Restrict Polymarket outcomes before final matching. Defaults come from --market-profile.",
    )
    discover_matches.add_argument(
        "--polymarket-event-limit",
        type=int,
        default=25,
        help="Number of active Polymarket events to pull",
    )
    discover_matches.add_argument(
        "--kalshi-limit",
        type=int,
        default=25,
        help="Kalshi markets to run discovery against after filters and optional size sorting",
    )
    discover_matches.add_argument(
        "--kalshi-fetch-limit",
        type=int,
        default=500,
        help="Kalshi markets per page fetched before local filters and optional size sorting, maximum 1000",
    )
    discover_matches.add_argument(
        "--kalshi-sort-by",
        choices=[
            "none",
            "volume-24h",
            "volume",
            "open-interest",
            "liquidity",
            "notional-value",
        ],
        default="volume-24h",
        help="Optional local Kalshi ranking field before applying --kalshi-limit",
    )
    discover_matches.add_argument(
        "--kalshi-pages",
        type=int,
        default=5,
        help="Maximum Kalshi market pages to pull",
    )
    discover_matches.add_argument(
        "--kalshi-status",
        default="open",
        help="Kalshi market status filter, such as open, unopened, closed, or settled",
    )
    discover_matches.add_argument(
        "--min-match-date",
        help=(
            "Earliest contract date to keep in discovered matches. Defaults to today unless "
            "--include-past-contracts is set."
        ),
    )
    discover_matches.add_argument(
        "--max-match-date",
        help=(
            "Latest contract date to keep in discovered matches. Defaults are profile-specific; "
            "use --no-max-match-date to disable."
        ),
    )
    discover_matches.add_argument(
        "--include-past-contracts",
        action="store_true",
        help="Do not drop matched contracts dated before today.",
    )
    discover_matches.add_argument(
        "--no-max-match-date",
        action="store_true",
        help="Disable profile-specific maximum contract-date filtering.",
    )
    discover_matches.add_argument(
        "--kalshi-series-ticker",
        help="Optional Kalshi series ticker filter",
    )
    discover_matches.add_argument(
        "--kalshi-include-series",
        help="Comma-separated Kalshi series tickers to keep after fetching markets",
    )
    discover_matches.add_argument(
        "--kalshi-exclude-series",
        help="Comma-separated Kalshi series tickers to skip after fetching markets",
    )
    discover_matches.add_argument(
        "--kalshi-market-types",
        help=(
            "Comma-separated market-type names to keep after fetching Kalshi markets. "
            "Defaults come from --market-profile."
        ),
    )
    discover_matches.add_argument(
        "--index-path",
        type=Path,
        default=Path("data/kalshi_market_index"),
        help="Directory where the Kalshi semantic search index is persisted",
    )
    discover_matches.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip final LLM matching; deterministic exact structural matches may still be output",
    )

    promote_matches = subparsers.add_parser(
        "promote-discovered-matches",
        help="Convert discovered market matches into a heartbeat-compatible pair config",
    )
    promote_matches.add_argument(
        "--input",
        type=Path,
        default=Path("data/discovered_market_matches.json"),
        help="Discovery JSON path produced by discover-matches",
    )
    promote_matches.add_argument(
        "--output",
        type=Path,
        default=Path("config/approved_market_pairs.json"),
        help="Output market-pair JSON path for spread-check or heartbeat",
    )
    promote_matches.add_argument(
        "--review-output",
        type=Path,
        default=Path("logs/approved_market_pairs.json"),
        help="Compact JSON review file containing only promoted heartbeat pairs.",
    )
    promote_matches.add_argument(
        "--min-confidence",
        type=float,
        default=0.9,
        help="Minimum discovered-match confidence to promote",
    )
    promote_matches.add_argument(
        "--include-price-warnings",
        action="store_true",
        help="Promote matches even when discovery price validation was missing or warning.",
    )


def run_discover_matches(
    *,
    output: Path,
    pairs_output: Path | None,
    review_output: Path | None,
    approved_review_output: Path | None,
    maybe_output: Path | None,
    rejected_output: Path | None,
    diagnostics_output: Path | None,
    search_debug_output: Path | None,
    flow_summary_limit: int,
    flow_candidates_per_kalshi: int,
    pairs_min_confidence: float,
    confidence_threshold: float,
    price_validation_threshold: Decimal | None,
    price_validation_mode: str,
    prefilter_threshold: float,
    max_candidates_per_polymarket: int,
    max_comparisons: int,
    strategy: str,
    market_profile: str,
    polymarket_search_limit: int,
    max_polymarket_contracts_per_event: int | None,
    polymarket_outcome_filter: str | None,
    polymarket_event_limit: int,
    kalshi_limit: int,
    kalshi_fetch_limit: int,
    kalshi_sort_by: str,
    kalshi_pages: int,
    kalshi_status: str,
    min_match_date: str | None,
    max_match_date: str | None,
    include_past_contracts: bool,
    no_max_match_date: bool,
    kalshi_series_ticker: str | None,
    kalshi_include_series: str | None,
    kalshi_exclude_series: str | None,
    kalshi_market_types: str | None,
    index_path: Path | None,
    no_llm: bool,
) -> int:
    if not 0 <= confidence_threshold <= 1:
        raise ValueError("--confidence-threshold must be between 0 and 1")
    if price_validation_threshold is not None and price_validation_threshold < 0:
        raise ValueError("--price-validation-threshold cannot be negative")
    if not 0 <= pairs_min_confidence <= 1:
        raise ValueError("--pairs-min-confidence must be between 0 and 1")
    if not 0 <= prefilter_threshold <= 1:
        raise ValueError("--prefilter-threshold must be between 0 and 1")
    if max_candidates_per_polymarket < 1:
        raise ValueError("--max-candidates-per-polymarket must be at least 1")
    if flow_summary_limit < 0:
        raise ValueError("--flow-summary-limit cannot be negative")
    if flow_candidates_per_kalshi < 1:
        raise ValueError("--flow-candidates-per-kalshi must be at least 1")
    if max_comparisons < 0:
        raise ValueError("--max-comparisons cannot be negative")
    if polymarket_search_limit < 1:
        raise ValueError("--polymarket-search-limit must be at least 1")
    resolved_profile = resolve_discovery_profile(
        market_profile=market_profile,
        max_polymarket_contracts_per_event=max_polymarket_contracts_per_event,
        polymarket_outcome_filter=polymarket_outcome_filter,
        kalshi_market_types=kalshi_market_types,
    )
    if (
        resolved_profile.max_polymarket_contracts_per_event is not None
        and resolved_profile.max_polymarket_contracts_per_event < 0
    ):
        raise ValueError("--max-polymarket-contracts-per-event cannot be negative")
    max_contracts_per_event = (
        None
        if resolved_profile.max_polymarket_contracts_per_event == 0
        else resolved_profile.max_polymarket_contracts_per_event
    )
    if polymarket_event_limit < 1:
        raise ValueError("--polymarket-event-limit must be at least 1")
    if kalshi_limit < 1 or kalshi_limit > 1000:
        raise ValueError("--kalshi-limit must be between 1 and 1000")
    if kalshi_fetch_limit < 1 or kalshi_fetch_limit > 1000:
        raise ValueError("--kalshi-fetch-limit must be between 1 and 1000")
    if kalshi_pages < 1:
        raise ValueError("--kalshi-pages must be at least 1")
    today = date.today()
    effective_min_match_date = None if include_past_contracts else min_match_date or today.isoformat()
    if effective_min_match_date is not None:
        try:
            date.fromisoformat(effective_min_match_date)
        except ValueError as exc:
            raise ValueError("--min-match-date must be in YYYY-MM-DD format") from exc
    effective_max_match_date = resolve_max_match_date(
        max_match_date=max_match_date,
        no_max_match_date=no_max_match_date,
        profile_max_match_days=resolved_profile.max_match_days,
        today=today,
    )
    include_series = parse_csv_set(kalshi_include_series)
    exclude_series = parse_csv_set(kalshi_exclude_series)
    overlapping_series = include_series & exclude_series
    if overlapping_series:
        raise ValueError(
            "--kalshi-include-series and --kalshi-exclude-series overlap: "
            + ", ".join(sorted(overlapping_series))
        )

    llm = None if no_llm else OllamaTitleMatcher(load_local_llm_config())
    result = discover_market_matches(
        polymarket_client=PolymarketClient(load_polymarket_config()),
        kalshi_client=KalshiClient(load_config()),
        llm=llm,
        use_llm=not no_llm,
        confidence_threshold=confidence_threshold,
        polymarket_event_limit=polymarket_event_limit,
        kalshi_limit=kalshi_limit,
        kalshi_fetch_limit=kalshi_fetch_limit,
        kalshi_size_sort_by=kalshi_sort_by,
        kalshi_pages=kalshi_pages,
        kalshi_status=kalshi_status,
        kalshi_series_ticker=kalshi_series_ticker,
        kalshi_include_series=include_series,
        kalshi_exclude_series=exclude_series,
        kalshi_market_types=resolved_profile.kalshi_market_types,
        max_candidates_per_polymarket=max_candidates_per_polymarket,
        max_comparisons=max_comparisons or None,
        prefilter_threshold=prefilter_threshold,
        index_path=index_path,
        discovery_strategy=strategy,
        polymarket_search_limit=polymarket_search_limit,
        max_polymarket_contracts_per_event=max_contracts_per_event,
        polymarket_outcome_filter=resolved_profile.polymarket_outcome_filter,
        include_search_debug=search_debug_output is not None,
        price_validation_threshold=price_validation_threshold,
        reject_on_price_validation=price_validation_mode == "reject",
        flow_summary_limit=flow_summary_limit,
        flow_candidates_per_kalshi=flow_candidates_per_kalshi,
        min_match_date=effective_min_match_date,
        max_match_date=effective_max_match_date,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    search_debug = result.pop("search_debug", None)
    result.pop("flow_samples", None)
    output.write_text(json.dumps(compact_discovery_output(result), indent=2, sort_keys=True))
    if maybe_output is not None:
        maybe_output.parent.mkdir(parents=True, exist_ok=True)
        maybe_output.write_text(json.dumps(result.get("maybe_matches", []), indent=2, sort_keys=True))
    if rejected_output is not None:
        rejected_output.parent.mkdir(parents=True, exist_ok=True)
        rejected_output.write_text(
            json.dumps(result.get("rejected_matches", []), indent=2, sort_keys=True)
        )
    if diagnostics_output is not None:
        diagnostics_output.parent.mkdir(parents=True, exist_ok=True)
        diagnostics_output.write_text(
            json.dumps(
                {
                    "summary": result.get("summary", {}),
                    "matches": result.get("matches", []),
                    "maybe_matches": result.get("maybe_matches", []),
                    "rejected_matches": result.get("rejected_matches", []),
                    "search_debug": search_debug or [],
                },
                indent=2,
                sort_keys=True,
            )
        )
    if search_debug_output is not None:
        search_debug_output.parent.mkdir(parents=True, exist_ok=True)
        search_debug_output.write_text(json.dumps(search_debug or [], indent=2, sort_keys=True))
    if review_output is not None:
        review_output.parent.mkdir(parents=True, exist_ok=True)
        review_output.write_text(format_match_review_output(result.get("matches", [])))
    pairs_promoted = None
    approved_review_rows = approved_matches_for_review(
        result.get("matches", []),
        min_confidence=pairs_min_confidence,
        require_price_validation=True,
    )
    if pairs_output is not None:
        promoted = promote_discovered_matches(
            result,
            min_confidence=pairs_min_confidence,
            require_price_validation=True,
        )
        pairs_output.parent.mkdir(parents=True, exist_ok=True)
        pairs_output.write_text(json.dumps(promoted, indent=2, sort_keys=True))
        pairs_promoted = len(promoted["markets"])
    if approved_review_output is not None:
        approved_review_output.parent.mkdir(parents=True, exist_ok=True)
        approved_review_output.write_text(format_match_review_output(approved_review_rows))
    print(
        format_discovery_cli_output(
            result,
            output=output,
            market_profile=market_profile,
            review_output=review_output,
            approved_review_output=approved_review_output,
            maybe_output=maybe_output,
            rejected_output=rejected_output,
            diagnostics_output=diagnostics_output,
            search_debug_output=search_debug_output,
            search_debug_rows=len(search_debug or []),
            pairs_output=pairs_output,
            pairs_promoted=pairs_promoted,
        )
    )
    return 0


def format_discovery_cli_output(
    result: dict[str, Any],
    *,
    output: Path,
    market_profile: str,
    review_output: Path | None = None,
    approved_review_output: Path | None = None,
    maybe_output: Path | None = None,
    rejected_output: Path | None = None,
    diagnostics_output: Path | None = None,
    search_debug_output: Path | None = None,
    search_debug_rows: int | None = None,
    pairs_output: Path | None = None,
    pairs_promoted: int | None = None,
) -> str:
    return "\n\n".join(
        [
            format_matched_pair_titles(result.get("matches", [])),
            "Discovery stats:\n"
            + compact_top_level_json(
                discovery_cli_stats(
                    result,
                    output=output,
                    market_profile=market_profile,
                    review_output=review_output,
                    approved_review_output=approved_review_output,
                    maybe_output=maybe_output,
                    rejected_output=rejected_output,
                    diagnostics_output=diagnostics_output,
                    search_debug_output=search_debug_output,
                    search_debug_rows=search_debug_rows,
                    pairs_output=pairs_output,
                    pairs_promoted=pairs_promoted,
                )
            ),
        ]
    )


def format_matched_pair_titles(matches: list[dict[str, Any]]) -> str:
    lines = ["Matched pairs:"]
    if not matches:
        lines.append("(none)")
        return "\n".join(lines)
    for index, match in enumerate(matches, start=1):
        lines.append(f"{index}. Kalshi: {match.get('kalshi_title')}")
        lines.append(f"   Poly:   {match.get('polymarket_title')}")
    return "\n".join(lines)


def format_match_review_output(matches: list[dict[str, Any]]) -> str:
    review_rows: list[dict[str, Any]] = []
    for match in matches:
        date_validation = match.get("date_validation") or {}
        price_validation = match.get("price_validation") or {}
        polymarket_token_id = selected_polymarket_token_id(match)
        review_rows.append(
            {
                "confidence": match.get("confidence"),
                "side_mapping": match.get("side_mapping"),
                "method": match.get("method"),
                "price_gap": price_validation.get("difference"),
                "price_status": "passed" if price_validation.get("passed") else "warning",
                "price_reason": price_validation.get("reason"),
                "kalshi": {
                    "ticker": match.get("kalshi_ticker"),
                    "title": compact_value(match.get("kalshi_title")),
                    "url": kalshi_market_url(optional_string(match.get("kalshi_ticker"))),
                    "date": date_validation.get("kalshi_date"),
                    "price": price_validation.get("kalshi_mid"),
                },
                "polymarket": {
                    "token_id": polymarket_token_id,
                    "title": compact_value(match.get("polymarket_title")),
                    "url": polymarket_market_url(
                        slug=optional_string(match.get("polymarket_slug")),
                        token_id=optional_string(polymarket_token_id),
                    ),
                    "date": date_validation.get("polymarket_date"),
                    "price": price_validation.get("polymarket_mid"),
                },
            }
        )
    return json.dumps(review_rows, indent=2, sort_keys=True) + "\n"


def approved_matches_for_review(
    matches: list[dict[str, Any]],
    *,
    min_confidence: float,
    require_price_validation: bool = True,
) -> list[dict[str, Any]]:
    approved: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
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
        kalshi_ticker = optional_string(match.get("kalshi_ticker"))
        polymarket_token_id = selected_polymarket_token_id(match)
        if not kalshi_ticker or not polymarket_token_id:
            continue
        key = (kalshi_ticker, polymarket_token_id)
        if key in seen:
            continue
        seen.add(key)
        row = dict(match)
        row["polymarket_token_id"] = polymarket_token_id
        approved.append(row)
    return approved


def selected_polymarket_token_id(match: dict[str, Any]) -> str | None:
    return optional_string(match.get("polymarket_yes_token_id") or match.get("polymarket_token_id"))


def compact_discovery_output(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "summary": result.get("summary", {}),
        "matches": result.get("matches", []),
    }


def compact_value(value: Any) -> str:
    if value is None or value == "":
        return "-"
    return str(value).replace("\n", " ")


def discovery_cli_stats(
    result: dict[str, Any],
    *,
    output: Path,
    market_profile: str,
    review_output: Path | None,
    approved_review_output: Path | None,
    maybe_output: Path | None,
    rejected_output: Path | None,
    diagnostics_output: Path | None,
    search_debug_output: Path | None,
    search_debug_rows: int | None,
    pairs_output: Path | None,
    pairs_promoted: int | None,
) -> dict[str, Any]:
    summary = result["summary"]
    outputs: dict[str, Any] = {"matches": str(output)}
    if review_output is not None:
        outputs["review"] = str(review_output)
    if approved_review_output is not None:
        outputs["approved_review"] = str(approved_review_output)
    if maybe_output is not None:
        outputs["maybe"] = str(maybe_output)
    if rejected_output is not None:
        outputs["rejected"] = str(rejected_output)
    if diagnostics_output is not None:
        outputs["diagnostics"] = str(diagnostics_output)
    if pairs_output is not None:
        outputs["pairs"] = str(pairs_output)
        outputs["pairs_promoted"] = pairs_promoted
    if search_debug_output is not None:
        outputs["debug"] = str(search_debug_output)
        outputs["debug_rows"] = search_debug_rows
    stats: dict[str, Any] = {
        "profile": market_profile,
        "markets": {
            "kalshi_fetched": summary.get("kalshi_markets_fetched"),
            "kalshi_after_filters": summary.get("kalshi_markets_after_filters"),
            "kalshi_selected": summary.get("kalshi_markets"),
            "kalshi_fetch_limit": summary.get("kalshi_fetch_limit"),
            "polymarket_raw_results": summary.get("polymarket_search_results"),
            "polymarket_unique_candidates": summary.get("polymarket_markets"),
        },
        "counts": {
            "candidate_pairs": summary.get("candidate_pairs"),
            "matches": summary.get("matches"),
            "maybe": summary.get("maybe_matches"),
            "rejected": summary.get("rejected_matches"),
        },
        "filter_hits": summary.get("filter_hits", {}),
        "reason_hits": summary.get("structural_reason_hits", {}),
        "price_rejections": summary.get("price_validation_rejections"),
        "llm": {
            "candidates": summary.get("llm_candidates"),
            "calls": summary.get("comparisons"),
        },
        "outputs": outputs,
    }
    if summary.get("stale_match_rejections"):
        stats["stale_date_rejections"] = summary.get("stale_match_rejections")
    if summary.get("future_match_rejections"):
        stats["future_date_rejections"] = summary.get("future_match_rejections")
    if summary.get("min_match_date"):
        stats["min_match_date"] = summary.get("min_match_date")
    if summary.get("max_match_date"):
        stats["max_match_date"] = summary.get("max_match_date")
    return stats


def resolve_max_match_date(
    *,
    max_match_date: str | None,
    no_max_match_date: bool,
    profile_max_match_days: int | None,
    today: date,
) -> str | None:
    if no_max_match_date:
        return None
    if max_match_date is not None:
        try:
            date.fromisoformat(max_match_date)
        except ValueError as exc:
            raise ValueError("--max-match-date must be in YYYY-MM-DD format") from exc
        return max_match_date
    if profile_max_match_days is None:
        return None
    return (today + timedelta(days=profile_max_match_days)).isoformat()


def compact_top_level_json(values: dict[str, Any]) -> str:
    lines = ["{"]
    items = list(values.items())
    for index, (key, value) in enumerate(items):
        comma = "," if index < len(items) - 1 else ""
        lines.append(f"  {json.dumps(key)}: {json.dumps(value, sort_keys=True)}{comma}")
    lines.append("}")
    return "\n".join(lines)


def run_promote_discovered_matches(
    *,
    input_path: Path,
    output: Path,
    review_output: Path | None,
    min_confidence: float,
    include_price_warnings: bool,
) -> int:
    if not 0 <= min_confidence <= 1:
        raise ValueError("--min-confidence must be between 0 and 1")
    payload = json.loads(input_path.read_text())
    promoted = promote_discovered_matches(
        payload,
        min_confidence=min_confidence,
        require_price_validation=not include_price_warnings,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(promoted, indent=2, sort_keys=True))
    if review_output is not None:
        review_rows = approved_matches_for_review(
            payload.get("matches", []),
            min_confidence=min_confidence,
            require_price_validation=not include_price_warnings,
        )
        review_output.parent.mkdir(parents=True, exist_ok=True)
        review_output.write_text(format_match_review_output(review_rows))
    print(
        json.dumps(
            {
                "input": str(input_path),
                "output": str(output),
                "review_output": str(review_output) if review_output is not None else None,
                "min_confidence": min_confidence,
                "include_price_warnings": include_price_warnings,
                "pairs_promoted": len(promoted["markets"]),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0
