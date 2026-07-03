from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path
import sqlite3
from time import perf_counter
from uuid import uuid4

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
from kalshibot.monitoring.fetch import check_spread_concurrently
from kalshibot.monitoring.output import (
    HEARTBEAT_OUTPUT_MODES as HEARTBEAT_OUTPUT_MODES,
    HEARTBEAT_SCHEDULERS as HEARTBEAT_SCHEDULERS,
    HeartbeatOutputMode,
    HeartbeatScheduler,
    emit_heartbeat_results as emit_heartbeat_results,
    format_heartbeat_drop as format_heartbeat_drop,
    format_heartbeat_failure as format_heartbeat_failure,
    format_heartbeat_summary as format_heartbeat_summary,
    heartbeat_pair_key as heartbeat_pair_key,
)
from kalshibot.monitoring.results import (
    process_batch_results as process_batch_results,
    process_single_market_failure,
    process_single_market_success,
)
from kalshibot.monitoring.scheduling import (
    CachedPairMetadata as CachedPairMetadata,
    metadata_refresh_due as metadata_refresh_due,
)
from kalshibot.paper import PaperExitConfig
from kalshibot.polymarket import PolymarketClient
from kalshibot.spreads import DEFAULT_FEE_MODE, FeeMode, MarketPair, load_market_pairs
from kalshibot.storage import connect_database, initialize_database
from kalshibot.strategies import StrategyEngineConfig
from kalshibot.utils import utc_now_iso


async def run_heartbeat_async(
    *,
    pairs_path: Path,
    db_path: Path,
    iterations: int,
    interval_seconds: Decimal,
    max_venue_spread: Decimal = DEFAULT_MAX_VENUE_SPREAD,
    min_buy_size: Decimal = DEFAULT_MIN_BUY_SIZE,
    min_depth_size: Decimal = DEFAULT_MIN_DEPTH_SIZE,
    depth_window: Decimal = DEFAULT_DEPTH_WINDOW,
    min_edge: Decimal = DEFAULT_MIN_EDGE,
    min_fee_adjusted_edge: Decimal = DEFAULT_MIN_FEE_ADJUSTED_EDGE,
    fee_mode: FeeMode = DEFAULT_FEE_MODE,
    signal_lookback_minutes: int = DEFAULT_SIGNAL_LOOKBACK_MINUTES,
    min_mid_edge: Decimal = DEFAULT_MIN_MID_EDGE,
    min_poly_mid_move: Decimal = DEFAULT_MIN_POLY_MID_MOVE,
    min_poly_oi_delta: Decimal = DEFAULT_MIN_POLY_OI_DELTA,
    min_poly_volume_delta: Decimal = DEFAULT_MIN_POLY_VOLUME_DELTA,
    max_kalshi_mid_move: Decimal = DEFAULT_MAX_KALSHI_MID_MOVE,
    paper_exit_config: PaperExitConfig,
    drop_failed_pairs_after: int,
    paper_trade_log_path: Path | None = Path("logs/paper_trades.jsonl"),
    paper_pnl_log_path: Path | None = Path("logs/paper_pnl.json"),
    heartbeat_output: HeartbeatOutputMode = "summary",
    scheduler: HeartbeatScheduler = "fixed-rate",
    metadata_refresh_seconds: Decimal = Decimal("5"),
    strategy_config: StrategyEngineConfig | None = None,
) -> int:
    if scheduler == "per-market":
        return await run_per_market_heartbeat_async(
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
            paper_exit_config=paper_exit_config,
            drop_failed_pairs_after=drop_failed_pairs_after,
            paper_trade_log_path=paper_trade_log_path,
            paper_pnl_log_path=paper_pnl_log_path,
            heartbeat_output=heartbeat_output,
            metadata_refresh_seconds=metadata_refresh_seconds,
            strategy_config=strategy_config,
        )

    return await run_batch_heartbeat_async(
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
        paper_exit_config=paper_exit_config,
        drop_failed_pairs_after=drop_failed_pairs_after,
        paper_trade_log_path=paper_trade_log_path,
        paper_pnl_log_path=paper_pnl_log_path,
        heartbeat_output=heartbeat_output,
        scheduler=scheduler,
        metadata_refresh_seconds=metadata_refresh_seconds,
        strategy_config=strategy_config,
    )


