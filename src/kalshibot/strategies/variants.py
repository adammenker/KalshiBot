from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from kalshibot.strategies.base import StrategyDecision, StrategySignalType
from kalshibot.strategies.common import (
    decimal_parameter,
    direction_for_side,
    fair_value_metadata,
    signal_type_parameter,
    strategy_parameters,
)
from kalshibot.strategies.context import StrategyContext
from kalshibot.strategies.fair_value import (
    FairValueProvider,
    POLYMARKET_MID_PROVIDER_ID,
    PolymarketBidConservativeFairValueProvider,
    PolymarketMidFairValueProvider,
)
from kalshibot.strategies.ids import (
    HOLD_TO_RESOLUTION_EV_POLY_BID_CONSERVATIVE_ID,
    HOLD_TO_RESOLUTION_EV_POLY_MID_ID,
    LEGACY_FEE_ADJUSTED_EDGE_ID,
    LOOSE_POLY_LEAD_SCOUT_ID,
    PERSISTENT_MID_GAP_ID,
)
from kalshibot.utils import optional_decimal

@dataclass(frozen=True)
class LegacyFeeAdjustedEdgeStrategy:
    strategy_id: str = LEGACY_FEE_ADJUSTED_EDGE_ID
    strategy_version: str = "1"

    def evaluate(self, context: StrategyContext) -> StrategyDecision:
        check = context.check
        if not check.passes_filters:
            return StrategyDecision.none(
                strategy_id=self.strategy_id,
                strategy_version=self.strategy_version,
                rejection_reasons=check.filter_reasons,
            )
        return StrategyDecision(
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            signal_type="paper_open",
            side=check.outcome,
            direction=direction_for_side(check.outcome),
            confidence=Decimal("1"),
            fair_value=check.polymarket_mid_price,
            entry_price=check.kalshi_buy_price,
            edge=check.polymarket_minus_kalshi,
            fee_adjusted_edge=check.fee_adjusted_edge,
            reasons=("passes_existing_heartbeat_filters",),
            metadata={
                "source": "existing_heartbeat_filters",
                "fair_value_provider": POLYMARKET_MID_PROVIDER_ID,
            },
        )


@dataclass(frozen=True)
class LoosePolymarketLeadScoutStrategy:
    strategy_id: str = LOOSE_POLY_LEAD_SCOUT_ID
    strategy_version: str = "1"

    def evaluate(self, context: StrategyContext) -> StrategyDecision:
        min_poly_mid_move = decimal_parameter(
            context,
            self.strategy_id,
            "min_poly_mid_move",
            Decimal("0.01"),
        )
        max_kalshi_mid_move = decimal_parameter(
            context,
            self.strategy_id,
            "max_kalshi_mid_move",
            Decimal("0.02"),
        )
        min_mid_edge = decimal_parameter(
            context,
            self.strategy_id,
            "min_mid_edge",
            Decimal("0"),
        )
        poly_mid_delta = optional_decimal(context.metrics.get("polymarket_mid_delta"))
        kalshi_mid_delta = optional_decimal(context.metrics.get("kalshi_mid_delta"))
        mid_edge = context.check.polymarket_mid_minus_kalshi_mid
        rejection_reasons: list[str] = []
        if poly_mid_delta is None:
            rejection_reasons.append("polymarket_mid_history_missing")
        elif poly_mid_delta < min_poly_mid_move:
            rejection_reasons.append("polymarket_mid_move_too_small")
        if kalshi_mid_delta is None:
            rejection_reasons.append("kalshi_mid_history_missing")
        elif abs(kalshi_mid_delta) > max_kalshi_mid_move:
            rejection_reasons.append("kalshi_mid_moved_too_much")
        if mid_edge is None:
            rejection_reasons.append("mid_edge_missing")
        elif mid_edge < min_mid_edge:
            rejection_reasons.append("mid_edge_below_minimum")
        if rejection_reasons:
            return StrategyDecision.none(
                strategy_id=self.strategy_id,
                strategy_version=self.strategy_version,
                rejection_reasons=tuple(rejection_reasons),
            )
        return StrategyDecision(
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            signal_type="shadow",
            side=context.check.outcome,
            direction=direction_for_side(context.check.outcome),
            confidence=Decimal("0.5"),
            score=poly_mid_delta,
            fair_value=context.check.polymarket_mid_price,
            entry_price=context.check.kalshi_buy_price,
            edge=context.check.polymarket_minus_kalshi,
            fee_adjusted_edge=context.check.fee_adjusted_edge,
            reasons=("polymarket_mid_led", "kalshi_lagged"),
            metadata={
                "fair_value_provider": POLYMARKET_MID_PROVIDER_ID,
                "min_poly_mid_move": str(min_poly_mid_move),
                "max_kalshi_mid_move": str(max_kalshi_mid_move),
                "min_mid_edge": str(min_mid_edge),
            },
        )


