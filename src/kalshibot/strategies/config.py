from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Literal

from kalshibot.strategies.ids import (
    SCOUT_STRATEGY_IDS,
    STRICT_STRATEGY_IDS,
    parse_enabled_strategy_ids,
)

StrategyMode = Literal["off", "scout", "strict"]
STRATEGY_MODES: tuple[StrategyMode, ...] = ("off", "scout", "strict")


@dataclass(frozen=True)
class StrategyEngineConfig:
    """Configuration shared by strategy variants during one engine run."""

    enabled_strategy_ids: tuple[str, ...] = ()
    paper_trade_strategy_ids: tuple[str, ...] = ()
    strategy_mode: StrategyMode = "off"
    strategy_parameters: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)


def strategy_engine_config_from_cli(
    strategy_variants: str,
    strategy_paper_trades: str,
    *,
    strategy_mode: str | None = None,
    strategy_config_path: Path | None = None,
) -> StrategyEngineConfig:
    file_mode, file_enabled_ids, file_paper_trade_ids, strategy_parameters = (
        load_strategy_config(strategy_config_path)
    )
    resolved_mode = parse_strategy_mode(strategy_mode or file_mode or "off")
    mode_enabled_ids = strategy_ids_for_mode(resolved_mode)
    enabled_ids = parse_enabled_strategy_ids(
        (*mode_enabled_ids, *file_enabled_ids, *parse_enabled_strategy_ids(strategy_variants))
    )
    paper_trade_ids = parse_enabled_strategy_ids(
        (*file_paper_trade_ids, *parse_enabled_strategy_ids(strategy_paper_trades))
    )
    return StrategyEngineConfig(
        enabled_strategy_ids=tuple(dict.fromkeys((*enabled_ids, *paper_trade_ids))),
        paper_trade_strategy_ids=paper_trade_ids,
        strategy_mode=resolved_mode,
        strategy_parameters=strategy_parameters,
    )


def parse_strategy_mode(value: str) -> StrategyMode:
    if value in STRATEGY_MODES:
        return value
    raise ValueError(f"Unknown strategy mode: {value}")


def strategy_ids_for_mode(strategy_mode: StrategyMode | str) -> tuple[str, ...]:
    parsed = parse_strategy_mode(str(strategy_mode))
    if parsed == "scout":
        return SCOUT_STRATEGY_IDS
    if parsed == "strict":
        return STRICT_STRATEGY_IDS
    return ()


def load_strategy_config(
    path: Path | None,
) -> tuple[StrategyMode | None, tuple[str, ...], tuple[str, ...], dict[str, dict[str, object]]]:
    if path is None:
        return None, (), (), {}
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError("Strategy config must be a JSON object")
    variants = payload.get("variants") or {}
    if not isinstance(variants, dict):
        raise ValueError("Strategy config 'variants' must be an object")
    enabled_ids: list[str] = []
    paper_trade_ids: list[str] = []
    parameters: dict[str, dict[str, object]] = {}
    for strategy_id, raw_config in variants.items():
        if not isinstance(raw_config, dict):
            raise ValueError(f"Strategy config for {strategy_id} must be an object")
        if raw_config.get("enabled") is True:
            enabled_ids.append(str(strategy_id))
        if raw_config.get("paper_trade") is True:
            paper_trade_ids.append(str(strategy_id))
        params = {
            str(key): value
            for key, value in raw_config.items()
            if key not in {"enabled", "paper_trade"}
        }
        if params:
            parameters[str(strategy_id)] = params
    raw_mode = payload.get("strategy_mode")
    mode = parse_strategy_mode(str(raw_mode)) if raw_mode is not None else None
    return (
        mode,
        parse_enabled_strategy_ids(enabled_ids),
        parse_enabled_strategy_ids(paper_trade_ids),
        parameters,
    )