async def run_batch_heartbeat_async(
    *,
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
    paper_exit_config: PaperExitConfig,
    drop_failed_pairs_after: int,
    paper_trade_log_path: Path | None,
    paper_pnl_log_path: Path | None,
    heartbeat_output: HeartbeatOutputMode,
    scheduler: HeartbeatScheduler,
    metadata_refresh_seconds: Decimal,
    strategy_config: StrategyEngineConfig | None,
) -> int:
    active_pairs = load_market_pairs(pairs_path)
    consecutive_failures: dict[str, int] = {}
    metadata_cache: dict[str, CachedPairMetadata] = {}
    initialize_database(db_path)
    db_connection = connect_database(db_path, check_same_thread=False)
    kalshi_client = KalshiClient(load_config())
    polymarket_client = PolymarketClient(load_polymarket_config())

    iteration = 0
    interval = float(interval_seconds)
    next_start = perf_counter()
    try:
        while iterations == 0 or iteration < iterations:
            if scheduler == "fixed-rate":
                sleep_for = next_start - perf_counter()
                if sleep_for > 0:
                    await asyncio.sleep(sleep_for)
                next_start += interval

            run_id = str(uuid4())
            if not active_pairs:
                emit_heartbeat_results(
                    [],
                    heartbeat_output,
                    {
                        "status": "stopped",
                        "run_id": run_id,
                        "observed_at": utc_now_iso(),
                        "reason": "no_active_pairs",
                    },
                )
                break

            batch_started = perf_counter()
            batch_started_at = utc_now_iso()
            refresh_flags = {
                heartbeat_pair_key(pair): metadata_refresh_due(
                    metadata_cache.get(heartbeat_pair_key(pair)),
                    batch_started,
                    metadata_refresh_seconds,
                )
                for pair in active_pairs
            }
            timed_checks = await asyncio.gather(
                *[
                    check_spread_concurrently(
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
                        polymarket_open_interest=metadata_cache.get(
                            heartbeat_pair_key(pair),
                            CachedPairMetadata(),
                        ).open_interest,
                        polymarket_volume=metadata_cache.get(
                            heartbeat_pair_key(pair),
                            CachedPairMetadata(),
                        ).volume,
                        refresh_metadata=refresh_flags[heartbeat_pair_key(pair)],
                        run_id=run_id,
                    )
                    for pair in active_pairs
                ],
                return_exceptions=True,
            )

            iteration_results, dropped_pair_keys = await process_batch_results(
                db_path=db_path,
                db_connection=db_connection,
                pairs=active_pairs,
                timed_checks=timed_checks,
                run_id=run_id,
                consecutive_failures=consecutive_failures,
                drop_failed_pairs_after=drop_failed_pairs_after,
                signal_lookback_minutes=signal_lookback_minutes,
                min_mid_edge=min_mid_edge,
                min_poly_mid_move=min_poly_mid_move,
                min_poly_oi_delta=min_poly_oi_delta,
                min_poly_volume_delta=min_poly_volume_delta,
                max_kalshi_mid_move=max_kalshi_mid_move,
                paper_exit_config=paper_exit_config,
                paper_trade_log_path=paper_trade_log_path,
                paper_pnl_log_path=paper_pnl_log_path,
                metadata_cache=metadata_cache,
                refresh_flags=refresh_flags,
                refreshed_at=batch_started,
                strategy_config=strategy_config,
            )

            if dropped_pair_keys:
                active_pairs = [
                    pair for pair in active_pairs if heartbeat_pair_key(pair) not in dropped_pair_keys
                ]

            batch_completed = perf_counter()
            summary = format_heartbeat_summary(
                iteration_results,
                run_id=run_id,
                observed_at=utc_now_iso(),
                scheduler=scheduler,
                output_mode=heartbeat_output,
                active_pairs=len(active_pairs),
                interval_seconds=interval_seconds,
                batch_started_at=batch_started_at,
                batch_duration_ms=Decimal(str((batch_completed - batch_started) * 1000)),
                metadata_refresh_count=sum(
                    1 for should_refresh in refresh_flags.values() if should_refresh
                ),
            )
            emit_heartbeat_results(iteration_results, heartbeat_output, summary)

            iteration += 1
            if scheduler == "sleep-after-batch" and (iterations == 0 or iteration < iterations):
                await asyncio.sleep(interval)
    finally:
        db_connection.close()

    return 0


