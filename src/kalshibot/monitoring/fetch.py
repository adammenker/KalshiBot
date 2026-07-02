from __future__ import annotations

import asyncio
from decimal import Decimal
from time import perf_counter
from typing import Any, Callable, TypeVar
from uuid import uuid4

from kalshibot.client import KalshiClient
from kalshibot.defaults import (
    DEFAULT_DEPTH_WINDOW,
    DEFAULT_MAX_VENUE_SPREAD,
    DEFAULT_MIN_BUY_SIZE,
    DEFAULT_MIN_DEPTH_SIZE,
    DEFAULT_MIN_EDGE,
    DEFAULT_MIN_FEE_ADJUSTED_EDGE,
)
from kalshibot.monitoring.models import TimedResponse, TimedSpreadCheck
from kalshibot.polymarket import PolymarketClient
from kalshibot.spreads import DEFAULT_FEE_MODE, FeeMode, MarketPair, build_spread_check_from_books
from kalshibot.utils import optional_decimal, timestamp_delta_ms, utc_now_iso

T = TypeVar("T")


async def timed_call(func: Callable[[], T]) -> TimedResponse:
    started_at = utc_now_iso()
    started = perf_counter()
    payload = await asyncio.to_thread(func)
    received = perf_counter()
    received_at = utc_now_iso()
    return TimedResponse(
        started_at=started_at,
        received_at=received_at,
        latency_ms=Decimal(str((received - started) * 1000)),
        payload=payload,
    )


async def check_spread_concurrently(
    pair: MarketPair,
    kalshi_client: KalshiClient,
    polymarket_client: PolymarketClient,
    *,
    max_venue_spread: Decimal = DEFAULT_MAX_VENUE_SPREAD,
    min_buy_size: Decimal = DEFAULT_MIN_BUY_SIZE,
    min_depth_size: Decimal = DEFAULT_MIN_DEPTH_SIZE,
    depth_window: Decimal = DEFAULT_DEPTH_WINDOW,
    min_edge: Decimal = DEFAULT_MIN_EDGE,
    min_fee_adjusted_edge: Decimal = DEFAULT_MIN_FEE_ADJUSTED_EDGE,
    fee_mode: FeeMode = DEFAULT_FEE_MODE,
    polymarket_open_interest: Decimal | None = None,
    polymarket_volume: Decimal | None = None,
    refresh_metadata: bool = True,
    run_id: str | None = None,
) -> TimedSpreadCheck:
    comparison_started_at = utc_now_iso()
    kalshi_task = asyncio.create_task(
        timed_call(lambda: kalshi_client.get_market_orderbook(pair.kalshi_ticker, depth=100))
    )
    polymarket_task = asyncio.create_task(
        timed_call(lambda: polymarket_client.get_order_book(pair.polymarket_token_id))
    )
    oi_task = (
        asyncio.create_task(timed_call(lambda: polymarket_client.get_open_interest(condition_id)))
        if refresh_metadata and (condition_id := pair.polymarket_condition_id)
        else None
    )
    volume_task = (
        asyncio.create_task(
            timed_call(lambda: polymarket_client.get_market_by_clob_token(pair.polymarket_token_id))
        )
        if refresh_metadata
        else None
    )
    responses = await asyncio.gather(
        kalshi_task,
        polymarket_task,
        *([oi_task] if oi_task is not None else []),
        *([volume_task] if volume_task is not None else []),
    )
    kalshi_response = responses[0]
    polymarket_response = responses[1]
    metadata_response_index = 2
    if oi_task is not None:
        polymarket_open_interest = responses[metadata_response_index].payload
        metadata_response_index += 1
    if volume_task is not None:
        polymarket_market = responses[metadata_response_index].payload
        polymarket_volume = polymarket_market_volume(polymarket_market)
    comparison_completed_at = utc_now_iso()

    check = build_spread_check_from_books(
        pair,
        kalshi_response.payload,
        polymarket_response.payload,
        polymarket_client,
        polymarket_open_interest=polymarket_open_interest,
        polymarket_volume=polymarket_volume,
        max_venue_spread=max_venue_spread,
        min_buy_size=min_buy_size,
        min_depth_size=min_depth_size,
        depth_window=depth_window,
        min_edge=min_edge,
        min_fee_adjusted_edge=min_fee_adjusted_edge,
        fee_mode=fee_mode,
    )
    return TimedSpreadCheck(
        run_id=run_id or str(uuid4()),
        check=check,
        observed_at=comparison_completed_at,
        comparison_started_at=comparison_started_at,
        comparison_completed_at=comparison_completed_at,
        kalshi_request_started_at=kalshi_response.started_at,
        kalshi_response_received_at=kalshi_response.received_at,
        kalshi_latency_ms=kalshi_response.latency_ms,
        polymarket_request_started_at=polymarket_response.started_at,
        polymarket_response_received_at=polymarket_response.received_at,
        polymarket_latency_ms=polymarket_response.latency_ms,
        response_skew_ms=timestamp_delta_ms(kalshi_response.received_at, polymarket_response.received_at),
    )


def polymarket_market_volume(market: Any) -> Decimal | None:
    if not isinstance(market, dict):
        return None
    for key in ("volumeNum", "volume", "volume24hr", "volume24hrClob"):
        value = optional_decimal(market.get(key))
        if value is not None:
            return value
    return None
