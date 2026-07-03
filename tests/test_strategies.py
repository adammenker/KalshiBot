from __future__ import annotations

import inspect
import json
import sqlite3
from dataclasses import dataclass, field, fields as dataclass_fields
from decimal import Decimal

import pytest

from kalshibot.spreads import SpreadCheck
from kalshibot.storage import initialize_database
import kalshibot.strategies.engine as strategy_engine_module
from kalshibot.strategies import (
    DuplicateStrategyError,
    HoldToResolutionEvPolyBidConservativeStrategy,
    HoldToResolutionEvPolyMidStrategy,
    LegacyFeeAdjustedEdgeStrategy,
    StrategyContext,
    StrategyDecision,
    StrategyEngine,
    StrategyEngineConfig,
    StrategyRegistry,
    UnknownStrategyError,
    insert_strategy_signal,
    list_strategy_signals,
    parse_enabled_strategy_ids,
)
from kalshibot.strategies.paper_trading import StrategyPaperTradeService
from kalshibot.strategies.runner import StrategyRunner, record_strategy_signals_on_connection


@dataclass(frozen=True)
class FakeStrategy:
    strategy_id: str
    strategy_version: str = "1"

    def evaluate(self, context: StrategyContext) -> StrategyDecision:
        return StrategyDecision(
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            signal_type="shadow",
            side=context.check.outcome,
            direction=f"buy_{context.check.outcome}",
            reasons=("fake_signal",),
        )


@dataclass(frozen=True)
class FailingStrategy:
    strategy_id: str
    strategy_version: str = "1"

    def evaluate(self, context: StrategyContext) -> StrategyDecision:
        raise RuntimeError("strategy failed")


def test_strategy_model_creation() -> None:
    context = make_strategy_context()
    decision = StrategyDecision(
        strategy_id="poly_lead_scout",
        strategy_version="1",
        signal_type="shadow",
        side="yes",
        direction="buy_yes",
        confidence=Decimal("0.70"),
        score=Decimal("1.25"),
        fair_value=Decimal("0.55"),
        entry_price=Decimal("0.47"),
        edge=Decimal("0.08"),
        fee_adjusted_edge=Decimal("0.06"),
        reasons=("poly_mid_up",),
        rejection_reasons=("kalshi_depth_thin",),
        metadata={"lookback_minutes": 10},
    )

    no_signal = StrategyDecision.none(
        strategy_id="poly_lead_scout",
        strategy_version="1",
        rejection_reasons=("edge_missing",),
    )

    assert context.config.enabled_strategy_ids == ("poly_lead_scout",)
    assert context.metrics["polymarket_mid_delta"] == "0.03"
    assert decision.signal_type == "shadow"
    assert decision.reasons == ("poly_mid_up",)
    assert no_signal.signal_type == "none"
    assert no_signal.rejection_reasons == ("edge_missing",)


def test_strategy_context_has_no_database_connection_field() -> None:
    assert "connection" not in {field.name for field in dataclass_fields(StrategyContext)}


def test_strategy_registry_resolves_enabled_ids_and_unknowns() -> None:
    alpha = FakeStrategy("alpha")
    beta = FakeStrategy("beta")
    registry = StrategyRegistry([alpha])
    registry.register(beta)

    assert registry.strategy_ids == ("alpha", "beta")
    assert parse_enabled_strategy_ids("beta, alpha, beta") == ("beta", "alpha")
    assert registry.resolve_enabled("beta,alpha") == (beta, alpha)
    assert registry.resolve_config(StrategyEngineConfig(enabled_strategy_ids=("alpha",))) == (alpha,)
    assert registry.resolve_enabled(None) == ()

    with pytest.raises(UnknownStrategyError, match="Unknown strategy_id: missing"):
        registry.resolve_enabled(("missing",))
    with pytest.raises(DuplicateStrategyError, match="Strategy already registered: alpha"):
        registry.register(FakeStrategy("alpha"))


