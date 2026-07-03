from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kalshibot.paper import PaperTradeLogEvent
from kalshibot.storage import connect_database
from kalshibot.strategies.base import StrategyDecision
from kalshibot.strategies.config import StrategyEngineConfig
from kalshibot.strategies.context import StrategyContext
from kalshibot.strategies.engine import StrategyEngine
from kalshibot.strategies.paper_trading import StrategyPaperTradeService
from kalshibot.strategies.storage import insert_strategy_signal


@dataclass(frozen=True)
class StrategyEvaluationResult:
    decision: StrategyDecision
    strategy_signal_id: int | None


@dataclass(frozen=True)
class StrategyRecordingResult:
    strategy_signal_ids: tuple[int, ...]
    paper_trade_events: tuple[PaperTradeLogEvent, ...]


@dataclass(frozen=True)
class StrategyRunner:
    engine: StrategyEngine
    paper_trader: StrategyPaperTradeService
    history_limit: int = 3

    @classmethod
    def from_config(
        cls,
        config: StrategyEngineConfig,
        *,
        history_limit: int = 3,
    ) -> StrategyRunner:
        return cls(
            engine=StrategyEngine(config=config),
            paper_trader=StrategyPaperTradeService(frozenset(config.paper_trade_strategy_ids)),
            history_limit=history_limit,
        )

    def record_saved_observations(
        self,
        connection: sqlite3.Connection,
        timed_checks: Sequence[Any],
        save_results: Sequence[Any],
    ) -> StrategyRecordingResult:
        if not self.engine.enabled():
            return StrategyRecordingResult(strategy_signal_ids=(), paper_trade_events=())

        signal_ids: list[int] = []
        paper_trade_events: list[PaperTradeLogEvent] = []
        for timed_check, save_result in zip(timed_checks, save_results, strict=True):
            observation_signal_count = 0
            strategy_paper_trade_count = 0
            context = strategy_context_from_saved_observation(
                connection,
                timed_check,
                save_result,
                config=self.engine.config,
                history_limit=self.history_limit,
            )
            for result in self.record_decisions(connection, context):
                if result.strategy_signal_id is None:
                    continue
                signal_ids.append(result.strategy_signal_id)
                observation_signal_count += 1
                event = self.paper_trader.open_for_decision(
                    connection,
                    decision=result.decision,
                    strategy_signal_id=result.strategy_signal_id,
                    observation_id=save_result.observation_id,
                    timed_check=timed_check,
                )
                if event is not None:
                    paper_trade_events.append(event)
                    strategy_paper_trade_count += 1
            save_result.signal_fields["strategy_signal_count"] = observation_signal_count
            save_result.signal_fields["strategy_paper_trade_count"] = strategy_paper_trade_count
        return StrategyRecordingResult(
            strategy_signal_ids=tuple(signal_ids),
            paper_trade_events=tuple(paper_trade_events),
        )

    def record_decisions(
        self,
        connection: sqlite3.Connection,
        context: StrategyContext,
    ) -> tuple[StrategyEvaluationResult, ...]:
        results = []
        for decision in self.engine.evaluate_safely(context):
            strategy_signal_id = insert_strategy_signal(connection, context, decision)
            results.append(
                StrategyEvaluationResult(
                    decision=decision,
                    strategy_signal_id=strategy_signal_id,
                )
            )
        return tuple(results)


def record_strategy_signals_for_saved_observations(
    db_path: Path,
    timed_checks: Sequence[Any],
    save_results: Sequence[Any],
    *,
    config: StrategyEngineConfig | None,
    history_limit: int = 3,
) -> StrategyRecordingResult:
    if config is None or not config.enabled_strategy_ids:
        return StrategyRecordingResult(strategy_signal_ids=(), paper_trade_events=())
    with connect_database(db_path) as connection:
        return record_strategy_signals_on_connection(
            connection,
            timed_checks,
            save_results,
            config=config,
            history_limit=history_limit,
        )


def record_strategy_signals_on_connection(
    connection: sqlite3.Connection,
    timed_checks: Sequence[Any],
    save_results: Sequence[Any],
    *,
    config: StrategyEngineConfig | None,
    history_limit: int = 3,
) -> StrategyRecordingResult:
    if config is None or not config.enabled_strategy_ids:
        return StrategyRecordingResult(strategy_signal_ids=(), paper_trade_events=())
    return StrategyRunner.from_config(config, history_limit=history_limit).record_saved_observations(
        connection,
        timed_checks,
        save_results,
    )


def strategy_context_from_saved_observation(
    connection: sqlite3.Connection,
    timed_check: Any,
    save_result: Any,
    *,
    config: StrategyEngineConfig,
    history_limit: int,
) -> StrategyContext:
    return StrategyContext(
        run_id=timed_check.run_id,
        observed_at=timed_check.observed_at,
        observation_id=save_result.observation_id,
        check=timed_check.check,
        metrics=strategy_metrics_from_save_result(save_result),
        history=recent_observation_history(
            connection,
            timed_check,
            observation_id=save_result.observation_id,
            limit=history_limit,
        ),
        config=config,
    )


def strategy_metrics_from_save_result(save_result: Any) -> dict[str, str | None]:
    return {
        "polymarket_mid_delta": optional_metric(save_result.signal_fields, "polymarket_mid_delta"),
        "kalshi_mid_delta": optional_metric(save_result.signal_fields, "kalshi_mid_delta"),
        "polymarket_open_interest_delta": optional_metric(
            save_result.signal_fields,
            "polymarket_open_interest_delta",
        ),
        "polymarket_volume_delta": optional_metric(
            save_result.signal_fields,
            "polymarket_volume_delta",
        ),
    }


def optional_metric(fields: Mapping[str, Any], key: str) -> str | None:
    value = fields.get(key)
    return str(value) if value is not None else None


def recent_observation_history(
    connection: sqlite3.Connection,
    timed_check: Any,
    *,
    observation_id: int,
    limit: int,
) -> tuple[Mapping[str, Any], ...]:
    if limit <= 0:
        return ()
    rows = connection.execute(
        """
        SELECT
            id, observed_at, kalshi_mid_price, kalshi_mid_delta,
            polymarket_mid_price, polymarket_mid_delta,
            polymarket_mid_minus_kalshi_mid,
            polymarket_open_interest, polymarket_open_interest_delta,
            polymarket_volume, polymarket_volume_delta
        FROM observations
        WHERE kalshi_ticker = ?
            AND polymarket_token_id = ?
            AND outcome = ?
            AND id < ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (
            timed_check.check.kalshi_ticker,
            timed_check.check.polymarket_token_id,
            timed_check.check.outcome,
            observation_id,
            limit,
        ),
    ).fetchall()
    return tuple(dict(row) for row in rows)
