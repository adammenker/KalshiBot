from __future__ import annotations

import asyncio
from decimal import Decimal
import json
from pathlib import Path

from kalshibot.client import KalshiClient
from kalshibot.config import load_config, load_polymarket_config
from kalshibot.defaults import (
    DEFAULT_DEPTH_WINDOW,
    DEFAULT_MAX_KALSHI_MID_MOVE,
    DEFAULT_MAX_VENUE_SPREAD,
    DEFAULT_MIN_BUY_SIZE,
    DEFAULT_MIN_DEPTH_SIZE,
    DEFAULT_MIN_EDGE,
    DEFAULT_MIN_FEE_ADJUSTED_EDGE,
    DEFAULT_MIN_MID_EDGE,
    DEFAULT_MIN_POLY_MID_MOVE,
    DEFAULT_MIN_POLY_OI_DELTA,
    DEFAULT_MIN_POLY_VOLUME_DELTA,
    DEFAULT_SIGNAL_LOOKBACK_MINUTES,
)
from kalshibot.monitoring.heartbeat import (
    HEARTBEAT_OUTPUT_MODES,
    HEARTBEAT_SCHEDULERS,
    HeartbeatOutputMode,
    HeartbeatScheduler,
    format_heartbeat_drop,
    format_heartbeat_failure,
    heartbeat_pair_key,
    run_heartbeat_async,
)
from kalshibot.paper import PaperExitConfig
from kalshibot.polymarket import PolymarketClient
from kalshibot.spreads import (
    DEFAULT_FEE_MODE,
    FEE_MODE_CHOICES,
    FeeMode,
    MarketPair,
    check_spread,
    format_spread_check,
    load_market_pairs,
    parse_outcome,
)

__all__ = [
    "add_trading_parsers",
    "build_spread_pairs",
    "format_heartbeat_drop",
    "format_heartbeat_failure",
    "heartbeat_pair_key",
    "heartbeat_interval_seconds",
    "run_heartbeat",
    "run_heartbeat_async",
    "run_spread_check",
]