def test_hold_to_resolution_ev_poly_mid_emits_paper_open_signal() -> None:
    context = make_strategy_context()
    decision = HoldToResolutionEvPolyMidStrategy().evaluate(context)

    assert decision.signal_type == "paper_open"
    assert decision.strategy_id == "hold_to_resolution_ev_poly_mid"
    assert decision.fair_value == Decimal("0.51")
    assert decision.entry_price == Decimal("0.47")
    assert decision.edge == Decimal("0.04")
    assert decision.fee_adjusted_edge == Decimal("0.03")
    assert decision.score == Decimal("0.03")
    assert decision.metadata["fair_value_provider"] == "polymarket_mid"
    assert decision.metadata["total_hold_to_resolution_ev"] == "0.03"


def test_hold_to_resolution_ev_poly_bid_conservative_uses_polymarket_bid() -> None:
    context = make_strategy_context()
    decision = HoldToResolutionEvPolyBidConservativeStrategy().evaluate(context)

    assert decision.signal_type == "shadow"
    assert decision.strategy_id == "hold_to_resolution_ev_poly_bid_conservative"
    assert decision.fair_value == Decimal("0.54")
    assert decision.edge == Decimal("0.07")
    assert decision.fee_adjusted_edge == Decimal("0.06")
    assert decision.metadata["fair_value_provider"] == "polymarket_bid_conservative"


def test_hold_to_resolution_ev_respects_threshold() -> None:
    context = make_strategy_context(
        config=StrategyEngineConfig(
            enabled_strategy_ids=("hold_to_resolution_ev_poly_mid",),
            strategy_parameters={
                "hold_to_resolution_ev_poly_mid": {
                    "min_fee_adjusted_edge": "0.05",
                }
            },
        ),
    )
    decision = HoldToResolutionEvPolyMidStrategy().evaluate(context)

    assert decision.signal_type == "shadow"
    assert decision.rejection_reasons == ("hold_to_resolution_ev_below_threshold",)
    assert decision.reasons[0] == "positive_fair_value_edge"


def test_strategy_engine_has_no_persistence_imports() -> None:
    source = inspect.getsource(strategy_engine_module)

    assert "sqlite3" not in source
    assert "Path" not in source
    assert "connect_database" not in source
    assert "insert_strategy_signal" not in source
    assert "create_open_paper_trade" not in source
    assert "PaperTradeLogEvent" not in source
    assert "paper_storage" not in source
    assert "StrategyEvaluationResult" not in source
    assert "StrategyRecordingResult" not in source
    assert "record_strategy_signals" not in source
    assert "strategy_context_from_saved_observation" not in source
    assert "recent_observation_history" not in source


def test_strategy_engine_evaluate_safely_returns_decisions_without_db_side_effects(
    tmp_path,
) -> None:
    db_path = tmp_path / "observations.sqlite"
    initialize_database(db_path)
    engine = StrategyEngine(
        registry=StrategyRegistry([LegacyFeeAdjustedEdgeStrategy()]),
        config=StrategyEngineConfig(enabled_strategy_ids=("legacy_fee_adjusted_edge",)),
    )

    decisions = engine.evaluate_safely(make_strategy_context())

    with sqlite3.connect(db_path) as connection:
        signal_count = connection.execute("SELECT COUNT(*) FROM strategy_signals").fetchone()[0]
        trade_count = connection.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0]

    assert [decision.strategy_id for decision in decisions] == ["legacy_fee_adjusted_edge"]
    assert signal_count == 0
    assert trade_count == 0


def test_strategy_engine_evaluate_safely_wraps_variant_errors() -> None:
    engine = StrategyEngine(
        registry=StrategyRegistry([FailingStrategy("broken")]),
        config=StrategyEngineConfig(enabled_strategy_ids=("broken",)),
    )

    decisions = engine.evaluate_safely(make_strategy_context())

    assert len(decisions) == 1
    assert decisions[0].strategy_id == "broken"
    assert decisions[0].signal_type == "shadow"
    assert decisions[0].direction == "error"
    assert decisions[0].rejection_reasons == ("RuntimeError",)
    assert decisions[0].metadata == {"error": "strategy failed"}


