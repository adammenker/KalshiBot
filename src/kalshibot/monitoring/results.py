from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path
import sqlite3
from time import perf_counter

from kalshibot.monitoring.models import TimedSpreadCheck
from kalshibot.monitoring.output import (
    HeartbeatOutputMode,
    emit_heartbeat_results_locked,
    format_heartbeat_drop,
    format_heartbeat_failure,
    format_heartbeat_summary,
    format_saved_timed_check,
    heartbeat_pair_key,
)
from kalshibot.monitoring.persistence import persist_heartbeat_checks
from kalshibot.monitoring.scheduling import CachedPairMetadata
from kalshibot.paper import PaperExitConfig
from kalshibot.spreads import MarketPair
from kalshibot.strategies import StrategyEngineConfig
from kalshibot.utils import utc_now_iso


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
    successful_checks = [check for check in timed_checks if not isinstance(check, BaseException)]

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


async def process_single_market_failure(
    pair: MarketPair,
    *,
    run_id: str,
    exc: Exception,
    consecutive_failures: int,
    drop_failed_pairs_after: int,
    heartbeat_output: HeartbeatOutputMode,
    interval_seconds: Decimal,
    started_at: float,
    metadata_refreshed: bool,
    output_lock: asyncio.Lock,
) -> bool:
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
            batch_duration_ms=Decimal(str((perf_counter() - started_at) * 1000)),
            metadata_refresh_count=int(metadata_refreshed),
        ),
        output_lock,
    )
    return bool(should_drop)


async def process_single_market_success(
    timed_check: TimedSpreadCheck,
    *,
    db_path: Path,
    db_connection: sqlite3.Connection,
    db_lock: asyncio.Lock,
    output_lock: asyncio.Lock,
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
    heartbeat_output: HeartbeatOutputMode,
    interval_seconds: Decimal,
    started_at: float,
    metadata: CachedPairMetadata,
    metadata_refreshed: bool,
) -> CachedPairMetadata:
    next_metadata = metadata
    if metadata_refreshed:
        next_metadata = CachedPairMetadata(
            open_interest=timed_check.check.polymarket_open_interest,
            volume=timed_check.check.polymarket_volume,
            refreshed_at=started_at,
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
    results = [format_saved_timed_check(timed_check, persistence.save_results[0])]
    await emit_heartbeat_results_locked(
        results,
        heartbeat_output,
        format_heartbeat_summary(
            results,
            run_id=timed_check.run_id,
            observed_at=utc_now_iso(),
            scheduler="per-market",
            output_mode=heartbeat_output,
            active_pairs=1,
            interval_seconds=interval_seconds,
            batch_started_at=timed_check.comparison_started_at,
            batch_duration_ms=Decimal(str((perf_counter() - started_at) * 1000)),
            metadata_refresh_count=int(metadata_refreshed),
        ),
        output_lock,
    )
    return next_metadata
