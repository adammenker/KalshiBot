from kalshibot.strategies.base import StrategyDecision, StrategySignalType, StrategyVariant
from kalshibot.strategies.config import (
    STRATEGY_MODES,
    StrategyEngineConfig,
    StrategyMode,
    load_strategy_config,
    parse_strategy_mode,
    strategy_engine_config_from_cli,
    strategy_ids_for_mode,
)
from kalshibot.strategies.context import StrategyContext
from kalshibot.strategies.engine import (
    StrategyEngine,
    default_strategy_registry,
)
from kalshibot.strategies.ids import (
    BUILT_IN_STRATEGY_IDS,
    HOLD_TO_RESOLUTION_EV_POLY_BID_CONSERVATIVE_ID,
    HOLD_TO_RESOLUTION_EV_POLY_MID_ID,
    LEGACY_FEE_ADJUSTED_EDGE_ID,
    LOOSE_POLY_LEAD_SCOUT_ID,
    PERSISTENT_MID_GAP_ID,
    SCOUT_STRATEGY_IDS,
    STRICT_STRATEGY_IDS,
    parse_enabled_strategy_ids,
)
from kalshibot.strategies.registry import (
    DuplicateStrategyError,
    StrategyRegistry,
    UnknownStrategyError,
)
from kalshibot.strategies.storage import insert_strategy_signal, list_strategy_signals
from kalshibot.strategies.variants import (
    HoldToResolutionEvPolyBidConservativeStrategy,
    HoldToResolutionEvPolyMidStrategy,
    LegacyFeeAdjustedEdgeStrategy,
    LoosePolymarketLeadScoutStrategy,
    PersistentMidGapStrategy,
    default_strategy_variants,
)

__all__ = [
    "BUILT_IN_STRATEGY_IDS",
    "DuplicateStrategyError",
    "HOLD_TO_RESOLUTION_EV_POLY_BID_CONSERVATIVE_ID",
    "HOLD_TO_RESOLUTION_EV_POLY_MID_ID",
    "HoldToResolutionEvPolyBidConservativeStrategy",
    "HoldToResolutionEvPolyMidStrategy",
    "LEGACY_FEE_ADJUSTED_EDGE_ID",
    "LOOSE_POLY_LEAD_SCOUT_ID",
    "LegacyFeeAdjustedEdgeStrategy",
    "LoosePolymarketLeadScoutStrategy",
    "PERSISTENT_MID_GAP_ID",
    "PersistentMidGapStrategy",
    "SCOUT_STRATEGY_IDS",
    "STRATEGY_MODES",
    "STRICT_STRATEGY_IDS",
    "StrategyContext",
    "StrategyDecision",
    "StrategyEngineConfig",
    "StrategyEngine",
    "StrategyEvaluationResult",
    "StrategyMode",
    "StrategyRecordingResult",
    "StrategyRegistry",
    "StrategyRunner",
    "StrategySignalType",
    "StrategyVariant",
    "UnknownStrategyError",
    "default_strategy_registry",
    "default_strategy_variants",
    "insert_strategy_signal",
    "list_strategy_signals",
    "load_strategy_config",
    "parse_strategy_mode",
    "parse_enabled_strategy_ids",
    "record_strategy_signals_for_saved_observations",
    "record_strategy_signals_on_connection",
    "strategy_engine_config_from_cli",
    "strategy_ids_for_mode",
]


def __getattr__(name: str):
    if name in {
        "StrategyEvaluationResult",
        "StrategyRecordingResult",
        "StrategyRunner",
        "record_strategy_signals_for_saved_observations",
        "record_strategy_signals_on_connection",
    }:
        from importlib import import_module

        runner = import_module("kalshibot.strategies.runner")
        return getattr(runner, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