def add_trading_parsers(subparsers) -> None:
    spread_check = subparsers.add_parser(
        "spread-check",
        help="Compare Kalshi and Polymarket executable buy prices",
    )
    spread_check.add_argument("--pairs", type=Path, help="Path to a JSON market-pair file")
    spread_check.add_argument("--kalshi-ticker", help="Kalshi market ticker for a one-off check")
    spread_check.add_argument("--polymarket-token-id", help="Polymarket CLOB token ID")
    spread_check.add_argument("--outcome", choices=["yes", "no"], default="yes")
    spread_check.add_argument("--label", help="Optional label for one-off check")
    add_spread_filter_args(spread_check)

    heartbeat = subparsers.add_parser(
        "heartbeat",
        help="Record spread observations to SQLite with concurrent venue fetches",
    )
    heartbeat.add_argument(
        "--pairs",
        type=Path,
        default=Path("config/approved_market_pairs.json"),
        help="Path to a JSON market-pair file",
    )
    heartbeat.add_argument(
        "--db",
        type=Path,
        default=Path("data/observations.sqlite"),
        help="SQLite database path",
    )
    heartbeat.add_argument(
        "--iterations",
        type=int,
        default=1,
        help="Number of heartbeat iterations to run. Use 0 to run forever.",
    )
    heartbeat.add_argument(
        "--interval-seconds",
        type=Decimal,
        default=Decimal("5"),
        help="Seconds to wait between heartbeat iterations. Decimals are allowed, e.g. 0.5.",
    )
    heartbeat.add_argument(
        "--interval-ms",
        type=Decimal,
        help="Milliseconds to wait between heartbeat iterations. Overrides --interval-seconds.",
    )
    heartbeat.add_argument(
        "--heartbeat-output",
        choices=HEARTBEAT_OUTPUT_MODES,
        default="summary",
        help="Heartbeat stdout volume. Use full for detailed JSON, summary for one line, quiet for none.",
    )
    heartbeat.add_argument(
        "--scheduler",
        choices=HEARTBEAT_SCHEDULERS,
        default="fixed-rate",
        help=(
            "Heartbeat scheduling mode. fixed-rate starts batches on a cadence, "
            "sleep-after-batch preserves the old behavior, per-market runs independent loops."
        ),
    )
    heartbeat.add_argument(
        "--metadata-refresh-seconds",
        type=Decimal,
        default=Decimal("5"),
        help=(
            "Seconds between Polymarket Gamma/Data metadata refreshes per market. "
            "Orderbooks still refresh every heartbeat. Use 0 to refresh metadata every tick."
        ),
    )
    add_spread_filter_args(heartbeat)
    heartbeat.add_argument(
        "--signal-lookback-minutes",
        type=int,
        default=DEFAULT_SIGNAL_LOOKBACK_MINUTES,
        help="Minutes back to compare Polymarket/Kalshi mid prices and Polymarket volume",
    )
    heartbeat.add_argument(
        "--min-mid-edge",
        type=Decimal,
        default=DEFAULT_MIN_MID_EDGE,
        help="Minimum Polymarket-mid-minus-Kalshi-mid edge required, in dollars",
    )
    heartbeat.add_argument(
        "--min-poly-mid-move",
        type=Decimal,
        default=DEFAULT_MIN_POLY_MID_MOVE,
        help="Minimum Polymarket mid-price increase over the lookback window, in dollars",
    )
    heartbeat.add_argument(
        "--min-poly-oi-delta",
        type=Decimal,
        default=DEFAULT_MIN_POLY_OI_DELTA,
        help="Minimum Polymarket open-interest increase required since the previous observation",
    )
    heartbeat.add_argument(
        "--min-poly-volume-delta",
        type=Decimal,
        default=DEFAULT_MIN_POLY_VOLUME_DELTA,
        help="Minimum Polymarket volume increase over the lookback window",
    )
    heartbeat.add_argument(
        "--max-kalshi-mid-move",
        type=Decimal,
        default=DEFAULT_MAX_KALSHI_MID_MOVE,
        help="Maximum absolute Kalshi mid-price move allowed over the lookback window",
    )
    heartbeat.add_argument(
        "--paper-exit-edge",
        type=Decimal,
        default=None,
        help=(
            "Close an open paper trade when Polymarket-minus-Kalshi edge is at or below "
            "this value. Disabled by default for hold-to-resolution testing."
        ),
    )
    heartbeat.add_argument(
        "--no-paper-exit-edge",
        action="store_true",
        help="Disable paper trade exits based on spread convergence. This is the default.",
    )
    heartbeat.add_argument(
        "--paper-take-profit",
        type=Decimal,
        help="Optional paper-trade take-profit amount per one-contract position",
    )
    heartbeat.add_argument(
        "--paper-stop-loss",
        type=Decimal,
        help="Optional paper-trade stop-loss amount per one-contract position",
    )
    heartbeat.add_argument(
        "--paper-max-hold-minutes",
        type=int,
        help="Optional maximum paper-trade hold time before closing",
    )
    heartbeat.add_argument(
        "--drop-failed-pairs-after",
        type=int,
        default=3,
        help=(
            "Drop a pair from the active heartbeat loop after this many consecutive fetch "
            "failures. Use 0 to keep retrying failed pairs."
        ),
    )
    heartbeat.add_argument(
        "--paper-trade-log",
        type=Path,
        default=Path("logs/paper_trades.jsonl"),
        help="Append-only JSONL log of paper trade open/close events.",
    )
    heartbeat.add_argument(
        "--paper-pnl-log",
        type=Path,
        default=Path("logs/paper_pnl.json"),
        help="Current paper P&L snapshot rewritten when a paper trade opens or closes.",
    )
    heartbeat.add_argument(
        "--no-paper-logs",
        action="store_true",
        help="Disable paper trade and paper P&L log files.",
    )


def add_spread_filter_args(parser) -> None:
    parser.add_argument(
        "--max-venue-spread",
        type=Decimal,
        default=DEFAULT_MAX_VENUE_SPREAD,
        help="Maximum allowed bid/ask spread per venue, in dollars",
    )
    parser.add_argument(
        "--min-buy-size",
        type=Decimal,
        default=DEFAULT_MIN_BUY_SIZE,
        help="Minimum contracts available at each venue's buy price",
    )
    parser.add_argument(
        "--min-depth-size",
        type=Decimal,
        default=DEFAULT_MIN_DEPTH_SIZE,
        help="Minimum contracts available within --depth-window of each venue's buy price",
    )
    parser.add_argument(
        "--depth-window",
        type=Decimal,
        default=DEFAULT_DEPTH_WINDOW,
        help="Dollar window around buy price used for depth checks",
    )
    parser.add_argument(
        "--min-edge",
        type=Decimal,
        default=DEFAULT_MIN_EDGE,
        help="Minimum Polymarket-minus-Kalshi edge required, in dollars",
    )
    parser.add_argument(
        "--min-fee-adjusted-edge",
        type=Decimal,
        default=DEFAULT_MIN_FEE_ADJUSTED_EDGE,
        help="Minimum edge after the configured Kalshi fee model, in dollars",
    )
    parser.add_argument(
        "--fee-mode",
        choices=FEE_MODE_CHOICES,
        default=DEFAULT_FEE_MODE,
        help=(
            "Fee model used for the entry hurdle. Use round-trip for pre-resolution "
            "convergence trading, or entry-only for hold-to-resolution EV testing."
        ),
    )