def test_strategy_runner_inserts_strategy_signals(tmp_path) -> None:
    db_path = tmp_path / "observations.sqlite"
    initialize_database(db_path)
    runner = StrategyRunner(
        engine=StrategyEngine(
            registry=StrategyRegistry([LegacyFeeAdjustedEdgeStrategy()]),
            config=StrategyEngineConfig(enabled_strategy_ids=("legacy_fee_adjusted_edge",)),
        ),
        paper_trader=StrategyPaperTradeService(frozenset()),
    )

    with sqlite3.connect(db_path) as connection:
        results = runner.record_decisions(connection, make_strategy_context())
        connection.commit()
        signal_count = connection.execute("SELECT COUNT(*) FROM strategy_signals").fetchone()[0]
        trade_count = connection.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0]

    assert len(results) == 1
    assert results[0].decision.strategy_id == "legacy_fee_adjusted_edge"
    assert results[0].strategy_signal_id == 1
    assert signal_count == 1
    assert trade_count == 0


def test_strategy_paper_trade_service_respects_allowlist(tmp_path) -> None:
    db_path = tmp_path / "observations.sqlite"
    initialize_database(db_path)
    timed_check = FakeTimedCheck(check=make_spread_check())
    decision = StrategyDecision(
        strategy_id="legacy_fee_adjusted_edge",
        strategy_version="1",
        signal_type="paper_open",
        side="yes",
        direction="buy_yes",
        fair_value=Decimal("0.50"),
        metadata={"fair_value_provider": "polymarket_mid"},
    )

    with sqlite3.connect(db_path) as connection:
        blocked_event = StrategyPaperTradeService(frozenset()).open_for_decision(
            connection,
            decision=decision,
            strategy_signal_id=1,
            observation_id=1,
            timed_check=timed_check,
        )
        allowed_event = StrategyPaperTradeService(
            frozenset({"legacy_fee_adjusted_edge"})
        ).open_for_decision(
            connection,
            decision=decision,
            strategy_signal_id=1,
            observation_id=1,
            timed_check=timed_check,
        )
        connection.commit()
        trade_count = connection.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0]

    assert blocked_event is None
    assert allowed_event is not None
    assert allowed_event.strategy_id == "legacy_fee_adjusted_edge"
    assert trade_count == 1


def test_strategy_runner_records_signals_without_opening_unallowlisted_trades(tmp_path) -> None:
    db_path = tmp_path / "observations.sqlite"
    initialize_database(db_path)
    timed_check = FakeTimedCheck(check=make_spread_check())
    save_result = FakeSaveResult()

    with sqlite3.connect(db_path) as connection:
        recording = record_strategy_signals_on_connection(
            connection,
            [timed_check],
            [save_result],
            config=StrategyEngineConfig(enabled_strategy_ids=("legacy_fee_adjusted_edge",)),
        )
        connection.commit()
        signal_count = connection.execute("SELECT COUNT(*) FROM strategy_signals").fetchone()[0]
        trade_count = connection.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0]

    assert recording.strategy_signal_ids == (1,)
    assert recording.paper_trade_events == ()
    assert save_result.signal_fields["strategy_signal_count"] == 1
    assert save_result.signal_fields["strategy_paper_trade_count"] == 0
    assert signal_count == 1
    assert trade_count == 0


def test_strategy_runner_opens_allowlisted_strategy_paper_trade(tmp_path) -> None:
    db_path = tmp_path / "observations.sqlite"
    initialize_database(db_path)
    timed_check = FakeTimedCheck(check=make_spread_check())
    save_result = FakeSaveResult()

    with sqlite3.connect(db_path) as connection:
        recording = record_strategy_signals_on_connection(
            connection,
            [timed_check],
            [save_result],
            config=StrategyEngineConfig(
                enabled_strategy_ids=("legacy_fee_adjusted_edge",),
                paper_trade_strategy_ids=("legacy_fee_adjusted_edge",),
            ),
        )
        connection.commit()
        trade = connection.execute(
            """
            SELECT strategy_signal_id, strategy_id, strategy_version, entry_policy,
                side, direction
            FROM paper_trades
            """
        ).fetchone()

    assert recording.strategy_signal_ids == (1,)
    assert len(recording.paper_trade_events) == 1
    assert save_result.signal_fields["strategy_signal_count"] == 1
    assert save_result.signal_fields["strategy_paper_trade_count"] == 1
    assert trade == (
        1,
        "legacy_fee_adjusted_edge",
        "1",
        "paper_open_signal",
        "yes",
        "buy_yes",
    )