async def run_per_market_heartbeat_async(
    *,
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
    paper_exit_config: PaperExitConfig,
    drop_failed_pairs_after: int,
    paper_trade_log_path: Path | None,
    paper_pnl_log_path: Path | None,
    heartbeat_output: HeartbeatOutputMode,
    metadata_refresh_seconds: Decimal,
    strategy_config: StrategyEngineConfig | None,
) -> int:
    pairs = load_market_pairs(pairs_path)
    initialize_database(db_path)
    db_connection = connect_database(db_path, check_same_thread=False)
    kalshi_client = KalshiClient(load_config())
    polymarket_client = PolymarketClient(load_polymarket_config())
    db_lock = asyncio.Lock()
    output_lock = asyncio.Lock()
    try:
        tasks = [
            asyncio.create_task(
                run_market_heartbeat_loop(
                    pair,
                    db_path=db_path,
                    db_connection=db_connection,
                    iterations=iterations,
                    interval_seconds=interval_seconds,
                    kalshi_client=kalshi_client,
                    polymarket_client=polymarket_client,
                    db_lock=db_lock,
                    output_lock=output_lock,
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
                    paper_exit_config=paper_exit_config,
                    drop_failed_pairs_after=drop_failed_pairs_after,
                    paper_trade_log_path=paper_trade_log_path,
                    paper_pnl_log_path=paper_pnl_log_path,
                    heartbeat_output=heartbeat_output,
                    metadata_refresh_seconds=metadata_refresh_seconds,
                    strategy_config=strategy_config,
                )
            )
            for pair in pairs
        ]
        await asyncio.gather(*tasks)
    finally:
        db_connection.close()
    return 0


async def run_market_heartbeat_loop(
    pair: MarketPair,
    *,
    db_path: Path,
    db_connection: sqlite3.Connection,
    iterations: int,
    interval_seconds: Decimal,
    kalshi_client: KalshiClient,
    polymarket_client: PolymarketClient,
    db_lock: asyncio.Lock,
    output_lock: asyncio.Lock,
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
    paper_exit_config: PaperExitConfig,
    drop_failed_pairs_after: int,
    paper_trade_log_path: Path | None,
    paper_pnl_log_path: Path | None,
    heartbeat_output: HeartbeatOutputMode,
    metadata_refresh_seconds: Decimal,
    strategy_config: StrategyEngineConfig | None,
) -> None:
    metadata = CachedPairMetadata()
    consecutive_failures = 0
    interval = float(interval_seconds)
    next_start = perf_counter()
    iteration = 0
    while iterations == 0 or iteration < iterations:
        sleep_for = next_start - perf_counter()
        if sleep_for > 0:
            await asyncio.sleep(sleep_for)
        next_start += interval

        run_id = str(uuid4())
        started = perf_counter()
        should_refresh = metadata_refresh_due(metadata, started, metadata_refresh_seconds)
        try:
            timed_check = await check_spread_concurrently(
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
                polymarket_open_interest=metadata.open_interest,
                polymarket_volume=metadata.volume,
                refresh_metadata=should_refresh,
                run_id=run_id,
            )
        except Exception as exc:
            consecutive_failures += 1
            should_drop = await process_single_market_failure(
                pair,
                run_id=run_id,
                exc=exc,
                consecutive_failures=consecutive_failures,
                drop_failed_pairs_after=drop_failed_pairs_after,
                heartbeat_output=heartbeat_output,
                interval_seconds=interval_seconds,
                started_at=started,
                metadata_refreshed=should_refresh,
                output_lock=output_lock,
            )
            if should_drop:
                return
            iteration += 1
            continue

        consecutive_failures = 0
        metadata = await process_single_market_success(
            timed_check,
            db_path=db_path,
            db_connection=db_connection,
            db_lock=db_lock,
            output_lock=output_lock,
            signal_lookback_minutes=signal_lookback_minutes,
            min_mid_edge=min_mid_edge,
            min_poly_mid_move=min_poly_mid_move,
            min_poly_oi_delta=min_poly_oi_delta,
            min_poly_volume_delta=min_poly_volume_delta,
            max_kalshi_mid_move=max_kalshi_mid_move,
            paper_exit_config=paper_exit_config,
            paper_trade_log_path=paper_trade_log_path,
            paper_pnl_log_path=paper_pnl_log_path,
            strategy_config=strategy_config,
            heartbeat_output=heartbeat_output,
            interval_seconds=interval_seconds,
            started_at=started,
            metadata=metadata,
            metadata_refreshed=should_refresh,
        )
        iteration += 1
