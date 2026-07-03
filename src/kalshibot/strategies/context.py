from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from kalshibot.spreads import SpreadCheck
from kalshibot.strategies.config import StrategyEngineConfig


@dataclass(frozen=True)
class StrategyContext:
    run_id: str
    observed_at: str
    observation_id: int
    check: SpreadCheck
    metrics: dict[str, str | None]
    history: Sequence[Mapping[str, Any]]
    config: StrategyEngineConfig
