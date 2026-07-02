from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
from time import perf_counter
from typing import Literal
from uuid import uuid4

import requests

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
from kalshibot.monitoring.formatting import format_timed_spread_check
from kalshibot.monitoring.models import TimedSpreadCheck
from kalshibot.monitoring.observations import (
    ObservationSaveResult,
    save_observations_on_connection,
)
from kalshibot.paper import (
    PaperExitConfig,
    append_paper_trade_events,
    write_paper_pnl_snapshot,
)
from kalshibot.polymarket import PolymarketClient
from kalshibot.spreads import DEFAULT_FEE_MODE, FeeMode, MarketPair, load_market_pairs
from kalshibot.storage import connect_database, initialize_database
from kalshibot.strategies import (
    StrategyEngineConfig,
    StrategyRecordingResult,
    record_strategy_signals_on_connection,
)
from kalshibot.utils import optional_decimal, utc_now_iso

HeartbeatOutputMode = Literal["quiet", "summary", "full"]
HeartbeatScheduler = Literal["fixed-rate", "sleep-after-batch", "per-market"]
HEARTBEAT_OUTPUT_MODES: tuple[HeartbeatOutputMode, ...] = ("quiet", "summary", "full")
HEARTBEAT_SCHEDULERS: tuple[HeartbeatScheduler, ...] = (
    "fixed-rate",
    "sleep-after-batch",
    "per-market",
)


@dataclass
class CachedPairMetadata:
    open_interest: Decimal | None = None
    volume: Decimal | None = None
    refreshed_at: float | None = None


@dataclass(frozen=True)
class HeartbeatPersistenceResult:
    save_results: list[ObservationSaveResult]
    strategy_recording: StrategyRecordingResult


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


async def process_batch_results(
    *,
    db_path: Path,
    db_connection: sqlite3.Connection | None = None,
    pairs: list[MarketPair],
    timed_checks: list[TimedSpreadCheck | BaseException],
    run_id: str,
    consecutive_failures: dict[str, int],
    drop_failed_pairs_after: int,
    signal_lookback_minutes: int,
    min_mid_edge: Decimal,
    min_poly_mid_move: Decimal,
    min_poly_oi_delta: Decimal,
    min_poly_volume_delta: Decimal,
    max_kalshi_mid_move: Decimal,
    paper_exit_config: PaperExitConfig,
    paper_trade_log_path: Path | None,
    paper_pnl_log_path: Path | None,
    metadata_cache: dict[str, CachedPairMetadata],
    refresh_flags: dict[str, bool],
    refreshed_at: float,
    strategy_config: StrategyEngineConfig | None,
) -> tuple[list[dict[str, object]], set[str]]:
    successful_checks: list[TimedSpreadCheck] = []
    for timed_check in timed_checks:
        if not isinstance(timed_check, BaseException):
            successful_checks.append(timed_check)

    if successful_checks:
        persistence = await asyncio.to_thread(
            persist_heartbeat_checks,
            db_path,
            successful_checks,
            db_connection=db_connection,
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
        )
        save_results = persistence.save_results
    else:
        save_results = []
    save_result_iter = iter(save_results)
    iteration_results: list[dict[str, object]] = []
    dropped_pair_keys: set[str] = set()
    for pair, timed_check in zip(pairs, timed_checks, strict=True):
        pair_key = heartbeat_pair_key(pair)
        if isinstance(timed_check, BaseException):
            failure_count = consecutive_failures.get(pair_key, 0) + 1
            consecutive_failures[pair_key] = failure_count
            iteration_results.append(
                format_heartbeat_failure(pair, run_id, timed_check, failure_count)
            )
            if drop_failed_pairs_after and failure_count >= drop_failed_pairs_after:
                dropped_pair_keys.add(pair_key)
                iteration_results.append(
                    format_heartbeat_drop(pair, run_id, failure_count, drop_failed_pairs_after)
                )
            continue

        consecutive_failures[pair_key] = 0
        if refresh_flags.get(pair_key):
            metadata_cache[pair_key] = CachedPairMetadata(
                open_interest=timed_check.check.polymarket_open_interest,
                volume=timed_check.check.polymarket_volume,
                refreshed_at=refreshed_at,
            )
        iteration_results.append(format_saved_timed_check(timed_check, next(save_result_iter)))

    return iteration_results, dropped_pair_keys


def persist_heartbeat_checks(
    db_path: Path,
    successful_checks: list[TimedSpreadCheck],
    *,
    db_connection: sqlite3.Connection | None,
    signal_lookback_minutes: int,
    min_mid_edge: Decimal,
    min_poly_mid_move: Decimal,
    min_poly_oi_delta: Decimal,
    min_poly_volume_delta: Decimal,
    max_kalshi_mid_move: Decimal,
    paper_exit_config: PaperExitConfig,
    paper_trade_log_path: Path | None,
    paper_pnl_log_path: Path | None,
    strategy_config: StrategyEngineConfig | None,
) -> HeartbeatPersistenceResult:
    if db_connection is None:
        initialize_database(db_path)
        with connect_database(db_path) as connection:
            return persist_heartbeat_checks_on_connection(
                connection,
                db_path,
                successful_checks,
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
            )
    return persist_heartbeat_checks_on_connection(
        db_connection,
        db_path,
        successful_checks,
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
    )


