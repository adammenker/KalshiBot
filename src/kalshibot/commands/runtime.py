from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path
from typing import Any

from kalshibot.commands.trading import (
    STRATEGY_MODES,
    add_spread_filter_args,
    heartbeat_interval_seconds,
    heartbeat_strategy_config,
)
from kalshibot.defaults import (
    DEFAULT_MAX_KALSHI_MID_MOVE,
    DEFAULT_MIN_MID_EDGE,
    DEFAULT_MIN_POLY_MID_MOVE,
    DEFAULT_MIN_POLY_OI_DELTA,
    DEFAULT_MIN_POLY_VOLUME_DELTA,
    DEFAULT_SIGNAL_LOOKBACK_MINUTES,
)
from kalshibot.monitoring.heartbeat import HEARTBEAT_OUTPUT_MODES
from kalshibot.paper import PaperExitConfig
from kalshibot.runtime.supervisor import (
    DynamicBotConfig,
    DynamicDiscoveryConfig,
    DynamicHeartbeatConfig,
    run_dynamic_bot_async,
)

__all__ = ["add_runtime_parsers", "run_dynamic_bot"]


def add_runtime_parsers(subparsers: Any) -> None:
    run_bot = subparsers.add_parser(
        "run-bot",
        help="Run dynamic live sports discovery plus heartbeat from a persistent active watchlist",
    )
    run_bot.add_argument(
        "--db",
        type=Path,
        default=Path("data/observations.sqlite"),
        help="SQLite database path for observations, trades, signals, and active pairs.",
    )
    run_bot.add_argument(
        "--seed-pairs",
        type=Path,
        help="Optional initial market-pair JSON file to activate before discovery starts.",
    )
    run_bot.add_argument(
        "--active-pairs-output",
        type=Path,
        default=Path("config/live_active_market_pairs.json"),
        help="JSON snapshot of currently active pairs, updated as the bot runs.",
    )
    run_bot.add_argument(
        "--clear-active-on-start",
        action="store_true",
        help="Mark existing active runtime pairs stale before seeding/discovery.",
    )
    run_bot.add_argument(
        "--runtime-minutes",
        type=Decimal,
        help="Optional runtime limit. Omit to run until interrupted.",
    )
    run_bot.add_argument(
        "--discovery-interval-seconds",
        type=Decimal,
        default=Decimal("900"),
        help="Seconds between live sports discovery cycles. Default is 15 minutes.",
    )
    run_bot.add_argument(
        "--market-profile",
        default="sports-game-winner",
        help="Discovery profile for live markets. Defaults to sports-game-winner.",
    )
    run_bot.add_argument(
        "--confidence-threshold",
        type=float,
        default=0.85,
        help="Minimum matcher confidence to keep a discovered match.",
    )
    run_bot.add_argument(
        "--pairs-min-confidence",
        type=float,
        default=0.9,
        help="Minimum discovered-match confidence to activate a heartbeat pair.",
    )
    run_bot.add_argument(
        "--prefilter-threshold",
        type=float,
        default=0.18,
        help="Minimum candidate similarity before calling the matcher.",
    )
    run_bot.add_argument(
        "--max-comparisons",
        type=int,
        default=50,
        help="Maximum LLM/title comparisons per discovery cycle. Use 0 for no cap.",
    )
    run_bot.add_argument(
        "--max-candidates-per-polymarket",
        type=int,
        default=3,
        help="Maximum Kalshi candidates to compare for each Polymarket outcome.",
    )
    run_bot.add_argument(
        "--polymarket-search-limit",
        type=int,
        default=10,
        help="Polymarket public-search results per live Kalshi market.",
    )
    run_bot.add_argument(
        "--max-polymarket-contracts-per-event",
        type=int,
        help="Skip Polymarket events with more token contracts than this. Use 0 to disable.",
    )
    run_bot.add_argument(
        "--price-validation-threshold",
        type=Decimal,
        default=Decimal("0.03"),
        help="Maximum allowed Kalshi/Polymarket midpoint difference during discovery.",
    )
    run_bot.add_argument(
        "--no-price-validation",
        action="store_true",
        help="Disable discovery price validation.",
    )
    run_bot.add_argument(
        "--price-validation-mode",
        choices=["warn", "reject"],
        default="reject",
        help="Reject or warn when discovery price validation fails.",
    )
    run_bot.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip final LLM matching; deterministic exact structural matches may still be output.",
    )
    run_bot.add_argument(
        "--live-lookback-hours",
        type=int,
        default=8,
        help="How far back to scan Kalshi sports milestones for in-progress games.",
    )
    run_bot.add_argument(
        "--live-future-window-minutes",
        type=int,
        default=15,
        help="Allow live-status milestones whose listed start is this far in the future.",
    )
    run_bot.add_argument(
        "--live-milestone-limit",
        type=int,
        default=500,
        help="Kalshi sports milestones per page fetched during live discovery.",
    )
    run_bot.add_argument(
        "--live-milestone-pages",
        type=int,
        default=2,
        help="Maximum Kalshi sports milestone pages fetched during live discovery.",
    )
    run_bot.add_argument(
        "--include-related-event-tickers",
        action="store_true",
        help="Fetch markets for all related milestone event tickers, not only primary tickers.",
    )
    run_bot.add_argument(
        "--discovery-output",
        type=Path,
        default=Path("data/live_discovered_market_matches.json"),
        help="Latest live discovery result JSON path.",
    )
    run_bot.add_argument(
        "--discovery-log",
        type=Path,
        default=Path("logs/live_discovery.jsonl"),
        help="Append-only compact JSONL log of live discovery cycle summaries.",
    )
    run_bot.add_argument(
        "--heartbeat-interval-seconds",
        type=Decimal,
        default=Decimal("0.25"),
        help="Seconds between heartbeat batches. Decimals are allowed.",
    )
    run_bot.add_argument(
        "--heartbeat-interval-ms",
        type=Decimal,
        help="Milliseconds between heartbeat batches. Overrides --heartbeat-interval-seconds.",
    )
    run_bot.add_argument(
        "--max-active-pairs",
        type=int,
        help="Optional maximum active pairs to poll per heartbeat batch.",
    )
    run_bot.add_argument(
        "--active-pair-refresh-seconds",
        type=Decimal,
        default=Decimal("2"),
        help="Seconds between reloading the active pair registry during heartbeat.",
    )
    run_bot.add_argument(
        "--lifecycle-check-seconds",
        type=Decimal,
        default=Decimal("60"),
        help="Seconds between active market lifecycle checks.",
    )
    run_bot.add_argument(
        "--heartbeat-output",
        choices=HEARTBEAT_OUTPUT_MODES,
        default="summary",
        help="Heartbeat stdout volume.",
    )
    run_bot.add_argument(
        "--metadata-refresh-seconds",
        type=Decimal,
        default=Decimal("5"),
        help="Seconds between Polymarket metadata refreshes per market.",
    )
    add_spread_filter_args(run_bot)
    run_bot.add_argument(
        "--signal-lookback-minutes",
        type=int,
        default=DEFAULT_SIGNAL_LOOKBACK_MINUTES,
        help="Minutes back to compare Polymarket/Kalshi mid prices and Polymarket volume.",
    )
    run_bot.add_argument(
        "--min-mid-edge",
        type=Decimal,
        default=DEFAULT_MIN_MID_EDGE,
        help="Minimum Polymarket-mid-minus-Kalshi-mid edge required, in dollars.",
    )
    run_bot.add_argument(
        "--min-poly-mid-move",
        type=Decimal,
        default=DEFAULT_MIN_POLY_MID_MOVE,
        help="Minimum Polymarket mid-price increase over the lookback window.",
    )
    run_bot.add_argument(
        "--min-poly-oi-delta",
        type=Decimal,
        default=DEFAULT_MIN_POLY_OI_DELTA,
        help="Minimum Polymarket open-interest increase since the previous observation.",
    )
    run_bot.add_argument(
        "--min-poly-volume-delta",
        type=Decimal,
        default=DEFAULT_MIN_POLY_VOLUME_DELTA,
        help="Minimum Polymarket volume increase over the lookback window.",
    )
    run_bot.add_argument(
        "--max-kalshi-mid-move",
        type=Decimal,
        default=DEFAULT_MAX_KALSHI_MID_MOVE,
        help="Maximum absolute Kalshi mid-price move over the lookback window.",
    )
    run_bot.add_argument(
        "--paper-exit-edge",
        type=Decimal,
        default=None,
        help="Optional paper-trade close threshold based on spread convergence.",
    )
    run_bot.add_argument("--paper-take-profit", type=Decimal)
    run_bot.add_argument("--paper-stop-loss", type=Decimal)
    run_bot.add_argument("--paper-max-hold-minutes", type=int)
    run_bot.add_argument(
        "--drop-failed-pairs-after",
        type=int,
        default=3,
        help="Mark a runtime pair inactive after this many consecutive fetch failures.",
    )
    run_bot.add_argument(
        "--paper-trade-log",
        type=Path,
        default=Path("logs/paper_trades.jsonl"),
        help="Append-only JSONL log of paper trade open/close events.",
    )
    run_bot.add_argument(
        "--paper-pnl-log",
        type=Path,
        default=Path("logs/paper_pnl.json"),
        help="Current paper P&L snapshot rewritten when a paper trade opens or closes.",
    )
    run_bot.add_argument(
        "--no-paper-logs",
        action="store_true",
        help="Disable paper trade and paper P&L log files.",
    )
    run_bot.add_argument(
        "--strategy-mode",
        choices=STRATEGY_MODES,
        default=None,
        help="Strategy engine preset.",
    )
    run_bot.add_argument("--strategy-variants", default="")
    run_bot.add_argument("--strategy-paper-trades", default="")
    run_bot.add_argument("--strategy-config", type=Path)