@dataclass(frozen=True)
class PersistentMidGapStrategy:
    strategy_id: str = PERSISTENT_MID_GAP_ID
    strategy_version: str = "1"

    def evaluate(self, context: StrategyContext) -> StrategyDecision:
        min_mid_edge = decimal_parameter(
            context,
            self.strategy_id,
            "min_mid_edge",
            Decimal("0.03"),
        )
        min_prior_hits = int(strategy_parameters(context, self.strategy_id).get("min_prior_hits", 2))
        current_mid_edge = context.check.polymarket_mid_minus_kalshi_mid
        if current_mid_edge is None or current_mid_edge < min_mid_edge:
            return StrategyDecision.none(
                strategy_id=self.strategy_id,
                strategy_version=self.strategy_version,
                rejection_reasons=("current_mid_edge_below_minimum",),
            )
        prior_edges = [
            optional_decimal(row.get("polymarket_mid_minus_kalshi_mid"))
            for row in context.history
        ]
        prior_hits = sum(1 for edge in prior_edges if edge is not None and edge >= min_mid_edge)
        if prior_hits < min_prior_hits:
            return StrategyDecision.none(
                strategy_id=self.strategy_id,
                strategy_version=self.strategy_version,
                rejection_reasons=("insufficient_persistent_gap_history",),
                metadata={"prior_hits": prior_hits, "required_prior_hits": min_prior_hits},
            )
        return StrategyDecision(
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            signal_type="shadow",
            side=context.check.outcome,
            direction=direction_for_side(context.check.outcome),
            confidence=Decimal("0.6"),
            score=current_mid_edge,
            fair_value=context.check.polymarket_mid_price,
            entry_price=context.check.kalshi_buy_price,
            edge=context.check.polymarket_minus_kalshi,
            fee_adjusted_edge=context.check.fee_adjusted_edge,
            reasons=("persistent_mid_gap",),
            metadata={
                "fair_value_provider": POLYMARKET_MID_PROVIDER_ID,
                "min_mid_edge": str(min_mid_edge),
                "prior_hits": prior_hits,
                "required_prior_hits": min_prior_hits,
            },
        )