def test_allowlisted_strategies_open_separate_paper_trades_for_same_market(tmp_path) -> None:
    db_path = tmp_path / "observations.sqlite"
    initialize_database(db_path)
    timed_check = FakeTimedCheck(check=make_spread_check())
    save_result = FakeSaveResult()

    with sqlite3.connect(db_path) as connection:
        recording = record_strategy_signals_on_connection(
            connection,
            [timed_check],
            [save_result],
            config=StrategyEngineConfig(
                enabled_strategy_ids=(
                    "legacy_fee_adjusted_edge",
                    "hold_to_resolution_ev_poly_mid",
                ),
                paper_trade_strategy_ids=(
                    "legacy_fee_adjusted_edge",
                    "hold_to_resolution_ev_poly_mid",
                ),
            ),
        )
        connection.commit()
        trade_rows = connection.execute(
            """
            SELECT strategy_signal_id, strategy_id, strategy_version, side, direction
            FROM paper_trades
            ORDER BY id
            """
        ).fetchall()

    assert recording.strategy_signal_ids == (1, 2)
    assert len(recording.paper_trade_events) == 2
    assert save_result.signal_fields["strategy_signal_count"] == 2
    assert save_result.signal_fields["strategy_paper_trade_count"] == 2
    assert trade_rows == [
        (1, "legacy_fee_adjusted_edge", "1", "yes", "buy_yes"),
        (2, "hold_to_resolution_ev_poly_mid", "1", "yes", "buy_yes"),
    ]


def test_same_strategy_does_not_duplicate_open_paper_trade_for_market_direction(
    tmp_path,
) -> None:
    db_path = tmp_path / "observations.sqlite"
    initialize_database(db_path)
    timed_check = FakeTimedCheck(check=make_spread_check())
    first_save_result = FakeSaveResult(observation_id=1)
    second_save_result = FakeSaveResult(observation_id=2)
    config = StrategyEngineConfig(
        enabled_strategy_ids=("legacy_fee_adjusted_edge",),
        paper_trade_strategy_ids=("legacy_fee_adjusted_edge",),
    )

    with sqlite3.connect(db_path) as connection:
        first_recording = record_strategy_signals_on_connection(
            connection,
            [timed_check],
            [first_save_result],
            config=config,
        )
        second_recording = record_strategy_signals_on_connection(
            connection,
            [timed_check],
            [second_save_result],
            config=config,
        )
        connection.commit()
        signal_count = connection.execute("SELECT COUNT(*) FROM strategy_signals").fetchone()[0]
        trade_rows = connection.execute(
            """
            SELECT strategy_signal_id, strategy_id, strategy_version, side, direction
            FROM paper_trades
            ORDER BY id
            """
        ).fetchall()

    assert first_recording.strategy_signal_ids == (1,)
    assert second_recording.strategy_signal_ids == (2,)
    assert len(first_recording.paper_trade_events) == 1
    assert second_recording.paper_trade_events == ()
    assert first_save_result.signal_fields["strategy_signal_count"] == 1
    assert first_save_result.signal_fields["strategy_paper_trade_count"] == 1
    assert second_save_result.signal_fields["strategy_signal_count"] == 1
    assert second_save_result.signal_fields["strategy_paper_trade_count"] == 0
    assert signal_count == 2
    assert trade_rows == [
        (1, "legacy_fee_adjusted_edge", "1", "yes", "buy_yes"),
    ]


def test_strategy_signals_table_creation_and_migration(tmp_path) -> None:
    db_path = tmp_path / "observations.sqlite"
    initialize_database(db_path)
    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        columns = {row[1] for row in connection.execute("PRAGMA table_info(strategy_signals)")}
        paper_trade_columns = {
            row[1]: row for row in connection.execute("PRAGMA table_info(paper_trades)")
        }

    assert {
        "observations",
        "paper_signals",
        "paper_trades",
        "paper_trade_marks",
        "strategy_signals",
    } <= tables
    assert {
        "observation_id",
        "strategy_id",
        "strategy_version",
        "signal_type",
        "fair_value",
        "entry_price",
        "mark_price",
        "edge",
        "fee_adjusted_edge",
        "reasons_json",
        "rejection_reasons_json",
        "metadata_json",
    } <= columns
    assert paper_trade_columns["signal_id"][3] == 0
    assert {
        "strategy_signal_id",
        "strategy_id",
        "strategy_version",
        "fair_value_provider",
        "entry_policy",
        "exit_policy",
        "side",
        "direction",
    } <= set(paper_trade_columns)


