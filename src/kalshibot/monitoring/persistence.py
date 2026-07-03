from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
import sqlite3

from kalshibot.monitoring.models import TimedSpreadCheck
from kalshibot.monitoring.observations import (
    ObservationSaveResult,
    save_observations_on_connection,
)
from kalshibot.paper import (
    PaperExitConfig,
    append_paper_trade_events,
    write_paper_pnl_snapshot,
)
from kalshibot.storage import connect_database, initialize_database
from kalshibot.strategies import StrategyEngineConfig
from kalshibot.strategies.runner import (
    StrategyRecordingResult,
    record_strategy_signals_on_connection,
)


@dataclass(frozen=True)
class HeartbeatPersistenceResult:
    save_results: list[ObservationSaveResult]
    strategy_recording: StrategyRecordingResult


def persist_heartbeat_checks(
    db_path: Path,
    successful_checks: list[TimedSpreadCheck],
    *,
    db_connection: sqlite3.Connection | None,
    signal_lookback_minutes: int,
    min_mid_edge: Decimal,
    min_poly_mid_move: Decimal,
    min_poly_oi_delta: Decimal,
    min_poly_volume_delta: Decimal,
    max_kalshi_mid_move: Decimal,
    paper_exit_config: PaperExitConfig,
    paper_trade_log_path: Path | None,
    paper_pnl_log_path: Path | None,
    strategy_config: StrategyEngineConfig | None,
) -> HeartbeatPersistenceResult:
    if db_connection is None:
        initialize_database(db_path)
        with connect_database(db_path) as connection:
            return persist_heartbeat_checks_on_connection(
                connection,
                db_path,
                successful_checks,
                signal_lookback_minutes=signal_lookback_minutes,
                min_mid_edge=min_mid_edge,
                min_poly_mid_move=min_poly_mid_move,
                min_poly_oi_delta=min_poly_oi_delta,
                min_poly_volume_delta=min_poly_volume_delta,
                max_kalshi_mid_move=max_kalshi_mid_move,
                paper_exit_config=paper_exit_config,
                paper_trade_log_path=paper_trade_log_path,
                paper_pnl_log_path=paper_pnl_log_path,
                strategy_config=strategy_config,
            )
    return persist_heartbeat_checks_on_connection(
        db_connection,
        db_path,
        successful_checks,
        signal_lookback_minutes=signal_lookback_minutes,
        min_mid_edge=min_mid_edge,
        min_poly_mid_move=min_poly_mid_move,
        min_poly_oi_delta=min_poly_oi_delta,
        min_poly_volume_delta=min_poly_volume_delta,
        max_kalshi_mid_move=max_kalshi_mid_move,
        paper_exit_config=paper_exit_config,
        paper_trade_log_path=paper_trade_log_path,
        paper_pnl_log_path=paper_pnl_log_path,
        strategy_config=strategy_config,
    )


def persist_heartbeat_checks_on_connection(
    connection: sqlite3.Connection,
    db_path: Path,
    successful_checks: list[TimedSpreadCheck],
    *,
    signal_lookback_minutes: int,
    min_mid_edge: Decimal,
    min_poly_mid_move: Decimal,
    min_poly_oi_delta: Decimal,
    min_poly_volume_delta: Decimal,
    max_kalshi_mid_move: Decimal,
    paper_exit_config: PaperExitConfig,
    paper_trade_log_path: Path | None,
    paper_pnl_log_path: Path | None,
    strategy_config: StrategyEngineConfig | None,
) -> HeartbeatPersistenceResult:
    try:
        save_results, legacy_trade_events = save_observations_on_connection(
            connection,
            successful_checks,
            signal_lookback_minutes=signal_lookback_minutes,
            min_mid_edge=min_mid_edge,
            min_poly_mid_move=min_poly_mid_move,
            min_poly_oi_delta=min_poly_oi_delta,
            min_poly_volume_delta=min_poly_volume_delta,
            max_kalshi_mid_move=max_kalshi_mid_move,
            paper_exit_config=paper_exit_config,
        )
        strategy_recording = record_strategy_signals_on_connection(
            connection,
            successful_checks,
            save_results,
            config=strategy_config,
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise

    trade_events = [*legacy_trade_events, *strategy_recording.paper_trade_events]
    if trade_events and paper_trade_log_path is not None:
        append_paper_trade_events(paper_trade_log_path, trade_events)
        if paper_pnl_log_path is not None:
            write_paper_pnl_snapshot(paper_pnl_log_path, db_path)
    return HeartbeatPersistenceResult(
        save_results=save_results,
        strategy_recording=strategy_recording,
    )