@dataclass(frozen=True)
class HoldToResolutionEvStrategy:
    strategy_id: str
    strategy_version: str
    fair_value_provider: FairValueProvider
    default_signal_type: StrategySignalType

    def evaluate(self, context: StrategyContext) -> StrategyDecision:
        estimate = self.fair_value_provider.estimate(context)
        if estimate.value is None:
            return StrategyDecision.none(
                strategy_id=self.strategy_id,
                strategy_version=self.strategy_version,
                rejection_reasons=estimate.reasons or ("fair_value_missing",),
                metadata=fair_value_metadata(context, estimate),
            )

        check = context.check
        min_fee_adjusted_edge = decimal_parameter(
            context,
            self.strategy_id,
            "min_fee_adjusted_edge",
            Decimal("0"),
        )
        signal_type = signal_type_parameter(
            context,
            self.strategy_id,
            "signal_type",
            self.default_signal_type,
        )
        edge = estimate.value - check.kalshi_buy_price
        total_hold_to_resolution_ev = edge * check.target_size - check.kalshi_entry_fee
        fee_adjusted_edge = total_hold_to_resolution_ev / check.target_size
        if fee_adjusted_edge < min_fee_adjusted_edge:
            if edge > 0:
                return StrategyDecision(
                    strategy_id=self.strategy_id,
                    strategy_version=self.strategy_version,
                    signal_type="shadow",
                    side=check.outcome,
                    direction=direction_for_side(check.outcome),
                    confidence=estimate.confidence,
                    score=fee_adjusted_edge,
                    fair_value=estimate.value,
                    entry_price=check.kalshi_buy_price,
                    edge=edge,
                    fee_adjusted_edge=fee_adjusted_edge,
                    reasons=("positive_fair_value_edge", *estimate.reasons),
                    rejection_reasons=("hold_to_resolution_ev_below_threshold",),
                    metadata={
                        **fair_value_metadata(context, estimate),
                        "min_fee_adjusted_edge": str(min_fee_adjusted_edge),
                        "entry_fee": str(check.kalshi_entry_fee),
                        "quantity": str(check.target_size),
                        "total_hold_to_resolution_ev": str(total_hold_to_resolution_ev),
                        "filter_reasons": check.filter_reasons,
                    },
                )
            return StrategyDecision.none(
                strategy_id=self.strategy_id,
                strategy_version=self.strategy_version,
                rejection_reasons=("hold_to_resolution_ev_below_threshold",),
                metadata={
                    **fair_value_metadata(context, estimate),
                    "min_fee_adjusted_edge": str(min_fee_adjusted_edge),
                    "edge": str(edge),
                    "fee_adjusted_edge": str(fee_adjusted_edge),
                    "total_hold_to_resolution_ev": str(total_hold_to_resolution_ev),
                    "filter_reasons": check.filter_reasons,
                },
            )

        return StrategyDecision(
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            signal_type=signal_type,
            side=check.outcome,
            direction=direction_for_side(check.outcome),
            confidence=estimate.confidence,
            score=fee_adjusted_edge,
            fair_value=estimate.value,
            entry_price=check.kalshi_buy_price,
            edge=edge,
            fee_adjusted_edge=fee_adjusted_edge,
            reasons=("positive_hold_to_resolution_ev", *estimate.reasons),
            metadata={
                **fair_value_metadata(context, estimate),
                "min_fee_adjusted_edge": str(min_fee_adjusted_edge),
                "entry_fee": str(check.kalshi_entry_fee),
                "quantity": str(check.target_size),
                "total_hold_to_resolution_ev": str(total_hold_to_resolution_ev),
                "filter_reasons": check.filter_reasons,
            },
        )


@dataclass(frozen=True)
class HoldToResolutionEvPolyMidStrategy(HoldToResolutionEvStrategy):
    strategy_id: str = HOLD_TO_RESOLUTION_EV_POLY_MID_ID
    strategy_version: str = "1"
    fair_value_provider: FairValueProvider = PolymarketMidFairValueProvider()
    default_signal_type: StrategySignalType = "paper_open"


@dataclass(frozen=True)
class HoldToResolutionEvPolyBidConservativeStrategy(HoldToResolutionEvStrategy):
    strategy_id: str = HOLD_TO_RESOLUTION_EV_POLY_BID_CONSERVATIVE_ID
    strategy_version: str = "1"
    fair_value_provider: FairValueProvider = PolymarketBidConservativeFairValueProvider()
    default_signal_type: StrategySignalType = "shadow"


def default_strategy_variants() -> tuple[
    LegacyFeeAdjustedEdgeStrategy
    | LoosePolymarketLeadScoutStrategy
    | PersistentMidGapStrategy
    | HoldToResolutionEvPolyMidStrategy
    | HoldToResolutionEvPolyBidConservativeStrategy,
    ...,
]:
    return (
        LegacyFeeAdjustedEdgeStrategy(),
        LoosePolymarketLeadScoutStrategy(),
        PersistentMidGapStrategy(),
        HoldToResolutionEvPolyMidStrategy(),
        HoldToResolutionEvPolyBidConservativeStrategy(),
    )
