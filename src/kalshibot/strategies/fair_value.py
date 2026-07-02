from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

from kalshibot.spreads import SpreadCheck
from kalshibot.strategies.context import StrategyContext

POLYMARKET_MID_PROVIDER_ID = "polymarket_mid"
POLYMARKET_BID_CONSERVATIVE_PROVIDER_ID = "polymarket_bid_conservative"


@dataclass(frozen=True)
class FairValueEstimate:
    provider_id: str
    value: Decimal | None
    confidence: Decimal | None = None
    reasons: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


class FairValueProvider(Protocol):
    provider_id: str

    def estimate(self, context: StrategyContext) -> FairValueEstimate:
        """Estimate current fair value for the configured Kalshi contract side."""


@dataclass(frozen=True)
class PolymarketMidFairValueProvider:
    provider_id: str = POLYMARKET_MID_PROVIDER_ID

    def estimate(self, context: StrategyContext) -> FairValueEstimate:
        value = context.check.polymarket_mid_price
        if value is None:
            return FairValueEstimate(
                provider_id=self.provider_id,
                value=None,
                reasons=("polymarket_mid_missing",),
            )
        return FairValueEstimate(
            provider_id=self.provider_id,
            value=value,
            confidence=Decimal("0.60"),
            reasons=("polymarket_mid_as_fair_value",),
            metadata={
                "polymarket_buy_price": str(context.check.polymarket_buy_price),
                "polymarket_sell_price": str(context.check.polymarket_sell_price)
                if context.check.polymarket_sell_price is not None
                else None,
            },
        )


@dataclass(frozen=True)
class PolymarketBidConservativeFairValueProvider:
    provider_id: str = POLYMARKET_BID_CONSERVATIVE_PROVIDER_ID

    def estimate(self, context: StrategyContext) -> FairValueEstimate:
        value = context.check.polymarket_sell_price
        if value is None:
            return FairValueEstimate(
                provider_id=self.provider_id,
                value=None,
                reasons=("polymarket_bid_missing",),
            )
        return FairValueEstimate(
            provider_id=self.provider_id,
            value=value,
            confidence=Decimal("0.75"),
            reasons=("polymarket_bid_as_conservative_fair_value",),
            metadata={
                "polymarket_buy_price": str(context.check.polymarket_buy_price),
                "polymarket_sell_price": str(context.check.polymarket_sell_price),
            },
        )


def fair_value_from_check(check: SpreadCheck, provider_id: str | None) -> Decimal | None:
    if provider_id == POLYMARKET_BID_CONSERVATIVE_PROVIDER_ID:
        return check.polymarket_sell_price
    return check.polymarket_mid_price
