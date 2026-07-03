from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from kalshibot.strategies.base import StrategySignalType
from kalshibot.strategies.context import StrategyContext
from kalshibot.strategies.fair_value import FairValueEstimate
from kalshibot.utils import optional_decimal


def strategy_parameters(context: StrategyContext, strategy_id: str) -> Mapping[str, Any]:
    return context.config.strategy_parameters.get(strategy_id, {})


def decimal_parameter(
    context: StrategyContext,
    strategy_id: str,
    name: str,
    default: Decimal,
) -> Decimal:
    value = strategy_parameters(context, strategy_id).get(name)
    return optional_decimal(value) if value is not None else default


def direction_for_side(side: str | None) -> str:
    return f"buy_{side or 'yes'}"


def signal_type_parameter(
    context: StrategyContext,
    strategy_id: str,
    name: str,
    default: StrategySignalType,
) -> StrategySignalType:
    value = str(strategy_parameters(context, strategy_id).get(name) or default)
    if value in {"shadow", "paper_open", "mark_only", "paper_close"}:
        return value  # type: ignore[return-value]
    return default


def fair_value_metadata(
    context: StrategyContext,
    estimate: FairValueEstimate,
) -> dict[str, Any]:
    return {
        "fair_value_provider": estimate.provider_id,
        "fair_value_provider_confidence": str(estimate.confidence)
        if estimate.confidence is not None
        else None,
        "fair_value_reasons": estimate.reasons,
        "fair_value_metadata": estimate.metadata,
        "kalshi_buy_price": str(context.check.kalshi_buy_price),
        "polymarket_mid_price": str(context.check.polymarket_mid_price)
        if context.check.polymarket_mid_price is not None
        else None,
        "polymarket_sell_price": str(context.check.polymarket_sell_price)
        if context.check.polymarket_sell_price is not None
        else None,
    }
