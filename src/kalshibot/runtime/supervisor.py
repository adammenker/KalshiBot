from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

import requests

from kalshibot.client import KalshiClient
from kalshibot.config import (
    load_config,
    load_local_llm_config,
    load_polymarket_config,
)
from kalshibot.defaults import (
    DEFAULT_DEPTH_WINDOW,
    DEFAULT_DISCOVERY_PRICE_VALIDATION_THRESHOLD,
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
from kalshibot.discovery import discover_market_matches, promote_discovered_matches
from kalshibot.discovery.profiles import resolve_discovery_profile
from kalshibot.market_matcher import OllamaTitleMatcher
from kalshibot.monitoring.fetch import check_spread_concurrently
from kalshibot.monitoring.heartbeat import (
    CachedPairMetadata,
    HeartbeatOutputMode,
    emit_heartbeat_results,
    format_heartbeat_summary,
    heartbeat_pair_key,
    metadata_refresh_due,
    process_batch_results,
)
from kalshibot.paper import PaperExitConfig
from kalshibot.polymarket import PolymarketClient
from kalshibot.runtime.active_pairs import (
    active_pair_key,
    create_active_market_pairs_table,
    load_active_market_pairs,
    mark_pair_inactive,
    market_pairs_from_payload,
    record_pair_failure,
    reset_pair_failure,
    upsert_active_market_pairs,
    write_active_pairs_snapshot,
)
from kalshibot.runtime.live_sports import fetch_live_sports_kalshi_markets
from kalshibot.spreads import DEFAULT_FEE_MODE, FeeMode, MarketPair, load_market_pairs
from kalshibot.storage import connect_database, initialize_database
from kalshibot.strategies import StrategyEngineConfig
from kalshibot.utils import utc_now_iso


@dataclass(frozen=True)
class DynamicDiscoveryConfig:
    interval_seconds: Decimal = Decimal("900")
    market_profile: str = "sports-game-winner"
    confidence_threshold: float = 0.85
    pairs_min_confidence: float = 0.9
    prefilter_threshold: float = 0.18
    max_comparisons: int | None = 50
    max_candidates_per_polymarket: int = 3
    polymarket_search_limit: int = 10
    max_polymarket_contracts_per_event: int | None = None
    price_validation_threshold: Decimal | None = DEFAULT_DISCOVERY_PRICE_VALIDATION_THRESHOLD
    reject_on_price_validation: bool = True
    no_llm: bool = False
    live_lookback_hours: int = 8
    live_future_window_minutes: int = 15
    live_milestone_limit: int = 500
    live_milestone_pages: int = 2
    include_related_event_tickers: bool = False
    output_path: Path | None = Path("data/live_discovered_market_matches.json")
    log_path: Path | None = Path("logs/live_discovery.jsonl")


@dataclass(frozen=True)
class DynamicHeartbeatConfig:
    interval_seconds: Decimal = Decimal("0.25")
    max_active_pairs: int | None = None
    active_pair_refresh_seconds: Decimal = Decimal("2")
    lifecycle_check_seconds: Decimal = Decimal("60")
    max_venue_spread: Decimal = DEFAULT_MAX_VENUE_SPREAD
    min_buy_size: Decimal = DEFAULT_MIN_BUY_SIZE
    min_depth_size: Decimal = DEFAULT_MIN_DEPTH_SIZE
    depth_window: Decimal = DEFAULT_DEPTH_WINDOW
    min_edge: Decimal = DEFAULT_MIN_EDGE
    min_fee_adjusted_edge: Decimal = DEFAULT_MIN_FEE_ADJUSTED_EDGE
    fee_mode: FeeMode = DEFAULT_FEE_MODE
    signal_lookback_minutes: int = DEFAULT_SIGNAL_LOOKBACK_MINUTES
    min_mid_edge: Decimal = DEFAULT_MIN_MID_EDGE
    min_poly_mid_move: Decimal = DEFAULT_MIN_POLY_MID_MOVE
    min_poly_oi_delta: Decimal = DEFAULT_MIN_POLY_OI_DELTA
    min_poly_volume_delta: Decimal = DEFAULT_MIN_POLY_VOLUME_DELTA
    max_kalshi_mid_move: Decimal = DEFAULT_MAX_KALSHI_MID_MOVE
    paper_exit_config: PaperExitConfig = PaperExitConfig()
    drop_failed_pairs_after: int = 3
    paper_trade_log_path: Path | None = Path("logs/paper_trades.jsonl")
    paper_pnl_log_path: Path | None = Path("logs/paper_pnl.json")
    heartbeat_output: HeartbeatOutputMode = "summary"
    metadata_refresh_seconds: Decimal = Decimal("5")
    strategy_config: StrategyEngineConfig | None = None


@dataclass(frozen=True)
class DynamicBotConfig:
    db_path: Path = Path("data/observations.sqlite")
    seed_pairs_path: Path | None = None
    active_pairs_output: Path | None = Path("config/live_active_market_pairs.json")
    clear_active_on_start: bool = False
    runtime_seconds: Decimal | None = None
    discovery: DynamicDiscoveryConfig = DynamicDiscoveryConfig()
    heartbeat: DynamicHeartbeatConfig = DynamicHeartbeatConfig()


async def run_dynamic_bot_async(config: DynamicBotConfig) -> int:
    initialize_database(config.db_path)
    with connect_database(config.db_path) as connection:
        create_active_market_pairs_table(connection)
        if config.clear_active_on_start:
            connection.execute("UPDATE active_market_pairs SET status = 'inactive_stale'")
        if config.seed_pairs_path is not None and config.seed_pairs_path.exists():
            upsert_active_market_pairs(
                connection,
                load_market_pairs(config.seed_pairs_path),
                source="seed_file",
            )
        connection.commit()
        refresh_active_pairs_snapshot(connection, config.active_pairs_output)

    stop_event = asyncio.Event()
    tasks = [
        asyncio.create_task(dynamic_discovery_loop(config, stop_event)),
        asyncio.create_task(dynamic_heartbeat_loop(config, stop_event)),
    ]
    try:
        if config.runtime_seconds is None:
            await asyncio.gather(*tasks)
        else:
            await asyncio.sleep(float(config.runtime_seconds))
            stop_event.set()
            await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        stop_event.set()
        raise
    finally:
        stop_event.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return 0


async def dynamic_discovery_loop(config: DynamicBotConfig, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        started = perf_counter()
        try:
            summary = await asyncio.to_thread(run_live_discovery_cycle, config)
        except Exception as exc:
            summary = {
                "status": "discovery_failed",
                "observed_at": utc_now_iso(),
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        print(json.dumps(summary, sort_keys=True))
        sleep_for = max(0.0, float(config.discovery.interval_seconds) - (perf_counter() - started))
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=sleep_for)
        except asyncio.TimeoutError:
            continue


def run_live_discovery_cycle(config: DynamicBotConfig) -> dict[str, Any]:
    discovered_at = utc_now_iso()
    kalshi_client = KalshiClient(load_config())
    polymarket_client = PolymarketClient(load_polymarket_config())
    resolved_profile = resolve_discovery_profile(
        market_profile=config.discovery.market_profile,
        max_polymarket_contracts_per_event=config.discovery.max_polymarket_contracts_per_event,
        polymarket_outcome_filter=None,
        kalshi_market_types=None,
    )
    live_markets = fetch_live_sports_kalshi_markets(
        kalshi_client,
        now=datetime.now(timezone.utc),
        lookback_hours=config.discovery.live_lookback_hours,
        future_window_minutes=config.discovery.live_future_window_minutes,
        milestone_limit=config.discovery.live_milestone_limit,
        milestone_pages=config.discovery.live_milestone_pages,
        include_related_event_tickers=config.discovery.include_related_event_tickers,
        kalshi_market_types=resolved_profile.kalshi_market_types,
    )
    if not live_markets.markets:
        summary = {
            "status": "discovery",
            "observed_at": discovered_at,
            "live_milestones": len(live_markets.live_milestones),
            "milestones_seen": live_markets.milestones_seen,
            "event_tickers": len(live_markets.event_tickers),
            "kalshi_markets": 0,
            "matches": 0,
            "activated_pairs": 0,
            "reason": "no_live_kalshi_markets",
        }
        append_jsonl(config.discovery.log_path, summary)
        return summary

    llm = None if config.discovery.no_llm else OllamaTitleMatcher(load_local_llm_config())
    result = discover_market_matches(
        polymarket_client=polymarket_client,
        kalshi_client=kalshi_client,
        llm=llm,
        use_llm=not config.discovery.no_llm,
        confidence_threshold=config.discovery.confidence_threshold,
        polymarket_event_limit=25,
        kalshi_limit=len(live_markets.markets),
        kalshi_fetch_limit=len(live_markets.markets),
        kalshi_size_sort_by="volume-24h",
        kalshi_pages=1,
        kalshi_status="open",
        kalshi_series_ticker=None,
        kalshi_include_series=None,
        kalshi_exclude_series=None,
        kalshi_market_types=resolved_profile.kalshi_market_types,
        max_candidates_per_polymarket=config.discovery.max_candidates_per_polymarket,
        max_comparisons=config.discovery.max_comparisons,
        prefilter_threshold=config.discovery.prefilter_threshold,
        discovery_strategy="polymarket-search",
        polymarket_search_limit=config.discovery.polymarket_search_limit,
        max_polymarket_contracts_per_event=(
            None
            if resolved_profile.max_polymarket_contracts_per_event == 0
            else resolved_profile.max_polymarket_contracts_per_event
        ),
        polymarket_outcome_filter=resolved_profile.polymarket_outcome_filter,
        price_validation_threshold=config.discovery.price_validation_threshold,
        reject_on_price_validation=config.discovery.reject_on_price_validation,
        min_match_date=None,
        max_match_date=None,
        seed_kalshi_markets=list(live_markets.markets),
    )
    promoted = promote_discovered_matches(
        result,
        min_confidence=config.discovery.pairs_min_confidence,
        require_price_validation=config.discovery.price_validation_threshold is not None,
    )
    promoted_pairs = market_pairs_from_payload(promoted)
    with connect_database(config.db_path) as connection:
        activated = upsert_active_market_pairs(
            connection,
            promoted_pairs,
            source="live_discovery",
            observed_at=discovered_at,
        )
        connection.commit()
        refresh_active_pairs_snapshot(connection, config.active_pairs_output)

    if config.discovery.output_path is not None:
        config.discovery.output_path.parent.mkdir(parents=True, exist_ok=True)
        config.discovery.output_path.write_text(json.dumps(result, indent=2, sort_keys=True))

    summary = {
        "status": "discovery",
        "observed_at": discovered_at,
        "live_milestones": len(live_markets.live_milestones),
        "milestones_seen": live_markets.milestones_seen,
        "event_tickers": len(live_markets.event_tickers),
        "kalshi_markets": len(live_markets.markets),
        "matches": result.get("summary", {}).get("matches"),
        "activated_pairs": activated,
        "live_data_checked": live_markets.live_data_checked,
        "live_data_rejected": live_markets.live_data_rejected,
        "output": str(config.discovery.output_path) if config.discovery.output_path else None,
        "active_pairs_output": (
            str(config.active_pairs_output) if config.active_pairs_output else None
        ),
    }
    append_jsonl(config.discovery.log_path, summary)
    return summary


async def dynamic_heartbeat_loop(config: DynamicBotConfig, stop_event: asyncio.Event) -> None:
    db_connection = connect_database(config.db_path, check_same_thread=False)
    kalshi_client = KalshiClient(load_config())
    polymarket_client = PolymarketClient(load_polymarket_config())
    consecutive_failures: dict[str, int] = {}
    metadata_cache: dict[str, CachedPairMetadata] = {}
    active_pairs: list[MarketPair] = []
    last_pair_refresh = 0.0
    last_lifecycle_check = 0.0
    iteration = 0
    next_start = perf_counter()
    try:
        while not stop_event.is_set():
            interval = float(config.heartbeat.interval_seconds)
            sleep_for = next_start - perf_counter()
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
            next_start += interval

            now_perf = perf_counter()
            if (
                not active_pairs
                or now_perf - last_pair_refresh
                >= float(config.heartbeat.active_pair_refresh_seconds)
            ):
                active_pairs = load_active_market_pairs(
                    db_connection,
                    limit=config.heartbeat.max_active_pairs,
                )
                last_pair_refresh = now_perf

            if (
                active_pairs
                and now_perf - last_lifecycle_check
                >= float(config.heartbeat.lifecycle_check_seconds)
            ):
                inactive_keys = await asyncio.to_thread(
                    deactivate_closed_pairs,
                    config.db_path,
                    active_pairs,
                )
                if inactive_keys:
                    active_pairs = [
                        pair for pair in active_pairs if active_pair_key(pair) not in inactive_keys
                    ]
                    refresh_active_pairs_snapshot(db_connection, config.active_pairs_output)
                last_lifecycle_check = now_perf

            run_id = str(uuid4())
            if not active_pairs:
                emit_heartbeat_results(
                    [],
                    config.heartbeat.heartbeat_output,
                    {
                        "status": "waiting_for_active_pairs",
                        "run_id": run_id,
                        "observed_at": utc_now_iso(),
                        "scheduler": "dynamic",
                        "active_pairs": 0,
                    },
                )
                iteration += 1
                continue

            batch_started = perf_counter()
            batch_started_at = utc_now_iso()
            refresh_flags = {
                heartbeat_pair_key(pair): metadata_refresh_due(
                    metadata_cache.get(heartbeat_pair_key(pair)),
                    batch_started,
                    config.heartbeat.metadata_refresh_seconds,
                )
                for pair in active_pairs
            }
            timed_checks = await asyncio.gather(
                *[
                    check_spread_concurrently(
                        pair,
                        kalshi_client,
                        polymarket_client,
                        max_venue_spread=config.heartbeat.max_venue_spread,
                        min_buy_size=config.heartbeat.min_buy_size,
                        min_depth_size=config.heartbeat.min_depth_size,
                        depth_window=config.heartbeat.depth_window,
                        min_edge=config.heartbeat.min_edge,
                        min_fee_adjusted_edge=config.heartbeat.min_fee_adjusted_edge,
                        fee_mode=config.heartbeat.fee_mode,
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
                db_path=config.db_path,
                db_connection=db_connection,
                pairs=active_pairs,
                timed_checks=timed_checks,
                run_id=run_id,
                consecutive_failures=consecutive_failures,
                drop_failed_pairs_after=config.heartbeat.drop_failed_pairs_after,
                signal_lookback_minutes=config.heartbeat.signal_lookback_minutes,
                min_mid_edge=config.heartbeat.min_mid_edge,
                min_poly_mid_move=config.heartbeat.min_poly_mid_move,
                min_poly_oi_delta=config.heartbeat.min_poly_oi_delta,
                min_poly_volume_delta=config.heartbeat.min_poly_volume_delta,
                max_kalshi_mid_move=config.heartbeat.max_kalshi_mid_move,
                paper_exit_config=config.heartbeat.paper_exit_config,
                paper_trade_log_path=config.heartbeat.paper_trade_log_path,
                paper_pnl_log_path=config.heartbeat.paper_pnl_log_path,
                metadata_cache=metadata_cache,
                refresh_flags=refresh_flags,
                refreshed_at=batch_started,
                strategy_config=config.heartbeat.strategy_config,
            )
            sync_active_pair_failures(
                db_connection,
                active_pairs,
                timed_checks,
                dropped_pair_keys,
            )
            if dropped_pair_keys:
                active_pairs = [
                    pair for pair in active_pairs if heartbeat_pair_key(pair) not in dropped_pair_keys
                ]
                refresh_active_pairs_snapshot(db_connection, config.active_pairs_output)

            emit_heartbeat_results(
                iteration_results,
                config.heartbeat.heartbeat_output,
                format_heartbeat_summary(
                    iteration_results,
                    run_id=run_id,
                    observed_at=utc_now_iso(),
                    scheduler="dynamic",
                    output_mode=config.heartbeat.heartbeat_output,
                    active_pairs=len(active_pairs),
                    interval_seconds=config.heartbeat.interval_seconds,
                    batch_started_at=batch_started_at,
                    batch_duration_ms=Decimal(str((perf_counter() - batch_started) * 1000)),
                    metadata_refresh_count=sum(
                        1 for should_refresh in refresh_flags.values() if should_refresh
                    ),
                ),
            )
            iteration += 1
    finally:
        db_connection.close()


def sync_active_pair_failures(
    connection: Any,
    pairs: list[MarketPair],
    timed_checks: list[Any],
    dropped_pair_keys: set[str],
) -> None:
    observed_at = utc_now_iso()
    for pair, timed_check in zip(pairs, timed_checks, strict=True):
        key = heartbeat_pair_key(pair)
        if isinstance(timed_check, BaseException):
            record_pair_failure(connection, pair, observed_at=observed_at)
            if key in dropped_pair_keys:
                mark_pair_inactive(
                    connection,
                    key,
                    status="inactive_failed",
                    reason="heartbeat_fetch_failures",
                    observed_at=observed_at,
                )
        else:
            reset_pair_failure(connection, pair, observed_at=observed_at)
    connection.commit()


def deactivate_closed_pairs(db_path: Path, pairs: list[MarketPair]) -> set[str]:
    kalshi_client = KalshiClient(load_config())
    polymarket_client = PolymarketClient(load_polymarket_config())
    inactive: dict[str, str] = {}
    for pair in pairs:
        reason = closed_pair_reason(kalshi_client, polymarket_client, pair)
        if reason:
            inactive[active_pair_key(pair)] = reason
    if not inactive:
        return set()
    with connect_database(db_path) as connection:
        for key, reason in inactive.items():
            mark_pair_inactive(
                connection,
                key,
                status="inactive_closed",
                reason=reason,
            )
        connection.commit()
    return set(inactive)


def closed_pair_reason(
    kalshi_client: KalshiClient,
    polymarket_client: PolymarketClient,
    pair: MarketPair,
) -> str | None:
    try:
        kalshi_response = kalshi_client.get_market(pair.kalshi_ticker)
    except requests.HTTPError as exc:
        return f"kalshi_http_{exc.response.status_code if exc.response else 'error'}"
    market = kalshi_response.get("market", kalshi_response)
    status = str(market.get("status") or "").lower()
    if status and status not in {"open", "active"}:
        return f"kalshi_status_{status}"
    close_time = parse_utc_datetime(market.get("close_time"))
    if close_time is not None and close_time <= datetime.now(timezone.utc):
        return "kalshi_close_time_elapsed"

    try:
        polymarket_market = polymarket_client.get_market_by_clob_token(pair.polymarket_token_id)
    except requests.HTTPError as exc:
        return f"polymarket_http_{exc.response.status_code if exc.response else 'error'}"
    if polymarket_market is None:
        return "polymarket_market_missing"
    if polymarket_market.get("closed") is True:
        return "polymarket_closed"
    if polymarket_market.get("active") is False:
        return "polymarket_inactive"
    if polymarket_market.get("accepting_orders") is False:
        return "polymarket_not_accepting_orders"
    return None


def refresh_active_pairs_snapshot(connection: Any, output_path: Path | None) -> None:
    if output_path is None:
        return
    write_active_pairs_snapshot(output_path, load_active_market_pairs(connection))


def append_jsonl(path: Path | None, payload: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as file:
        file.write(json.dumps(payload, sort_keys=True) + "\n")


def parse_utc_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
