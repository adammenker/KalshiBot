from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal, Protocol

from kalshibot.strategies.context import StrategyContext

StrategySignalType = Literal["none", "shadow", "paper_open", "paper_close", "mark_only"]


@dataclass(frozen=True)
class StrategyDecision:
    strategy_id: str
    strategy_version: str
    signal_type: StrategySignalType
    side: str | None = None
    direction: str | None = None
    confidence: Decimal | None = None
    score: Decimal | None = None
    fair_value: Decimal | None = None
    entry_price: Decimal | None = None
    mark_price: Decimal | None = None
    edge: Decimal | None = None
    fee_adjusted_edge: Decimal | None = None
    reasons: tuple[str, ...] = ()
    rejection_reasons: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def none(
        cls,
        *,
        strategy_id: str,
        strategy_version: str,
        rejection_reasons: tuple[str, ...] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> StrategyDecision:
        return cls(
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            signal_type="none",
            rejection_reasons=rejection_reasons,
            metadata=metadata or {},
        )


class StrategyVariant(Protocol):
    strategy_id: str
    strategy_version: str

    def evaluate(self, context: StrategyContext) -> StrategyDecision:
        """Evaluate one heartbeat observation without mutating runtime state."""

