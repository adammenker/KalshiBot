from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from kalshibot.spreads import SpreadCheck


@dataclass(frozen=True)
class TimedResponse:
    started_at: str
    received_at: str
    latency_ms: Decimal
    payload: Any


@dataclass(frozen=True)
class TimedSpreadCheck:
    run_id: str
    check: SpreadCheck
    observed_at: str
    comparison_started_at: str
    comparison_completed_at: str
    kalshi_request_started_at: str
    kalshi_response_received_at: str
    kalshi_latency_ms: Decimal
    polymarket_request_started_at: str
    polymarket_response_received_at: str
    polymarket_latency_ms: Decimal
    response_skew_ms: Decimal
