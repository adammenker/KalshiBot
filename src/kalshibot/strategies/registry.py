from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from kalshibot.strategies.base import StrategyVariant
from kalshibot.strategies.ids import parse_enabled_strategy_ids


class UnknownStrategyError(ValueError):
    pass


class DuplicateStrategyError(ValueError):
    pass


class StrategyRegistry:
    def __init__(self, variants: Iterable[StrategyVariant] = ()) -> None:
        self._variants: dict[str, StrategyVariant] = {}
        for variant in variants:
            self.register(variant)

    def register(self, variant: StrategyVariant, *, replace: bool = False) -> None:
        strategy_id = variant.strategy_id.strip()
        if not strategy_id:
            raise ValueError("strategy_id cannot be empty")
        if strategy_id in self._variants and not replace:
            raise DuplicateStrategyError(f"Strategy already registered: {strategy_id}")
        self._variants[strategy_id] = variant

    def get(self, strategy_id: str) -> StrategyVariant:
        try:
            return self._variants[strategy_id]
        except KeyError as exc:
            raise UnknownStrategyError(f"Unknown strategy_id: {strategy_id}") from exc

    def resolve_enabled(self, strategy_ids: str | Iterable[str] | None) -> tuple[StrategyVariant, ...]:
        return tuple(self.get(strategy_id) for strategy_id in parse_enabled_strategy_ids(strategy_ids))

    def resolve_config(self, config: Any) -> tuple[StrategyVariant, ...]:
        return self.resolve_enabled(config.enabled_strategy_ids)

    @property
    def strategy_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._variants))