def run_spread_check(
    pairs_path: Path | None,
    kalshi_ticker: str | None,
    polymarket_token_id: str | None,
    outcome: str,
    label: str | None,
    max_venue_spread: Decimal,
    min_buy_size: Decimal,
    min_depth_size: Decimal,
    depth_window: Decimal,
    min_edge: Decimal,
    min_fee_adjusted_edge: Decimal,
    fee_mode: FeeMode,
) -> int:
    pairs = build_spread_pairs(pairs_path, kalshi_ticker, polymarket_token_id, outcome, label)
    kalshi_client = KalshiClient(load_config())
    polymarket_client = PolymarketClient(load_polymarket_config())

    results = [
        format_spread_check(
            check_spread(
                pair,
                kalshi_client,
                polymarket_client,
                max_venue_spread=max_venue_spread,
                min_buy_size=min_buy_size,
                min_depth_size=min_depth_size,
                depth_window=depth_window,
                min_edge=min_edge,
                min_fee_adjusted_edge=min_fee_adjusted_edge,
                fee_mode=fee_mode,
            )
        )
        for pair in pairs
    ]
    print(json.dumps(results, indent=2, sort_keys=True))
    return 0


def run_heartbeat(
    pairs_path: Path,
    db_path: Path,
    iterations: int,
    interval_seconds: Decimal,
    max_venue_spread: Decimal,
    min_buy_size: Decimal,
    min_depth_size: Decimal,
    depth_window: Decimal,
    min_edge: Decimal,
    min_fee_adjusted_edge: Decimal,
    fee_mode: FeeMode,
    signal_lookback_minutes: int,
    min_mid_edge: Decimal,
    min_poly_mid_move: Decimal,
    min_poly_oi_delta: Decimal,
    min_poly_volume_delta: Decimal,
    max_kalshi_mid_move: Decimal,
    paper_exit_edge: Decimal | None,
    paper_take_profit: Decimal | None,
    paper_stop_loss: Decimal | None,
    paper_max_hold_minutes: int | None,
    drop_failed_pairs_after: int,
    paper_trade_log_path: Path | None,
    paper_pnl_log_path: Path | None,
    heartbeat_output: HeartbeatOutputMode,
    scheduler: HeartbeatScheduler,
    metadata_refresh_seconds: Decimal,
) -> int:
    if iterations < 0:
        raise ValueError("--iterations cannot be negative")
    if interval_seconds < 0:
        raise ValueError("heartbeat interval cannot be negative")
    if drop_failed_pairs_after < 0:
        raise ValueError("--drop-failed-pairs-after cannot be negative")
    if metadata_refresh_seconds < 0:
        raise ValueError("--metadata-refresh-seconds cannot be negative")

    return asyncio.run(
        run_heartbeat_async(
            pairs_path=pairs_path,
            db_path=db_path,
            iterations=iterations,
            interval_seconds=interval_seconds,
            max_venue_spread=max_venue_spread,
            min_buy_size=min_buy_size,
            min_depth_size=min_depth_size,
            depth_window=depth_window,
            min_edge=min_edge,
            min_fee_adjusted_edge=min_fee_adjusted_edge,
            fee_mode=fee_mode,
            signal_lookback_minutes=signal_lookback_minutes,
            min_mid_edge=min_mid_edge,
            min_poly_mid_move=min_poly_mid_move,
            min_poly_oi_delta=min_poly_oi_delta,
            min_poly_volume_delta=min_poly_volume_delta,
            max_kalshi_mid_move=max_kalshi_mid_move,
            paper_exit_config=PaperExitConfig(
                exit_edge=paper_exit_edge,
                take_profit=paper_take_profit,
                stop_loss=paper_stop_loss,
                max_hold_minutes=paper_max_hold_minutes,
            ),
            drop_failed_pairs_after=drop_failed_pairs_after,
            paper_trade_log_path=paper_trade_log_path,
            paper_pnl_log_path=paper_pnl_log_path,
            heartbeat_output=heartbeat_output,
            scheduler=scheduler,
            metadata_refresh_seconds=metadata_refresh_seconds,
        )
    )


def heartbeat_interval_seconds(interval_seconds: Decimal, interval_ms: Decimal | None) -> Decimal:
    if interval_ms is None:
        return interval_seconds
    return interval_ms / Decimal("1000")


def build_spread_pairs(
    pairs_path: Path | None,
    kalshi_ticker: str | None,
    polymarket_token_id: str | None,
    outcome: str,
    label: str | None,
) -> list[MarketPair]:
    if pairs_path:
        return load_market_pairs(pairs_path)
    if not kalshi_ticker or not polymarket_token_id:
        raise ValueError(
            "Provide --pairs, or provide both --kalshi-ticker and --polymarket-token-id"
        )
    return [
        MarketPair(
            label=label or kalshi_ticker,
            kalshi_ticker=kalshi_ticker,
            polymarket_token_id=polymarket_token_id,
            outcome=parse_outcome(outcome),
        )
    ]
