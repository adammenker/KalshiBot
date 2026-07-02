from __future__ import annotations

import argparse
import json
import sys

import requests

from kalshibot.client import KalshiClient
from kalshibot.config import load_config
from kalshibot.commands.discovery import (
    add_discovery_parsers,
    run_discover_matches,
    run_promote_discovered_matches,
)
from kalshibot.commands.history import (
    add_history_parsers,
    parse_timestamp,
    run_analyze,
    run_backfill_history,
    run_backtest_history,
)
from kalshibot.commands.polymarket import (
    add_polymarket_parsers,
    run_match_titles,
    run_poly_book,
    run_poly_event,
    run_poly_events,
    run_poly_market,
    run_poly_price,
)
from kalshibot.commands.runtime import add_runtime_parsers, run_dynamic_bot
from kalshibot.commands.trading import (
    add_trading_parsers,
    build_spread_pairs,
    format_heartbeat_drop,
    format_heartbeat_failure,
    heartbeat_pair_key,
    heartbeat_interval_seconds,
    run_heartbeat,
    run_heartbeat_async,
    run_spread_check,
)

__all__ = [
    "build_parser",
    "build_spread_pairs",
    "format_heartbeat_drop",
    "format_heartbeat_failure",
    "heartbeat_pair_key",
    "main",
    "parse_timestamp",
    "run_heartbeat_async",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kalshibot")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("balance", help="Fetch authenticated account balance")

    add_polymarket_parsers(subparsers)
    add_discovery_parsers(subparsers)
    add_trading_parsers(subparsers)
    add_runtime_parsers(subparsers)
    add_history_parsers(subparsers)
    return parser


def run_balance() -> int:
    config = load_config()
    client = KalshiClient(config)
    balance = client.get_balance()
    print(json.dumps(balance, indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "balance":
            return run_balance()
        if args.command == "poly-market":
            return run_poly_market(args.slug)
        if args.command == "poly-event":
            return run_poly_event(args.slug, raw=args.raw)
        if args.command == "poly-events":
            return run_poly_events(args.limit)
        if args.command == "poly-book":
            return run_poly_book(args.token_id)
        if args.command == "poly-price":
            return run_poly_price(args.token_id, args.side)
        if args.command == "match-titles":
            return run_match_titles(
                args.polymarket_title,
                args.kalshi_title,
                no_llm=args.no_llm,
            )
        if args.command == "discover-matches":
            return run_discover_matches(
                output=args.output,
                pairs_output=args.pairs_output,
                review_output=args.review_output,
                approved_review_output=args.approved_review_output,
                maybe_output=args.maybe_output,
                rejected_output=args.rejected_output,
                diagnostics_output=args.diagnostics_output,
                search_debug_output=args.search_debug_output,
                flow_summary_limit=args.flow_summary_limit,
                flow_candidates_per_kalshi=args.flow_candidates_per_kalshi,
                pairs_min_confidence=args.pairs_min_confidence,
                confidence_threshold=args.confidence_threshold,
                price_validation_threshold=(
                    None if args.no_price_validation else args.price_validation_threshold
                ),
                price_validation_mode=args.price_validation_mode,
                prefilter_threshold=args.prefilter_threshold,
                max_candidates_per_polymarket=args.max_candidates_per_polymarket,
                max_comparisons=args.max_comparisons,
                strategy=args.strategy,
                market_profile=args.market_profile,
                polymarket_search_limit=args.polymarket_search_limit,
                max_polymarket_contracts_per_event=args.max_polymarket_contracts_per_event,
                polymarket_outcome_filter=args.polymarket_outcome_filter,
                polymarket_event_limit=args.polymarket_event_limit,
                kalshi_limit=args.kalshi_limit,
                kalshi_fetch_limit=args.kalshi_fetch_limit,
                kalshi_sort_by=args.kalshi_sort_by,
                kalshi_pages=args.kalshi_pages,
                kalshi_status=args.kalshi_status,
                min_match_date=args.min_match_date,
                max_match_date=args.max_match_date,
                include_past_contracts=args.include_past_contracts,
                no_max_match_date=args.no_max_match_date,
                kalshi_series_ticker=args.kalshi_series_ticker,
                kalshi_include_series=args.kalshi_include_series,
                kalshi_exclude_series=args.kalshi_exclude_series,
                kalshi_market_types=args.kalshi_market_types,
                index_path=args.index_path,
                no_llm=args.no_llm,
            )
        if args.command == "promote-discovered-matches":
            return run_promote_discovered_matches(
                input_path=args.input,
                output=args.output,
                review_output=args.review_output,
                min_confidence=args.min_confidence,
                include_price_warnings=args.include_price_warnings,
            )
        if args.command == "spread-check":
            return run_spread_check(
                args.pairs,
                args.kalshi_ticker,
                args.polymarket_token_id,
                args.outcome,
                args.label,
                args.max_venue_spread,
                args.min_buy_size,
                args.min_depth_size,
                args.depth_window,
                args.min_edge,
                args.min_fee_adjusted_edge,
                args.fee_mode,
            )
        if args.command == "heartbeat":
            return run_heartbeat(
                args.pairs,
                args.db,
                args.iterations,
                heartbeat_interval_seconds(args.interval_seconds, args.interval_ms),
                args.max_venue_spread,
                args.min_buy_size,
                args.min_depth_size,
                args.depth_window,
                args.min_edge,
                args.min_fee_adjusted_edge,
                args.fee_mode,
                args.signal_lookback_minutes,
                args.min_mid_edge,
                args.min_poly_mid_move,
                args.min_poly_oi_delta,
                args.min_poly_volume_delta,
                args.max_kalshi_mid_move,
                None if args.no_paper_exit_edge else args.paper_exit_edge,
                args.paper_take_profit,
                args.paper_stop_loss,
                args.paper_max_hold_minutes,
                args.drop_failed_pairs_after,
                None if args.no_paper_logs else args.paper_trade_log,
                None if args.no_paper_logs else args.paper_pnl_log,
                args.heartbeat_output,
                args.scheduler,
                args.metadata_refresh_seconds,
                args.strategy_mode,
                args.strategy_variants,
                args.strategy_paper_trades,
                args.strategy_config,
            )
        if args.command == "run-bot":
            return run_dynamic_bot(args)
        if args.command == "analyze":
            return run_analyze(args.db, args.market_limit, args.strategy_signal_limit)
        if args.command == "backfill-history":
            return run_backfill_history(
                pairs_path=args.pairs,
                db_path=args.db,
                start=args.start,
                end=args.end,
                period_interval=args.period_interval,
                polymarket_interval=args.polymarket_interval,
                series_ticker=args.series_ticker,
            )
        if args.command == "backtest-history":
            return run_backtest_history(
                db_path=args.db,
                min_edge=args.min_edge,
                hold_period_minutes=args.hold_period_minutes,
                slippage=args.slippage,
            )
    except (OSError, ValueError, requests.RequestException) as exc:
        print(f"kalshibot: {exc}", file=sys.stderr)
        return 1

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