def persist_heartbeat_checks_on_connection(
    connection: sqlite3.Connection,
    db_path: Path,
    successful_checks: list[TimedSpreadCheck],
    *,
    signal_lookback_minutes: int,
    min_mid_edge: Decimal,
    min_poly_mid_move: Decimal,
    min_poly_oi_delta: Decimal,
    min_poly_volume_delta: Decimal,
    max_kalshi_mid_move: Decimal,
    paper_exit_config: PaperExitConfig,
    paper_trade_log_path: Path | None,
    paper_pnl_log_path: Path | None,
    strategy_config: StrategyEngineConfig | None,
) -> HeartbeatPersistenceResult:
    try:
        save_results, legacy_trade_events = save_observations_on_connection(
            connection,
            successful_checks,
            signal_lookback_minutes=signal_lookback_minutes,
            min_mid_edge=min_mid_edge,
            min_poly_mid_move=min_poly_mid_move,
            min_poly_oi_delta=min_poly_oi_delta,
            min_poly_volume_delta=min_poly_volume_delta,
            max_kalshi_mid_move=max_kalshi_mid_move,
            paper_exit_config=paper_exit_config,
        )
        strategy_recording = record_strategy_signals_on_connection(
            connection,
            successful_checks,
            save_results,
            config=strategy_config,
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise

    trade_events = [*legacy_trade_events, *strategy_recording.paper_trade_events]
    if trade_events and paper_trade_log_path is not None:
        append_paper_trade_events(paper_trade_log_path, trade_events)
        if paper_pnl_log_path is not None:
            write_paper_pnl_snapshot(paper_pnl_log_path, db_path)
    return HeartbeatPersistenceResult(
        save_results=save_results,
        strategy_recording=strategy_recording,
    )


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
            result = format_heartbeat_failure(pair, run_id, exc, consecutive_failures)
            should_drop = drop_failed_pairs_after and consecutive_failures >= drop_failed_pairs_after
            results = [result]
            if should_drop:
                results.append(
                    format_heartbeat_drop(pair, run_id, consecutive_failures, drop_failed_pairs_after)
                )
            await emit_heartbeat_results_locked(
                results,
                heartbeat_output,
                format_heartbeat_summary(
                    results,
                    run_id=run_id,
                    observed_at=utc_now_iso(),
                    scheduler="per-market",
                    output_mode=heartbeat_output,
                    active_pairs=1,
                    interval_seconds=interval_seconds,
                    batch_started_at=utc_now_iso(),
                    batch_duration_ms=Decimal(str((perf_counter() - started) * 1000)),
                    metadata_refresh_count=int(should_refresh),
                ),
                output_lock,
            )
            if should_drop:
                return
            iteration += 1
            continue

        consecutive_failures = 0
        if should_refresh:
            metadata = CachedPairMetadata(
                open_interest=timed_check.check.polymarket_open_interest,
                volume=timed_check.check.polymarket_volume,
                refreshed_at=started,
            )
        async with db_lock:
            persistence = await asyncio.to_thread(
                persist_heartbeat_checks,
                db_path,
                [timed_check],
                db_connection=db_connection,
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
            )
        save_results = persistence.save_results
        results = [format_saved_timed_check(timed_check, save_results[0])]
        await emit_heartbeat_results_locked(
            results,
            heartbeat_output,
            format_heartbeat_summary(
                results,
                run_id=run_id,
                observed_at=utc_now_iso(),
                scheduler="per-market",
                output_mode=heartbeat_output,
                active_pairs=1,
                interval_seconds=interval_seconds,
                batch_started_at=timed_check.comparison_started_at,
                batch_duration_ms=Decimal(str((perf_counter() - started) * 1000)),
                metadata_refresh_count=int(should_refresh),
            ),
            output_lock,
        )
        iteration += 1


def format_saved_timed_check(
    timed_check: TimedSpreadCheck,
    save_result: ObservationSaveResult,
) -> dict[str, object]:
    formatted = format_timed_spread_check(timed_check)
    formatted["observation_id"] = save_result.observation_id
    formatted.update(save_result.signal_fields)
    return formatted


async def emit_heartbeat_results_locked(
    results: list[dict[str, object]],
    output_mode: HeartbeatOutputMode,
    summary: dict[str, object],
    output_lock: asyncio.Lock,
) -> None:
    async with output_lock:
        emit_heartbeat_results(results, output_mode, summary)


def emit_heartbeat_results(
    results: list[dict[str, object]],
    output_mode: HeartbeatOutputMode,
    summary: dict[str, object],
) -> None:
    if output_mode == "quiet":
        return
    if output_mode == "full":
        print(json.dumps(results, indent=2, sort_keys=True))
        return
    print(json.dumps(summary, sort_keys=True))


def format_heartbeat_summary(
    results: list[dict[str, object]],
    *,
    run_id: str,
    observed_at: str,
    scheduler: str,
    output_mode: str,
    active_pairs: int,
    interval_seconds: Decimal,
    batch_started_at: str,
    batch_duration_ms: Decimal,
    metadata_refresh_count: int,
) -> dict[str, object]:
    failures = [result for result in results if result.get("status") == "failed"]
    drops = [result for result in results if result.get("status") == "dropped"]
    successful = [
        result
        for result in results
        if result.get("status") not in {"failed", "dropped"}
    ]
    signal_count = sum(1 for result in successful if result.get("passes_filters") is True)
    strategy_signal_count = sum_int_field(successful, "strategy_signal_count")
    strategy_paper_trade_count = sum_int_field(successful, "strategy_paper_trade_count")
    return {
        "status": "heartbeat",
        "run_id": run_id,
        "observed_at": observed_at,
        "batch_started_at": batch_started_at,
        "scheduler": scheduler,
        "output": output_mode,
        "interval_seconds": str(interval_seconds),
        "batch_duration_ms": f"{batch_duration_ms:.2f}",
        "active_pairs": active_pairs,
        "result_count": len(results),
        "success_count": len(successful),
        "failure_count": len(failures),
        "dropped_count": len(drops),
        "signal_count": signal_count,
        "strategy_signal_count": strategy_signal_count,
        "strategy_paper_trade_count": strategy_paper_trade_count,
        "metadata_refresh_count": metadata_refresh_count,
        "max_raw_edge": max_decimal_string(results, "polymarket_minus_kalshi"),
        "max_fee_adjusted_edge": max_decimal_string(results, "fee_adjusted_edge"),
        "avg_kalshi_latency_ms": average_decimal_string(results, "kalshi_latency_ms"),
        "avg_polymarket_latency_ms": average_decimal_string(results, "polymarket_latency_ms"),
        "avg_response_skew_ms": average_decimal_string(results, "response_skew_ms"),
    }


def sum_int_field(results: list[dict[str, object]], key: str) -> int:
    total = 0
    for result in results:
        value = result.get(key)
        if isinstance(value, bool) or value is None:
            continue
        try:
            total += int(value)
        except (TypeError, ValueError):
            continue
    return total


def metadata_refresh_due(
    cache: CachedPairMetadata | None,
    now: float,
    metadata_refresh_seconds: Decimal,
) -> bool:
    if metadata_refresh_seconds <= 0:
        return True
    if cache is None or cache.refreshed_at is None:
        return True
    return now - cache.refreshed_at >= float(metadata_refresh_seconds)


def max_decimal_string(results: list[dict[str, object]], key: str) -> str | None:
    values = [
        value
        for value in (optional_decimal(result.get(key)) for result in results)
        if value is not None
    ]
    return str(max(values)) if values else None


def average_decimal_string(results: list[dict[str, object]], key: str) -> str | None:
    values = [
        value
        for value in (optional_decimal(result.get(key)) for result in results)
        if value is not None
    ]
    if not values:
        return None
    return f"{sum(values) / Decimal(len(values)):.2f}"


def heartbeat_pair_key(pair: MarketPair) -> str:
    return f"{pair.kalshi_ticker}:{pair.polymarket_token_id}:{pair.outcome}"


def format_heartbeat_failure(
    pair: MarketPair,
    run_id: str,
    error: BaseException,
    consecutive_failures: int = 1,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "status": "failed",
        "run_id": run_id,
        "observed_at": utc_now_iso(),
        "label": pair.label,
        "outcome": pair.outcome,
        "kalshi_ticker": pair.kalshi_ticker,
        "kalshi_url": pair.kalshi_url or "",
        "polymarket_token_id": pair.polymarket_token_id,
        "polymarket_url": pair.polymarket_url or "",
        "polymarket_condition_id": pair.polymarket_condition_id,
        "consecutive_failures": consecutive_failures,
        "error_type": type(error).__name__,
        "error": str(error),
    }
    if isinstance(error, requests.HTTPError) and error.response is not None:
        payload["http_status_code"] = error.response.status_code
        payload["response_text"] = error.response.text[:500]
    return payload


def format_heartbeat_drop(
    pair: MarketPair,
    run_id: str,
    consecutive_failures: int,
    threshold: int,
) -> dict[str, object]:
    return {
        "status": "dropped",
        "run_id": run_id,
        "observed_at": utc_now_iso(),
        "label": pair.label,
        "outcome": pair.outcome,
        "kalshi_ticker": pair.kalshi_ticker,
        "kalshi_url": pair.kalshi_url or "",
        "polymarket_token_id": pair.polymarket_token_id,
        "polymarket_url": pair.polymarket_url or "",
        "polymarket_condition_id": pair.polymarket_condition_id,
        "consecutive_failures": consecutive_failures,
        "drop_failed_pairs_after": threshold,
    }