def run_dynamic_bot(args: Any) -> int:
    validate_runtime_args(args)
    config = DynamicBotConfig(
        db_path=args.db,
        seed_pairs_path=args.seed_pairs,
        active_pairs_output=args.active_pairs_output,
        clear_active_on_start=args.clear_active_on_start,
        runtime_seconds=(
            args.runtime_minutes * Decimal("60") if args.runtime_minutes is not None else None
        ),
        discovery=DynamicDiscoveryConfig(
            interval_seconds=args.discovery_interval_seconds,
            market_profile=args.market_profile,
            confidence_threshold=args.confidence_threshold,
            pairs_min_confidence=args.pairs_min_confidence,
            prefilter_threshold=args.prefilter_threshold,
            max_comparisons=args.max_comparisons or None,
            max_candidates_per_polymarket=args.max_candidates_per_polymarket,
            polymarket_search_limit=args.polymarket_search_limit,
            max_polymarket_contracts_per_event=(
                None
                if args.max_polymarket_contracts_per_event == 0
                else args.max_polymarket_contracts_per_event
            ),
            price_validation_threshold=(
                None if args.no_price_validation else args.price_validation_threshold
            ),
            reject_on_price_validation=args.price_validation_mode == "reject",
            no_llm=args.no_llm,
            live_lookback_hours=args.live_lookback_hours,
            live_future_window_minutes=args.live_future_window_minutes,
            live_milestone_limit=args.live_milestone_limit,
            live_milestone_pages=args.live_milestone_pages,
            include_related_event_tickers=args.include_related_event_tickers,
            output_path=args.discovery_output,
            log_path=args.discovery_log,
        ),
        heartbeat=DynamicHeartbeatConfig(
            interval_seconds=heartbeat_interval_seconds(
                args.heartbeat_interval_seconds,
                args.heartbeat_interval_ms,
            ),
            max_active_pairs=args.max_active_pairs,
            active_pair_refresh_seconds=args.active_pair_refresh_seconds,
            lifecycle_check_seconds=args.lifecycle_check_seconds,
            max_venue_spread=args.max_venue_spread,
            min_buy_size=args.min_buy_size,
            min_depth_size=args.min_depth_size,
            depth_window=args.depth_window,
            min_edge=args.min_edge,
            min_fee_adjusted_edge=args.min_fee_adjusted_edge,
            fee_mode=args.fee_mode,
            signal_lookback_minutes=args.signal_lookback_minutes,
            min_mid_edge=args.min_mid_edge,
            min_poly_mid_move=args.min_poly_mid_move,
            min_poly_oi_delta=args.min_poly_oi_delta,
            min_poly_volume_delta=args.min_poly_volume_delta,
            max_kalshi_mid_move=args.max_kalshi_mid_move,
            paper_exit_config=PaperExitConfig(
                exit_edge=args.paper_exit_edge,
                take_profit=args.paper_take_profit,
                stop_loss=args.paper_stop_loss,
                max_hold_minutes=args.paper_max_hold_minutes,
            ),
            drop_failed_pairs_after=args.drop_failed_pairs_after,
            paper_trade_log_path=None if args.no_paper_logs else args.paper_trade_log,
            paper_pnl_log_path=None if args.no_paper_logs else args.paper_pnl_log,
            heartbeat_output=args.heartbeat_output,
            metadata_refresh_seconds=args.metadata_refresh_seconds,
            strategy_config=heartbeat_strategy_config(
                args.strategy_variants,
                args.strategy_paper_trades,
                strategy_mode=args.strategy_mode,
                strategy_config_path=args.strategy_config,
            ),
        ),
    )
    return asyncio.run(run_dynamic_bot_async(config))


