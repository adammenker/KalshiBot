from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from kalshibot.spreads import SpreadCheck


@dataclass(frozen=True)
class StrategyEngineConfig:
    """Configuration shared by strategy variants during one engine run."""

    enabled_strategy_ids: tuple[str, ...] = ()
    paper_trade_strategy_ids: tuple[str, ...] = ()
    strategy_mode: str = "off"
    strategy_parameters: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class StrategyContext:
    connection: sqlite3.Connection
    run_id: str
    observed_at: str
    observation_id: int
    check: SpreadCheck
    metrics: dict[str, str | None]
    history: Sequence[Mapping[str, Any]]
    config: StrategyEngineConfig
