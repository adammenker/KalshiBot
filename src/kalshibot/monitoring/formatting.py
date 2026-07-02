from __future__ import annotations

from typing import Any

from kalshibot.monitoring.models import TimedSpreadCheck
from kalshibot.spreads import format_spread_check
from kalshibot.utils import first_response_venue


def format_timed_spread_check(timed_check: TimedSpreadCheck) -> dict[str, Any]:
    formatted = format_spread_check(timed_check.check)
    formatted.update(
        {
            "run_id": timed_check.run_id,
            "observed_at": timed_check.observed_at,
            "comparison_started_at": timed_check.comparison_started_at,
            "comparison_completed_at": timed_check.comparison_completed_at,
            "kalshi_request_started_at": timed_check.kalshi_request_started_at,
            "kalshi_response_received_at": timed_check.kalshi_response_received_at,
            "kalshi_latency_ms": f"{timed_check.kalshi_latency_ms:.2f}",
            "polymarket_request_started_at": timed_check.polymarket_request_started_at,
            "polymarket_response_received_at": timed_check.polymarket_response_received_at,
            "polymarket_latency_ms": f"{timed_check.polymarket_latency_ms:.2f}",
            "response_skew_ms": f"{timed_check.response_skew_ms:.2f}",
            "first_response_venue": first_response_venue(
                timed_check.kalshi_response_received_at,
                timed_check.polymarket_response_received_at,
            ),
        }
    )
    return formatted