def validate_runtime_args(args: Any) -> None:
    non_negative_decimal_args = {
        "--runtime-minutes": args.runtime_minutes,
        "--discovery-interval-seconds": args.discovery_interval_seconds,
        "--heartbeat-interval-seconds": args.heartbeat_interval_seconds,
        "--heartbeat-interval-ms": args.heartbeat_interval_ms,
        "--active-pair-refresh-seconds": args.active_pair_refresh_seconds,
        "--lifecycle-check-seconds": args.lifecycle_check_seconds,
        "--metadata-refresh-seconds": args.metadata_refresh_seconds,
    }
    for name, value in non_negative_decimal_args.items():
        if value is not None and value < 0:
            raise ValueError(f"{name} cannot be negative")
    if args.drop_failed_pairs_after < 0:
        raise ValueError("--drop-failed-pairs-after cannot be negative")
    if args.max_active_pairs is not None and args.max_active_pairs < 1:
        raise ValueError("--max-active-pairs must be at least 1")
    if args.live_lookback_hours < 0:
        raise ValueError("--live-lookback-hours cannot be negative")
    if args.live_future_window_minutes < 0:
        raise ValueError("--live-future-window-minutes cannot be negative")
    if args.live_milestone_limit < 1 or args.live_milestone_limit > 500:
        raise ValueError("--live-milestone-limit must be between 1 and 500")
    if args.live_milestone_pages < 1:
        raise ValueError("--live-milestone-pages must be at least 1")
    if args.max_comparisons < 0:
        raise ValueError("--max-comparisons cannot be negative")
    if not 0 <= args.confidence_threshold <= 1:
        raise ValueError("--confidence-threshold must be between 0 and 1")
    if not 0 <= args.pairs_min_confidence <= 1:
        raise ValueError("--pairs-min-confidence must be between 0 and 1")
    if not 0 <= args.prefilter_threshold <= 1:
        raise ValueError("--prefilter-threshold must be between 0 and 1")
