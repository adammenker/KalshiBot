from __future__ import annotations

from decimal import Decimal

from kalshibot.discovery.models import KalshiDiscoveryMarket
from kalshibot.utils import optional_string

DEFAULT_KALSHI_SIZE_SORT = "none"
KALSHI_SIZE_SORT_FIELDS = (
    "none",
    "volume_24h",
    "volume",
    "open_interest",
    "liquidity",
    "notional_value",
)


def normalize_kalshi_size_sort(sort_by: str | None) -> str:
    normalized = (sort_by or DEFAULT_KALSHI_SIZE_SORT).strip().lower().replace("-", "_")
    if normalized not in KALSHI_SIZE_SORT_FIELDS:
        allowed = ", ".join(KALSHI_SIZE_SORT_FIELDS)
        raise ValueError(f"Kalshi size sort must be one of: {allowed}")
    return normalized


def kalshi_market_size_value(market: KalshiDiscoveryMarket, sort_by: str) -> Decimal:
    normalized = normalize_kalshi_size_sort(sort_by)
    if normalized == "none":
        return Decimal("0")
    return getattr(market, normalized) or Decimal("0")


def sort_kalshi_markets_by_size(
    markets: list[KalshiDiscoveryMarket],
    *,
    sort_by: str,
) -> list[KalshiDiscoveryMarket]:
    normalized = normalize_kalshi_size_sort(sort_by)
    if normalized == "none":
        return list(markets)
    ranked = sorted(
        enumerate(markets),
        key=lambda item: (
            -kalshi_market_size_value(item[1], normalized),
            item[0],
        ),
    )
    return [market for _, market in ranked]


def kalshi_market_size_summary(market: KalshiDiscoveryMarket) -> dict[str, str | None]:
    return {
        "volume_24h": optional_string(market.volume_24h),
        "volume": optional_string(market.volume),
        "open_interest": optional_string(market.open_interest),
        "liquidity": optional_string(market.liquidity),
        "notional_value": optional_string(market.notional_value),
    }
