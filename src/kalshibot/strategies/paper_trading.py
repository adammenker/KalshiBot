from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from kalshibot.paper import PaperTradeLogEvent, create_open_paper_trade
from kalshibot.strategies.base import StrategyDecision


@dataclass(frozen=True)
class StrategyPaperTradeService:
    paper_trade_strategy_ids: frozenset[str]

    def should_open_paper_trade(self, decision: StrategyDecision) -> bool:
        return (
            decision.strategy_id in self.paper_trade_strategy_ids
            and decision.signal_type == "paper_open"
        )

    def open_for_decision(
        self,
        connection: sqlite3.Connection,
        *,
        decision: StrategyDecision,
        strategy_signal_id: int,
        observation_id: int,
        timed_check: Any,
    ) -> PaperTradeLogEvent | None:
        if not self.should_open_paper_trade(decision):
            return None
        return create_open_paper_trade(
            connection,
            signal_id=None,
            strategy_signal_id=strategy_signal_id,
            strategy_id=decision.strategy_id,
            strategy_version=decision.strategy_version,
            fair_value_provider=fair_value_provider_from_decision(decision),
            fair_value=decision.fair_value,
            entry_policy=f"{decision.signal_type}_signal",
            exit_policy="heartbeat_paper_exit_config",
            side=decision.side,
            direction=decision.direction,
            observation_id=observation_id,
            timed_check=timed_check,
            initial_observation_count=1,
        )


def fair_value_provider_from_decision(decision: StrategyDecision) -> str | None:
    provider = decision.metadata.get("fair_value_provider")
    return str(provider) if provider is not None else None