def test_paper_trades_migration_makes_signal_id_nullable(tmp_path) -> None:
    db_path = tmp_path / "observations.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE paper_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id INTEGER NOT NULL,
                observation_id INTEGER NOT NULL,
                run_id TEXT NOT NULL,
                opened_at TEXT NOT NULL,
                status TEXT NOT NULL,
                label TEXT NOT NULL,
                outcome TEXT NOT NULL,
                kalshi_ticker TEXT NOT NULL,
                polymarket_token_id TEXT NOT NULL,
                simulated_entry_venue TEXT NOT NULL,
                entry_price TEXT NOT NULL,
                entry_comparison_price TEXT NOT NULL,
                entry_edge TEXT NOT NULL,
                quantity TEXT NOT NULL,
                latest_observation_id INTEGER NOT NULL,
                latest_marked_at TEXT NOT NULL,
                latest_edge TEXT NOT NULL,
                observation_count INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO paper_trades (
                signal_id, observation_id, run_id, opened_at, status,
                label, outcome, kalshi_ticker, polymarket_token_id,
                simulated_entry_venue, entry_price, entry_comparison_price,
                entry_edge, quantity, latest_observation_id, latest_marked_at,
                latest_edge, observation_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                7,
                1,
                "run-1",
                "2026-07-02T12:00:00+00:00",
                "open",
                "Example",
                "yes",
                "KXEXAMPLE",
                "token-example",
                "kalshi",
                "0.47",
                "0.55",
                "0.08",
                "1",
                1,
                "2026-07-02T12:00:00+00:00",
                "0.08",
                1,
            ),
        )

    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        paper_trade_columns = {
            row[1]: row for row in connection.execute("PRAGMA table_info(paper_trades)")
        }
        migrated_row = connection.execute(
            "SELECT signal_id, label, strategy_id FROM paper_trades"
        ).fetchone()

    assert paper_trade_columns["signal_id"][3] == 0
    assert "strategy_id" in paper_trade_columns
    assert migrated_row == (7, "Example", None)


def test_insert_and_list_strategy_signal(tmp_path) -> None:
    db_path = tmp_path / "observations.sqlite"
    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        context = make_strategy_context()
        decision = StrategyDecision(
            strategy_id="poly_lead_scout",
            strategy_version="1",
            signal_type="shadow",
            side="yes",
            direction="buy_yes",
            confidence=Decimal("0.75"),
            score=Decimal("1.20"),
            fair_value=Decimal("0.56"),
            entry_price=Decimal("0.47"),
            mark_price=Decimal("0.50"),
            edge=Decimal("0.09"),
            fee_adjusted_edge=Decimal("0.07"),
            reasons=("poly_mid_up", "kalshi_lagged"),
            rejection_reasons=("depth_below_trade_threshold",),
            metadata={"lookback_minutes": 10, "threshold": Decimal("0.03")},
        )

        signal_id = insert_strategy_signal(
            connection,
            context,
            decision,
            created_at="2026-07-02T12:00:01+00:00",
        )
        skipped_id = insert_strategy_signal(
            connection,
            context,
            StrategyDecision.none(strategy_id="poly_lead_scout", strategy_version="1"),
        )
        rows = list_strategy_signals(connection, strategy_id="poly_lead_scout")
        raw_json = connection.execute(
            """
            SELECT reasons_json, rejection_reasons_json, metadata_json
            FROM strategy_signals
            WHERE id = ?
            """,
            (signal_id,),
        ).fetchone()

    assert signal_id == 1
    assert skipped_id is None
    assert len(rows) == 1
    assert rows[0]["strategy_id"] == "poly_lead_scout"
    assert rows[0]["signal_type"] == "shadow"
    assert rows[0]["label"] == "Example"
    assert rows[0]["kalshi_ticker"] == "KXEXAMPLE"
    assert rows[0]["polymarket_token_id"] == "token-example"
    assert rows[0]["fair_value"] == "0.56"
    assert rows[0]["entry_price"] == "0.47"
    assert rows[0]["edge"] == "0.09"
    assert rows[0]["fee_adjusted_edge"] == "0.07"
    assert rows[0]["polymarket_mid_delta"] == "0.03"
    assert rows[0]["kalshi_mid_delta"] == "0.00"
    assert rows[0]["polymarket_open_interest"] == "1000"
    assert rows[0]["polymarket_open_interest_delta"] == "25"
    assert rows[0]["polymarket_volume"] == "5000"
    assert rows[0]["polymarket_volume_delta"] == "100"
    assert rows[0]["reasons"] == ("poly_mid_up", "kalshi_lagged")
    assert rows[0]["rejection_reasons"] == ("depth_below_trade_threshold",)
    assert rows[0]["metadata"] == {"lookback_minutes": 10, "threshold": "0.03"}
    assert json.loads(raw_json[0]) == ["poly_mid_up", "kalshi_lagged"]
    assert json.loads(raw_json[1]) == ["depth_below_trade_threshold"]
    assert json.loads(raw_json[2]) == {"lookback_minutes": 10, "threshold": "0.03"}


