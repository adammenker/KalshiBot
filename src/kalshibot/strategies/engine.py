from __future__ import annotations

from kalshibot.strategies.base import StrategyDecision
from kalshibot.strategies.config import StrategyEngineConfig
from kalshibot.strategies.context import StrategyContext
from kalshibot.strategies.registry import StrategyRegistry
from kalshibot.strategies.variants import default_strategy_variants


class StrategyEngine:
    """Pure strategy evaluator.

    The engine owns variant selection and evaluation only. Persistence,
    heartbeat context assembly, and paper trade opening belong to the runner
    and paper-trading service layers.
    """

    def __init__(
        self,
        registry: StrategyRegistry | None = None,
        config: StrategyEngineConfig | None = None,
    ) -> None:
        self.registry = registry or default_strategy_registry()
        self.config = config or StrategyEngineConfig()

    def enabled(self) -> bool:
        return bool(self.config.enabled_strategy_ids)

    def evaluate(self, context: StrategyContext) -> tuple[StrategyDecision, ...]:
        return tuple(
            variant.evaluate(context)
            for variant in self.registry.resolve_config(self.config)
        )

    def evaluate_safely(self, context: StrategyContext) -> tuple[StrategyDecision, ...]:
        decisions: list[StrategyDecision] = []
        for variant in self.registry.resolve_config(self.config):
            try:
                decision = variant.evaluate(context)
            except Exception as exc:
                decision = StrategyDecision(
                    strategy_id=variant.strategy_id,
                    strategy_version=variant.strategy_version,
                    signal_type="shadow",
                    side=context.check.outcome,
                    direction="error",
                    reasons=("strategy_error",),
                    rejection_reasons=(type(exc).__name__,),
                    metadata={"error": str(exc)},
                )
            if decision.signal_type != "none":
                decisions.append(decision)
        return tuple(decisions)


def default_strategy_registry() -> StrategyRegistry:
    return StrategyRegistry(default_strategy_variants())
