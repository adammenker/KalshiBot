from __future__ import annotations

import asyncio
from decimal import Decimal
import json
from typing import Literal

import requests

from kalshibot.monitoring.formatting import format_timed_spread_check
from kalshibot.monitoring.observations import ObservationSaveResult
from kalshibot.spreads import MarketPair
from kalshibot.utils import optional_decimal, utc_now_iso

HeartbeatOutputMode = Literal["quiet", "summary", "full"]
HeartbeatScheduler = Literal["fixed-rate", "sleep-after-batch", "per-market"]
HEARTBEAT_OUTPUT_MODES: tuple[HeartbeatOutputMode, ...] = ("quiet", "summary", "full")
HEARTBEAT_SCHEDULERS: tuple[HeartbeatScheduler, ...] = (
    "fixed-rate",
    "sleep-after-batch",
    "per-market",
)


def format_saved_timed_check(
    timed_check: object,
    save_result: ObservationSaveResult,
) -> dict[str, object]:
    formatted = format_timed_spread_check(timed_check)  # type: ignore[arg-type]
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
