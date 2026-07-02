from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def timestamp_delta_ms(left: str, right: str) -> Decimal:
    left_at = datetime.fromisoformat(left)
    right_at = datetime.fromisoformat(right)
    return Decimal(str(abs((left_at - right_at).total_seconds() * 1000)))


def first_response_venue(kalshi_received_at: str, polymarket_received_at: str) -> str:
    kalshi_at = datetime.fromisoformat(kalshi_received_at)
    polymarket_at = datetime.fromisoformat(polymarket_received_at)
    if kalshi_at < polymarket_at:
        return "kalshi"
    if polymarket_at < kalshi_at:
        return "polymarket"
    return "tie"


def optional_decimal(value: Any) -> Decimal | None:
    if value in {None, ""}:
        return None
    return Decimal(str(value))


def optional_string(value: Any) -> str | None:
    if value in {None, ""}:
        return None
    return str(value)


def format_ratio(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "0.00%"
    return f"{numerator / denominator:.2%}"


def format_decimal(value: Decimal | None, *, places: int = 4) -> str | None:
    if value is None:
        return None
    return f"{value:.{places}f}"


def parse_csv_set(value: str | None) -> set[str]:
    if not value:
        return set()
    return {part.strip().upper() for part in value.split(",") if part.strip()}


def format_float(value: Any, *, places: int) -> str | None:
    if value is None:
        return None
    return f"{float(value):.{places}f}"