def make_strategy_context(*, config: StrategyEngineConfig | None = None) -> StrategyContext:
    return StrategyContext(
        run_id="run-1",
        observed_at="2026-07-02T12:00:00+00:00",
        observation_id=1,
        check=make_spread_check(),
        metrics={
            "polymarket_mid_delta": "0.03",
            "kalshi_mid_delta": "0.00",
            "polymarket_open_interest_delta": "25",
            "polymarket_volume_delta": "100",
        },
        history=({"polymarket_mid_price": "0.50"},),
        config=config or StrategyEngineConfig(enabled_strategy_ids=("poly_lead_scout",)),
    )


@dataclass
class FakeSaveResult:
    observation_id: int = 1
    signal_fields: dict[str, object] = field(
        default_factory=lambda: {
            "polymarket_mid_delta": "0.03",
            "kalshi_mid_delta": "0.00",
            "polymarket_open_interest_delta": "25",
            "polymarket_volume_delta": "100",
        }
    )


@dataclass(frozen=True)
class FakeTimedCheck:
    check: SpreadCheck
    run_id: str = "run-1"
    observed_at: str = "2026-07-02T12:00:00+00:00"


def make_spread_check() -> SpreadCheck:
    return SpreadCheck(
        label="Example",
        outcome="yes",
        kalshi_ticker="KXEXAMPLE",
        polymarket_token_id="token-example",
        polymarket_condition_id="0xexample",
        polymarket_open_interest=Decimal("1000"),
        polymarket_volume=Decimal("5000"),
        kalshi_mid_price=Decimal("0.45"),
        polymarket_mid_price=Decimal("0.51"),
        polymarket_mid_minus_kalshi_mid=Decimal("0.06"),
        kalshi_buy_price=Decimal("0.47"),
        kalshi_sell_price=Decimal("0.43"),
        kalshi_buy_size=Decimal("20"),
        kalshi_buy_depth=Decimal("100"),
        kalshi_spread=Decimal("0.04"),
        polymarket_buy_price=Decimal("0.56"),
        polymarket_sell_price=Decimal("0.54"),
        polymarket_buy_size=Decimal("50"),
        polymarket_buy_depth=Decimal("150"),
        polymarket_spread=Decimal("0.02"),
        depth_window=Decimal("0.03"),
        polymarket_minus_kalshi=Decimal("0.09"),
        kalshi_lower=True,
        passes_filters=True,
        filter_reasons=(),
        kalshi_url="https://kalshi.com/search?query=KXEXAMPLE",
        polymarket_url="https://polymarket.com/search?query=token-example",
        kalshi_entry_fee=Decimal("0.01"),
        kalshi_exit_fee=Decimal("0.01"),
        kalshi_round_trip_fee=Decimal("0.02"),
        fee_adjustment=Decimal("0.01"),
        fee_adjusted_edge=Decimal("0.08"),
    )
