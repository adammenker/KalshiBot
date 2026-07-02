from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import sqlite3
from pathlib import Path

from kalshibot.config import PolymarketConfig
from kalshibot.cli import (
    build_parser,
    format_heartbeat_drop,
    format_heartbeat_failure,
    heartbeat_pair_key,
)
from kalshibot.commands.trading import heartbeat_interval_seconds, heartbeat_strategy_config
from kalshibot.monitoring.heartbeat import (
    CachedPairMetadata,
    format_heartbeat_summary,
    metadata_refresh_due,
    process_batch_results,
)
from kalshibot.analysis import analyze_database
from kalshibot.monitor import (
    TimedSpreadCheck,
    build_spread_check_from_books,
    first_response_venue,
    save_observation,
    timestamp_delta_ms,
)
from kalshibot.monitoring.observations import save_observations
from kalshibot.paper import PaperExitConfig
from kalshibot.polymarket import PolymarketClient
from kalshibot.spreads import MarketPair
from kalshibot.strategies import StrategyEngineConfig


def test_timestamp_delta_ms_compares_iso_timestamps() -> None:
    assert (
        timestamp_delta_ms("2026-06-20T12:00:00+00:00", "2026-06-20T12:00:00.125000+00:00")
        == Decimal("125.000")
    )


def test_first_response_venue_compares_receive_timestamps() -> None:
    assert (
        first_response_venue(
            "2026-06-20T12:00:00.100000+00:00",
            "2026-06-20T12:00:00.150000+00:00",
        )
        == "kalshi"
    )
    assert (
        first_response_venue(
            "2026-06-20T12:00:00.250000+00:00",
            "2026-06-20T12:00:00.150000+00:00",
        )
        == "polymarket"
    )
    assert (
        first_response_venue(
            "2026-06-20T12:00:00.150000+00:00",
            "2026-06-20T12:00:00.150000+00:00",
        )
        == "tie"
    )


def test_format_heartbeat_failure_marks_pair_failed() -> None:
    failure = format_heartbeat_failure(
        MarketPair(
            label="Closed market",
            kalshi_ticker="KXCLOSED",
            polymarket_token_id="token-closed",
            polymarket_condition_id="0xclosed",
        ),
        "run-1",
        RuntimeError("market unavailable"),
    )

    assert failure["status"] == "failed"
    assert failure["run_id"] == "run-1"
    assert failure["label"] == "Closed market"
    assert failure["kalshi_ticker"] == "KXCLOSED"
    assert failure["polymarket_token_id"] == "token-closed"
    assert failure["consecutive_failures"] == 1
    assert failure["error_type"] == "RuntimeError"
    assert failure["error"] == "market unavailable"


def test_format_heartbeat_drop_marks_pair_removed() -> None:
    pair = MarketPair(
        label="Closed market",
        kalshi_ticker="KXCLOSED",
        polymarket_token_id="token-closed",
        polymarket_condition_id="0xclosed",
    )

    dropped = format_heartbeat_drop(pair, "run-1", consecutive_failures=3, threshold=3)

    assert heartbeat_pair_key(pair) == "KXCLOSED:token-closed:yes"
    assert dropped["status"] == "dropped"
    assert dropped["run_id"] == "run-1"
    assert dropped["label"] == "Closed market"
    assert dropped["consecutive_failures"] == 3
    assert dropped["drop_failed_pairs_after"] == 3


def test_heartbeat_interval_accepts_decimal_seconds_and_milliseconds() -> None:
    seconds_args = build_parser().parse_args(
        [
            "heartbeat",
            "--pairs",
            "config/approved_market_pairs.json",
            "--interval-seconds",
            "0.5",
        ]
    )
    milliseconds_args = build_parser().parse_args(
        [
            "heartbeat",
            "--pairs",
            "config/approved_market_pairs.json",
            "--interval-ms",
            "500",
        ]
    )

    assert seconds_args.interval_seconds == Decimal("0.5")
    assert heartbeat_interval_seconds(
        seconds_args.interval_seconds,
        seconds_args.interval_ms,
    ) == Decimal("0.5")
    assert heartbeat_interval_seconds(
        milliseconds_args.interval_seconds,
        milliseconds_args.interval_ms,
    ) == Decimal("0.5")


def test_heartbeat_parser_accepts_strategy_variants_and_paper_trades() -> None:
    args = build_parser().parse_args(
        [
            "heartbeat",
            "--pairs",
            "config/approved_market_pairs.json",
            "--strategy-variants",
            "legacy_fee_adjusted_edge,loose_poly_lead_scout",
            "--strategy-paper-trades",
            "legacy_fee_adjusted_edge",
        ]
    )
    config = heartbeat_strategy_config(args.strategy_variants, args.strategy_paper_trades)

    assert args.strategy_variants == "legacy_fee_adjusted_edge,loose_poly_lead_scout"
    assert args.strategy_paper_trades == "legacy_fee_adjusted_edge"
    assert config.enabled_strategy_ids == (
        "legacy_fee_adjusted_edge",
        "loose_poly_lead_scout",
    )
    assert config.paper_trade_strategy_ids == ("legacy_fee_adjusted_edge",)


def test_heartbeat_strategy_config_scout_mode_enables_builtins() -> None:
    args = build_parser().parse_args(
        [
            "heartbeat",
            "--strategy-mode",
            "scout",
            "--strategy-paper-trades",
            "hold_to_resolution_ev_poly_mid",
        ]
    )
    config = heartbeat_strategy_config(
        args.strategy_variants,
        args.strategy_paper_trades,
        strategy_mode=args.strategy_mode,
    )

    assert "legacy_fee_adjusted_edge" in config.enabled_strategy_ids
    assert "loose_poly_lead_scout" in config.enabled_strategy_ids
    assert "persistent_mid_gap" in config.enabled_strategy_ids
    assert "hold_to_resolution_ev_poly_mid" in config.enabled_strategy_ids
    assert "hold_to_resolution_ev_poly_bid_conservative" in config.enabled_strategy_ids
    assert config.paper_trade_strategy_ids == ("hold_to_resolution_ev_poly_mid",)
    assert config.strategy_mode == "scout"


def test_heartbeat_strategy_config_loads_json_config(tmp_path) -> None:
    config_path = tmp_path / "strategy_variants.json"
    config_path.write_text(
        json.dumps(
            {
                "strategy_mode": "off",
                "variants": {
                    "hold_to_resolution_ev_poly_mid": {
                        "enabled": True,
                        "paper_trade": True,
                        "min_fee_adjusted_edge": "0.02",
                    }
                },
            }
        )
    )

    config = heartbeat_strategy_config("", "", strategy_config_path=config_path)

    assert config.enabled_strategy_ids == ("hold_to_resolution_ev_poly_mid",)
    assert config.paper_trade_strategy_ids == ("hold_to_resolution_ev_poly_mid",)
    assert config.strategy_parameters == {
        "hold_to_resolution_ev_poly_mid": {"min_fee_adjusted_edge": "0.02"}
    }


def test_analyze_parser_accepts_strategy_signal_limit() -> None:
    args = build_parser().parse_args(["analyze", "--strategy-signal-limit", "3"])

    assert args.strategy_signal_limit == 3


def test_heartbeat_parser_accepts_entry_only_fee_mode() -> None:
    default_args = build_parser().parse_args(
        [
            "heartbeat",
        ]
    )
    args = build_parser().parse_args(
        [
            "heartbeat",
            "--pairs",
            "config/approved_market_pairs.json",
            "--fee-mode",
            "entry-only",
        ]
    )

    assert default_args.fee_mode == "entry-only"
    assert default_args.pairs == Path("config/approved_market_pairs.json")
    assert default_args.paper_exit_edge is None
    assert args.fee_mode == "entry-only"


def test_heartbeat_parser_accepts_performance_flags() -> None:
    args = build_parser().parse_args(
        [
            "heartbeat",
            "--pairs",
            "config/approved_market_pairs.json",
            "--heartbeat-output",
            "quiet",
            "--scheduler",
            "per-market",
            "--metadata-refresh-seconds",
            "10",
        ]
    )

    assert args.heartbeat_output == "quiet"
    assert args.scheduler == "per-market"
    assert args.metadata_refresh_seconds == Decimal("10")


def test_metadata_refresh_due_respects_refresh_interval() -> None:
    assert metadata_refresh_due(None, now=100, metadata_refresh_seconds=Decimal("5")) is True
    assert (
        metadata_refresh_due(
            CachedPairMetadata(refreshed_at=96),
            now=100,
            metadata_refresh_seconds=Decimal("5"),
        )
        is False
    )
    assert (
        metadata_refresh_due(
            CachedPairMetadata(refreshed_at=95),
            now=100,
            metadata_refresh_seconds=Decimal("5"),
        )
        is True
    )
    assert (
        metadata_refresh_due(
            CachedPairMetadata(refreshed_at=100),
            now=100,
            metadata_refresh_seconds=Decimal("0"),
        )
        is True
    )


def test_format_heartbeat_summary_compacts_batch_results() -> None:
    summary = format_heartbeat_summary(
        [
            {
                "passes_filters": True,
                "strategy_signal_count": 4,
                "strategy_paper_trade_count": 1,
                "polymarket_minus_kalshi": "0.03",
                "fee_adjusted_edge": "0.01",
                "kalshi_latency_ms": "100",
                "polymarket_latency_ms": "150",
                "response_skew_ms": "50",
            },
            {
                "status": "failed",
                "kalshi_latency_ms": "200",
                "error": "closed",
            },
            {
                "status": "dropped",
                "error": "closed",
            },
        ],
        run_id="run-1",
        observed_at="2026-06-20T12:00:00+00:00",
        scheduler="fixed-rate",
        output_mode="summary",
        active_pairs=2,
        interval_seconds=Decimal("0.25"),
        batch_started_at="2026-06-20T12:00:00+00:00",
        batch_duration_ms=Decimal("123.456"),
        metadata_refresh_count=1,
    )

    assert summary["result_count"] == 3
    assert summary["success_count"] == 1
    assert summary["failure_count"] == 1
    assert summary["dropped_count"] == 1
    assert summary["signal_count"] == 1
    assert summary["strategy_signal_count"] == 4
    assert summary["strategy_paper_trade_count"] == 1
    assert summary["max_raw_edge"] == "0.03"
    assert summary["avg_kalshi_latency_ms"] == "150.00"
    assert summary["avg_polymarket_latency_ms"] == "150.00"


def test_build_spread_check_from_books_uses_existing_filters() -> None:
    pair = MarketPair(
        label="Example",
        kalshi_ticker="KXTEST-26-YES",
        polymarket_token_id="123",
    )
    client = PolymarketClient(
        PolymarketConfig(
            gamma_base_url="https://example.com",
            clob_base_url="https://example.com",
            data_base_url="https://example.com",
        )
    )

    check = build_spread_check_from_books(
        pair,
        {
            "orderbook_fp": {
                "yes_dollars": [["0.4300", "25"]],
                "no_dollars": [["0.5400", "75"], ["0.5300", "10"]],
            }
        },
        {
            "bids": [{"price": "0.48", "size": "70"}],
            "asks": [{"price": "0.49", "size": "80"}, {"price": "0.51", "size": "20"}],
        },
        client,
        max_venue_spread=Decimal("0.05"),
        min_buy_size=Decimal("10"),
        min_depth_size=Decimal("50"),
        depth_window=Decimal("0.03"),
        min_edge=Decimal("0"),
        min_fee_adjusted_edge=Decimal("-1"),
    )

    assert check.kalshi_buy_price == Decimal("0.4600")
    assert check.polymarket_buy_price == Decimal("0.49")
    assert check.polymarket_minus_kalshi == Decimal("0.0300")
    assert check.passes_filters is True


def test_build_spread_check_from_books_accounts_for_kalshi_fees() -> None:
    pair = MarketPair(
        label="Example",
        kalshi_ticker="KXTEST-26-YES",
        polymarket_token_id="123",
    )
    client = PolymarketClient(
        PolymarketConfig(
            gamma_base_url="https://example.com",
            clob_base_url="https://example.com",
            data_base_url="https://example.com",
        )
    )

    check = build_spread_check_from_books(
        pair,
        {
            "orderbook_fp": {
                "yes_dollars": [["0.4300", "25"]],
                "no_dollars": [["0.5400", "75"], ["0.5300", "10"]],
            }
        },
        {
            "bids": [{"price": "0.48", "size": "70"}],
            "asks": [{"price": "0.49", "size": "80"}, {"price": "0.51", "size": "20"}],
        },
        client,
        max_venue_spread=Decimal("0.05"),
        min_buy_size=Decimal("10"),
        min_depth_size=Decimal("50"),
        depth_window=Decimal("0.03"),
        min_edge=Decimal("0"),
        fee_mode="round-trip",
    )

    assert check.polymarket_minus_kalshi == Decimal("0.0300")
    assert check.kalshi_entry_fee == Decimal("0.02")
    assert check.kalshi_exit_fee == Decimal("0.02")
    assert check.kalshi_round_trip_fee == Decimal("0.04")
    assert check.fee_adjusted_edge == Decimal("-0.0100")
    assert check.passes_filters is False
    assert "fee_adjusted_edge_below_minimum" in check.filter_reasons


def test_build_spread_check_from_books_supports_entry_only_fee_mode() -> None:
    pair = MarketPair(
        label="Example",
        kalshi_ticker="KXTEST-26-YES",
        polymarket_token_id="123",
    )
    client = PolymarketClient(
        PolymarketConfig(
            gamma_base_url="https://example.com",
            clob_base_url="https://example.com",
            data_base_url="https://example.com",
        )
    )

    check = build_spread_check_from_books(
        pair,
        {
            "orderbook_fp": {
                "yes_dollars": [["0.4300", "25"]],
                "no_dollars": [["0.5400", "75"], ["0.5300", "10"]],
            }
        },
        {
            "bids": [{"price": "0.48", "size": "70"}],
            "asks": [{"price": "0.49", "size": "80"}, {"price": "0.51", "size": "20"}],
        },
        client,
        max_venue_spread=Decimal("0.05"),
        min_buy_size=Decimal("10"),
        min_depth_size=Decimal("50"),
        depth_window=Decimal("0.03"),
        min_edge=Decimal("0"),
        fee_mode="entry-only",
    )

    assert check.polymarket_minus_kalshi == Decimal("0.0300")
    assert check.kalshi_entry_fee == Decimal("0.02")
    assert check.kalshi_exit_fee == Decimal("0.02")
    assert check.kalshi_round_trip_fee == Decimal("0.04")
    assert check.fee_mode == "entry-only"
    assert check.fee_adjustment == Decimal("0.02")
    assert check.fee_adjusted_edge == Decimal("0.0100")
    assert check.passes_filters is True


def test_save_observation_writes_observation_and_paper_signal(tmp_path) -> None:
    check = build_spread_check_from_books(
        MarketPair(
            label="Example",
            kalshi_ticker="KXTEST-26-YES",
            polymarket_token_id="123",
        ),
        {
            "orderbook_fp": {
                "yes_dollars": [["0.4300", "25"]],
                "no_dollars": [["0.5400", "75"], ["0.5300", "10"]],
            }
        },
        {
            "bids": [{"price": "0.48", "size": "70"}],
            "asks": [{"price": "0.49", "size": "80"}],
        },
        PolymarketClient(
            PolymarketConfig(
                gamma_base_url="https://example.com",
                clob_base_url="https://example.com",
                data_base_url="https://example.com",
            )
        ),
        max_venue_spread=Decimal("0.05"),
        min_buy_size=Decimal("10"),
        min_depth_size=Decimal("50"),
        depth_window=Decimal("0.03"),
        min_edge=Decimal("0"),
        min_fee_adjusted_edge=Decimal("-1"),
    )
    timed_check = TimedSpreadCheck(
        run_id="run-1",
        check=check,
        observed_at="2026-06-20T12:00:00.200000+00:00",
        comparison_started_at="2026-06-20T12:00:00+00:00",
        comparison_completed_at="2026-06-20T12:00:00.200000+00:00",
        kalshi_request_started_at="2026-06-20T12:00:00+00:00",
        kalshi_response_received_at="2026-06-20T12:00:00.100000+00:00",
        kalshi_latency_ms=Decimal("100"),
        polymarket_request_started_at="2026-06-20T12:00:00+00:00",
        polymarket_response_received_at="2026-06-20T12:00:00.150000+00:00",
        polymarket_latency_ms=Decimal("150"),
        response_skew_ms=Decimal("50"),
    )

    db_path = tmp_path / "observations.sqlite"
    observation_id = save_observation(db_path, timed_check)

    with sqlite3.connect(db_path) as connection:
        observation = connection.execute(
            """
            SELECT label, passes_filters, first_response_venue, raw_json
            FROM observations
            WHERE id = ?
            """,
            (observation_id,),
        ).fetchone()
        signal = connection.execute(
            "SELECT simulated_entry_venue, edge FROM paper_signals WHERE observation_id = ?",
            (observation_id,),
        ).fetchone()
        trade = connection.execute(
            """
            SELECT status, entry_price, entry_fee, latest_mark_price, latest_exit_fee,
                latest_gross_unrealized_pnl, latest_unrealized_pnl, observation_count
            FROM paper_trades
            """
        ).fetchone()

    assert observation[0] == "Example"
    assert observation[1] == 1
    assert observation[2] == "kalshi"
    raw_json = json.loads(observation[3])
    assert raw_json["response_skew_ms"] == "50.00"
    assert raw_json["first_response_venue"] == "kalshi"
    assert signal == ("kalshi", "0.0300")
    assert trade == ("open", "0.4600", "0.02", "0.4300", "0.02", "-0.0300", "-0.0700", 1)


def test_save_observation_marks_open_paper_trade(tmp_path) -> None:
    db_path = tmp_path / "observations.sqlite"
    save_observation(
        db_path,
        make_timed_check(
            label="Trade",
            edge_setup="passing",
            kalshi_response_received_at="2026-06-20T12:00:00.100000+00:00",
            polymarket_response_received_at="2026-06-20T12:00:00.150000+00:00",
            response_skew_ms=Decimal("50"),
        ),
    )
    save_observation(
        db_path,
        make_timed_check(
            label="Trade",
            edge_setup="failing",
            kalshi_response_received_at="2026-06-20T12:00:01.100000+00:00",
            polymarket_response_received_at="2026-06-20T12:00:01.150000+00:00",
            response_skew_ms=Decimal("50"),
        ),
    )

    with sqlite3.connect(db_path) as connection:
        trade = connection.execute(
            """
            SELECT latest_mark_price, latest_exit_fee, latest_gross_unrealized_pnl,
                latest_unrealized_pnl, best_unrealized_pnl, worst_unrealized_pnl,
                latest_edge, latest_fair_price, latest_hold_to_resolution_ev,
                best_hold_to_resolution_ev, worst_hold_to_resolution_ev,
                observation_count
            FROM paper_trades
            """
        ).fetchone()
        mark_count = connection.execute("SELECT COUNT(*) FROM paper_trade_marks").fetchone()[0]

    assert trade == (
        "0.4800",
        "0.02",
        "0.0200",
        "-0.0200",
        "-0.0200",
        "-0.0700",
        "-0.0100",
        "0.48",
        "0.0000",
        "0.0050",
        "0.0000",
        2,
    )
    assert mark_count == 2


def test_save_observations_persists_batch_with_signal_fields(tmp_path) -> None:
    db_path = tmp_path / "observations.sqlite"
    results = save_observations(
        db_path,
        [
            make_timed_check(
                label="BatchA",
                edge_setup="passing",
                kalshi_response_received_at="2026-06-20T12:00:00.100000+00:00",
                polymarket_response_received_at="2026-06-20T12:00:00.150000+00:00",
                response_skew_ms=Decimal("50"),
            ),
            make_timed_check(
                label="BatchB",
                edge_setup="failing",
                kalshi_response_received_at="2026-06-20T12:00:01.100000+00:00",
                polymarket_response_received_at="2026-06-20T12:00:01.150000+00:00",
                response_skew_ms=Decimal("50"),
            ),
        ],
    )

    with sqlite3.connect(db_path) as connection:
        observation_count = connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
        trade_count = connection.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0]

    assert [result.observation_id for result in results] == [1, 2]
    assert results[0].signal_fields["passes_filters"] is True
    assert results[1].signal_fields["passes_filters"] is False
    assert observation_count == 2
    assert trade_count == 1


def test_save_observation_can_close_paper_trade_before_resolution(tmp_path) -> None:
    db_path = tmp_path / "observations.sqlite"
    save_observation(
        db_path,
        make_timed_check(
            label="Exit",
            edge_setup="passing",
            kalshi_response_received_at="2026-06-20T12:00:00.100000+00:00",
            polymarket_response_received_at="2026-06-20T12:00:00.150000+00:00",
            response_skew_ms=Decimal("50"),
        ),
    )
    save_observation(
        db_path,
        make_timed_check(
            label="Exit",
            edge_setup="failing",
            kalshi_response_received_at="2026-06-20T12:01:00.100000+00:00",
            polymarket_response_received_at="2026-06-20T12:01:00.150000+00:00",
            response_skew_ms=Decimal("50"),
        ),
        paper_exit_config=PaperExitConfig(exit_edge=Decimal("0")),
    )

    with sqlite3.connect(db_path) as connection:
        trade = connection.execute(
            """
            SELECT status, closed_at, close_reason, exit_price, exit_fee,
                realized_gross_pnl, realized_pnl, latest_mark_price,
                latest_gross_unrealized_pnl, latest_unrealized_pnl, observation_count
            FROM paper_trades
            """
        ).fetchone()

    assert trade == (
        "closed",
        "2026-06-20T12:01:00.150000+00:00",
        "edge_closed",
        "0.4800",
        "0.02",
        "0.0200",
        "-0.0200",
        "0.4800",
        "0.0200",
        "-0.0200",
        2,
    )


def test_save_observation_writes_paper_trade_log_and_pnl_snapshot(tmp_path) -> None:
    db_path = tmp_path / "observations.sqlite"
    trade_log_path = tmp_path / "paper_trades.jsonl"
    pnl_log_path = tmp_path / "paper_pnl.json"

    save_observation(
        db_path,
        make_timed_check(
            label="Logged",
            edge_setup="passing",
            kalshi_response_received_at="2026-06-20T12:00:00.100000+00:00",
            polymarket_response_received_at="2026-06-20T12:00:00.150000+00:00",
            response_skew_ms=Decimal("50"),
        ),
        paper_trade_log_path=trade_log_path,
        paper_pnl_log_path=pnl_log_path,
    )

    trade_events = [
        json.loads(line) for line in trade_log_path.read_text().splitlines()
    ]
    pnl_snapshot = json.loads(pnl_log_path.read_text())

    assert len(trade_events) == 1
    assert trade_events[0] == {
        "close_reason": None,
        "edge": "0.0300",
        "entry_fee": "0.02",
        "event": "open",
        "exit_fee": "0.02",
        "fee_adjustment": "0.02",
        "fee_mode": "entry-only",
        "fair_value_provider": None,
        "gross_pnl": "-0.0300",
        "hold_to_resolution_ev": "0.0050",
        "hold_to_resolution_fair_price": "0.485",
        "kalshi_ticker": "KXTEST-26-LOGGED",
        "kalshi_url": "https://kalshi.com/search?query=KXTEST-26-LOGGED",
        "market": "Logged",
        "net_pnl": "-0.0700",
        "observation_id": 1,
        "outcome": "yes",
        "polymarket_token_id": "token-Logged",
        "polymarket_url": "https://polymarket.com/search?query=token-Logged",
        "purchase_price": "0.4600",
        "quantity": "1",
        "run_id": "run-Logged",
        "sell_price": "0.4300",
        "timestamp": "2026-06-20T12:00:00.150000+00:00",
        "trade_id": 1,
    }
    assert pnl_snapshot["trade_count"] == 1
    assert pnl_snapshot["open_trade_count"] == 1
    assert pnl_snapshot["closed_trade_count"] == 0
    assert pnl_snapshot["total_open_unrealized_pnl"] == "-0.0700"
    assert pnl_snapshot["total_pnl"] == "-0.0700"
    assert pnl_snapshot["total_open_hold_to_resolution_ev"] == "0.0050"
    assert pnl_snapshot["open_trades"][0]["market"] == "Logged"
    assert pnl_snapshot["open_trades"][0]["current_hold_to_resolution_ev"] == "0.0050"

    save_observation(
        db_path,
        make_timed_check(
            label="Logged",
            edge_setup="failing",
            kalshi_response_received_at="2026-06-20T12:01:00.100000+00:00",
            polymarket_response_received_at="2026-06-20T12:01:00.150000+00:00",
            response_skew_ms=Decimal("50"),
        ),
        paper_exit_config=PaperExitConfig(exit_edge=Decimal("0")),
        paper_trade_log_path=trade_log_path,
        paper_pnl_log_path=pnl_log_path,
    )

    trade_events = [
        json.loads(line) for line in trade_log_path.read_text().splitlines()
    ]
    pnl_snapshot = json.loads(pnl_log_path.read_text())

    assert len(trade_events) == 2
    assert trade_events[1]["event"] == "close"
    assert trade_events[1]["close_reason"] == "edge_closed"
    assert trade_events[1]["fee_mode"] == "entry-only"
    assert trade_events[1]["fee_adjustment"] == "0.02"
    assert trade_events[1]["market"] == "Logged"
    assert trade_events[1]["purchase_price"] == "0.4600"
    assert trade_events[1]["sell_price"] == "0.4800"
    assert trade_events[1]["gross_pnl"] == "0.0200"
    assert trade_events[1]["net_pnl"] == "-0.0200"
    assert trade_events[1]["hold_to_resolution_ev"] == "0.0000"
    assert pnl_snapshot["trade_count"] == 1
    assert pnl_snapshot["open_trade_count"] == 0
    assert pnl_snapshot["closed_trade_count"] == 1
    assert pnl_snapshot["total_realized_pnl"] == "-0.0200"
    assert pnl_snapshot["total_open_unrealized_pnl"] == "0"
    assert pnl_snapshot["total_open_hold_to_resolution_ev"] == "0"
    assert pnl_snapshot["total_pnl"] == "-0.0200"
    assert pnl_snapshot["open_trades"] == []


def test_save_observation_tracks_open_interest_delta(tmp_path) -> None:
    db_path = tmp_path / "observations.sqlite"
    save_observation(
        db_path,
        make_timed_check(
            label="OI",
            edge_setup="failing",
            kalshi_response_received_at="2026-06-20T12:00:00.100000+00:00",
            polymarket_response_received_at="2026-06-20T12:00:00.150000+00:00",
            response_skew_ms=Decimal("50"),
            condition_id="0xabc",
            open_interest=Decimal("1000"),
        ),
    )
    save_observation(
        db_path,
        make_timed_check(
            label="OI",
            edge_setup="failing",
            kalshi_response_received_at="2026-06-20T12:00:01.100000+00:00",
            polymarket_response_received_at="2026-06-20T12:00:01.150000+00:00",
            response_skew_ms=Decimal("50"),
            condition_id="0xabc",
            open_interest=Decimal("1125"),
        ),
    )

    with sqlite3.connect(db_path) as connection:
        latest = connection.execute(
            """
            SELECT polymarket_open_interest, polymarket_open_interest_previous,
                polymarket_open_interest_delta, polymarket_open_interest_delta_pct
            FROM observations
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    assert latest == ("1125", "1000", "125", "0.125")


def test_save_observation_applies_mid_oi_volume_momentum_filters(tmp_path) -> None:
    db_path = tmp_path / "observations.sqlite"
    save_observation(
        db_path,
        make_timed_check(
            label="Momentum",
            edge_setup="passing",
            kalshi_response_received_at="2026-06-20T12:00:00.100000+00:00",
            polymarket_response_received_at="2026-06-20T12:00:00.150000+00:00",
            response_skew_ms=Decimal("50"),
            condition_id="0xmomentum",
            open_interest=Decimal("1000"),
            volume=Decimal("5000"),
            polymarket_bid=Decimal("0.47"),
            polymarket_ask=Decimal("0.48"),
        ),
    )
    observation_id = save_observation(
        db_path,
        make_timed_check(
            label="Momentum",
            edge_setup="passing",
            kalshi_response_received_at="2026-06-20T12:10:00.100000+00:00",
            polymarket_response_received_at="2026-06-20T12:10:00.150000+00:00",
            response_skew_ms=Decimal("50"),
            condition_id="0xmomentum",
            open_interest=Decimal("1050"),
            volume=Decimal("5400"),
            polymarket_bid=Decimal("0.52"),
            polymarket_ask=Decimal("0.53"),
        ),
        signal_lookback_minutes=10,
        min_mid_edge=Decimal("0.05"),
        min_poly_mid_move=Decimal("0.03"),
        min_poly_oi_delta=Decimal("10"),
        min_poly_volume_delta=Decimal("100"),
        max_kalshi_mid_move=Decimal("0.01"),
    )

    with sqlite3.connect(db_path) as connection:
        latest = connection.execute(
            """
            SELECT passes_filters, filter_reasons, polymarket_mid_delta,
                polymarket_open_interest_delta, polymarket_volume_delta,
                kalshi_mid_delta, polymarket_mid_minus_kalshi_mid
            FROM observations
            WHERE id = ?
            """,
            (observation_id,),
        ).fetchone()

    assert latest == (1, "", "0.050", "50", "400", "0.0000", "0.0800")


def test_process_batch_results_records_enabled_strategy_signals(tmp_path) -> None:
    db_path = tmp_path / "observations.sqlite"
    timed_check = make_timed_check(
        label="Strategy",
        edge_setup="passing",
        kalshi_response_received_at="2026-06-20T12:00:00.100000+00:00",
        polymarket_response_received_at="2026-06-20T12:00:00.150000+00:00",
        response_skew_ms=Decimal("50"),
    )
    pair = MarketPair(
        label="Strategy",
        kalshi_ticker="KXTEST-26-STRATEGY",
        polymarket_token_id="token-Strategy",
    )

    results, dropped = asyncio.run(
        process_batch_results(
            db_path=db_path,
            pairs=[pair],
            timed_checks=[timed_check],
            run_id="run-Strategy",
            consecutive_failures={},
            drop_failed_pairs_after=3,
            signal_lookback_minutes=10,
            min_mid_edge=Decimal("0"),
            min_poly_mid_move=Decimal("0"),
            min_poly_oi_delta=Decimal("0"),
            min_poly_volume_delta=Decimal("0"),
            max_kalshi_mid_move=Decimal("1"),
            paper_exit_config=PaperExitConfig(),
            paper_trade_log_path=None,
            paper_pnl_log_path=None,
            metadata_cache={},
            refresh_flags={heartbeat_pair_key(pair): False},
            refreshed_at=0,
            strategy_config=StrategyEngineConfig(
                enabled_strategy_ids=("legacy_fee_adjusted_edge",),
            ),
        )
    )

    with sqlite3.connect(db_path) as connection:
        signal = connection.execute(
            """
            SELECT strategy_id, strategy_version, signal_type, kalshi_ticker,
                polymarket_token_id, edge, fee_adjusted_edge, reasons_json
            FROM strategy_signals
            """
        ).fetchone()
        paper_trade_count = connection.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0]
    summary = analyze_database(db_path, strategy_signal_limit=1)

    assert dropped == set()
    assert results[0]["strategy_signal_count"] == 1
    assert paper_trade_count == 1
    assert signal == (
        "legacy_fee_adjusted_edge",
        "1",
        "paper_open",
        "KXTEST-26-STRATEGY",
        "token-Strategy",
        "0.0300",
        "0.0100",
        "[\"passes_existing_heartbeat_filters\"]",
    )
    assert summary["strategy_signals"]["signal_count"] == 1
    assert summary["strategy_signals"]["signal_rate"] == "100.00%"
    assert summary["strategy_signals"]["signal_type_counts"] == {"paper_open": 1}
    assert summary["strategy_signals"]["reason_counts"] == {
        "passes_existing_heartbeat_filters": 1
    }
    assert summary["strategy_signals"]["rejection_reason_counts"] == {}
    assert summary["strategy_signals"]["strategies"] == [
        {
            "strategy_id": "legacy_fee_adjusted_edge",
            "strategy_version": "1",
            "signal_count": 1,
            "signal_rate": "100.00%",
            "signal_type_counts": {"paper_open": 1},
            "reason_counts": {"passes_existing_heartbeat_filters": 1},
            "rejection_reason_counts": {},
            "first_signal_at": summary["strategy_signals"]["strategies"][0]["first_signal_at"],
            "last_signal_at": summary["strategy_signals"]["strategies"][0]["last_signal_at"],
            "score": {"average": None},
            "confidence": {"average": "1.0000"},
            "edge": {
                "average": "0.0300",
                "minimum": "0.0300",
                "maximum": "0.0300",
            },
            "fee_adjusted_edge": {
                "average": "0.0100",
                "minimum": "0.0100",
                "maximum": "0.0100",
            },
        }
    ]
    assert summary["strategy_signals"]["recent"][0] | {
        "created_at": summary["strategy_signals"]["recent"][0]["created_at"],
    } == {
        "id": 1,
        "observation_id": 1,
        "run_id": "run-Strategy",
        "observed_at": "2026-06-20T12:00:00.150000+00:00",
        "created_at": summary["strategy_signals"]["recent"][0]["created_at"],
        "strategy_id": "legacy_fee_adjusted_edge",
        "strategy_version": "1",
        "signal_type": "paper_open",
        "label": "Strategy",
        "outcome": "yes",
        "kalshi_ticker": "KXTEST-26-STRATEGY",
        "polymarket_token_id": "token-Strategy",
        "side": "yes",
        "direction": "buy_yes",
        "score": None,
        "confidence": "1",
        "fair_value": "0.485",
        "entry_price": "0.4600",
        "mark_price": None,
        "edge": "0.0300",
        "fee_adjusted_edge": "0.0100",
        "reasons": ["passes_existing_heartbeat_filters"],
        "rejection_reasons": [],
        "metadata": {
            "fair_value_provider": "polymarket_mid",
            "source": "existing_heartbeat_filters",
        },
    }


def test_process_batch_results_opens_strategy_specific_paper_trade_when_enabled(
    tmp_path,
) -> None:
    db_path = tmp_path / "observations.sqlite"
    trade_log_path = tmp_path / "paper_trades.jsonl"
    pnl_log_path = tmp_path / "paper_pnl.json"
    timed_check = make_timed_check(
        label="StrategyTrade",
        edge_setup="passing",
        kalshi_response_received_at="2026-06-20T12:00:00.100000+00:00",
        polymarket_response_received_at="2026-06-20T12:00:00.150000+00:00",
        response_skew_ms=Decimal("50"),
    )
    pair = MarketPair(
        label="StrategyTrade",
        kalshi_ticker="KXTEST-26-STRATEGYTRADE",
        polymarket_token_id="token-StrategyTrade",
    )

    results, dropped = asyncio.run(
        process_batch_results(
            db_path=db_path,
            pairs=[pair],
            timed_checks=[timed_check],
            run_id="run-StrategyTrade",
            consecutive_failures={},
            drop_failed_pairs_after=3,
            signal_lookback_minutes=10,
            min_mid_edge=Decimal("0"),
            min_poly_mid_move=Decimal("0"),
            min_poly_oi_delta=Decimal("0"),
            min_poly_volume_delta=Decimal("0"),
            max_kalshi_mid_move=Decimal("1"),
            paper_exit_config=PaperExitConfig(),
            paper_trade_log_path=trade_log_path,
            paper_pnl_log_path=pnl_log_path,
            metadata_cache={},
            refresh_flags={heartbeat_pair_key(pair): False},
            refreshed_at=0,
            strategy_config=StrategyEngineConfig(
                enabled_strategy_ids=("legacy_fee_adjusted_edge",),
                paper_trade_strategy_ids=("legacy_fee_adjusted_edge",),
            ),
        )
    )

    with sqlite3.connect(db_path) as connection:
        trade_rows = connection.execute(
            """
            SELECT signal_id, strategy_signal_id, strategy_id, strategy_version,
                fair_value_provider, entry_policy, exit_policy, side, direction,
                status, observation_count
            FROM paper_trades
            ORDER BY id
            """
        ).fetchall()
        mark_count = connection.execute("SELECT COUNT(*) FROM paper_trade_marks").fetchone()[0]
    trade_events = [json.loads(line) for line in trade_log_path.read_text().splitlines()]
    pnl_snapshot = json.loads(pnl_log_path.read_text())
    summary = analyze_database(db_path, strategy_signal_limit=1)

    assert dropped == set()
    assert results[0]["strategy_signal_count"] == 1
    assert results[0]["strategy_paper_trade_count"] == 1
    assert trade_rows == [
        (1, None, None, None, None, None, None, None, None, "open", 1),
        (
                None,
                1,
                "legacy_fee_adjusted_edge",
                "1",
                "polymarket_mid",
                "paper_open_signal",
            "heartbeat_paper_exit_config",
            "yes",
            "buy_yes",
            "open",
            1,
        ),
    ]
    assert mark_count == 1
    assert [event["event"] for event in trade_events] == ["open", "open"]
    assert "strategy_id" not in trade_events[0]
    assert trade_events[1]["strategy_id"] == "legacy_fee_adjusted_edge"
    assert trade_events[1]["fair_value_provider"] == "polymarket_mid"
    assert trade_events[1]["strategy_signal_id"] == 1
    assert trade_events[1]["side"] == "yes"
    assert trade_events[1]["direction"] == "buy_yes"
    assert pnl_snapshot["trade_count"] == 2
    assert summary["paper_trades"]["trade_count"] == 2
    assert summary["paper_trades"]["by_strategy"] == [
        {
            "strategy_id": None,
            "strategy_version": None,
            "trade_count": 1,
            "open_trade_count": 1,
            "closed_trade_count": 0,
            "average_latest_unrealized_pnl": "-0.0700",
            "total_latest_unrealized_pnl": "-0.0700",
            "average_latest_gross_unrealized_pnl": "-0.0300",
            "total_latest_gross_unrealized_pnl": "-0.0300",
            "average_latest_hold_to_resolution_ev": "0.0050",
            "total_latest_hold_to_resolution_ev": "0.0050",
            "total_entry_fees": "0.0200",
            "total_latest_exit_fees": "0.0200",
            "average_realized_pnl": None,
            "total_realized_pnl": None,
            "average_realized_gross_pnl": None,
            "total_realized_gross_pnl": None,
            "total_realized_exit_fees": None,
            "best_unrealized_pnl": "-0.0700",
            "worst_unrealized_pnl": "-0.0700",
            "best_hold_to_resolution_ev": "0.0050",
            "worst_hold_to_resolution_ev": "0.0050",
        },
        {
            "strategy_id": "legacy_fee_adjusted_edge",
            "strategy_version": "1",
            "trade_count": 1,
            "open_trade_count": 1,
            "closed_trade_count": 0,
            "average_latest_unrealized_pnl": "-0.0700",
            "total_latest_unrealized_pnl": "-0.0700",
            "average_latest_gross_unrealized_pnl": "-0.0300",
            "total_latest_gross_unrealized_pnl": "-0.0300",
            "average_latest_hold_to_resolution_ev": "0.0050",
            "total_latest_hold_to_resolution_ev": "0.0050",
            "total_entry_fees": "0.0200",
            "total_latest_exit_fees": "0.0200",
            "average_realized_pnl": None,
            "total_realized_pnl": None,
            "average_realized_gross_pnl": None,
            "total_realized_gross_pnl": None,
            "total_realized_exit_fees": None,
            "best_unrealized_pnl": "-0.0700",
            "worst_unrealized_pnl": "-0.0700",
            "best_hold_to_resolution_ev": "0.0050",
            "worst_hold_to_resolution_ev": "0.0050",
        },
    ]


def test_analyze_database_summarizes_observations(tmp_path) -> None:
    db_path = tmp_path / "observations.sqlite"
    save_observation(
        db_path,
        make_timed_check(
            label="Passing",
            edge_setup="passing",
            kalshi_response_received_at="2026-06-20T12:00:00.100000+00:00",
            polymarket_response_received_at="2026-06-20T12:00:00.150000+00:00",
            response_skew_ms=Decimal("50"),
        ),
    )
    save_observation(
        db_path,
        make_timed_check(
            label="Failing",
            edge_setup="failing",
            kalshi_response_received_at="2026-06-20T12:00:01.250000+00:00",
            polymarket_response_received_at="2026-06-20T12:00:01.150000+00:00",
            response_skew_ms=Decimal("100"),
        ),
    )

    summary = analyze_database(db_path)

    assert summary["observation_count"] == 2
    assert summary["paper_signal_count"] == 1
    assert summary["paper_trades"] == {
        "average_latest_hold_to_resolution_ev": "0.0050",
        "average_latest_gross_unrealized_pnl": "-0.0300",
        "average_realized_pnl": None,
        "average_realized_gross_pnl": None,
        "average_latest_unrealized_pnl": "-0.0700",
        "best_hold_to_resolution_ev": "0.0050",
        "best_unrealized_pnl": "-0.0700",
        "by_strategy": [
            {
                "strategy_id": None,
                "strategy_version": None,
                "trade_count": 1,
                "open_trade_count": 1,
                "closed_trade_count": 0,
                "average_latest_unrealized_pnl": "-0.0700",
                "total_latest_unrealized_pnl": "-0.0700",
                "average_latest_gross_unrealized_pnl": "-0.0300",
                "total_latest_gross_unrealized_pnl": "-0.0300",
                "average_latest_hold_to_resolution_ev": "0.0050",
                "total_latest_hold_to_resolution_ev": "0.0050",
                "total_entry_fees": "0.0200",
                "total_latest_exit_fees": "0.0200",
                "average_realized_pnl": None,
                "total_realized_pnl": None,
                "average_realized_gross_pnl": None,
                "total_realized_gross_pnl": None,
                "total_realized_exit_fees": None,
                "best_unrealized_pnl": "-0.0700",
                "worst_unrealized_pnl": "-0.0700",
                "best_hold_to_resolution_ev": "0.0050",
                "worst_hold_to_resolution_ev": "0.0050",
            }
        ],
        "close_reason_counts": {},
        "closed_trade_count": 0,
        "open_trade_count": 1,
        "total_entry_fees": "0.0200",
        "total_latest_hold_to_resolution_ev": "0.0050",
        "total_latest_exit_fees": "0.0200",
        "total_latest_gross_unrealized_pnl": "-0.0300",
        "total_realized_pnl": None,
        "total_realized_exit_fees": None,
        "total_realized_gross_pnl": None,
        "total_latest_unrealized_pnl": "-0.0700",
        "trade_count": 1,
        "worst_hold_to_resolution_ev": "0.0050",
        "worst_unrealized_pnl": "-0.0700",
    }
    assert summary["passing_observation_count"] == 1
    assert summary["passing_observation_rate"] == "50.00%"
    assert summary["kalshi_lower_count"] == 1
    assert summary["positive_edge_count"] == 1
    assert summary["edge"] == {
        "average": "0.0100",
        "minimum": "-0.0100",
        "maximum": "0.0300",
    }
    assert summary["latency"]["first_response_counts"] == {
        "kalshi": 1,
        "polymarket": 1,
    }
    assert summary["filter_reason_counts"] == {
        "edge_below_minimum": 1,
        "kalshi_not_lower": 1,
        "mid_edge_below_minimum": 1,
    }
    assert {market["label"] for market in summary["markets"]} == {"Passing", "Failing"}


def make_timed_check(
    *,
    label: str,
    edge_setup: str,
    kalshi_response_received_at: str,
    polymarket_response_received_at: str,
    response_skew_ms: Decimal,
    condition_id: str | None = None,
    open_interest: Decimal | None = None,
    volume: Decimal | None = None,
    polymarket_bid: Decimal | None = None,
    polymarket_ask: Decimal | None = None,
) -> TimedSpreadCheck:
    if edge_setup == "passing":
        kalshi_book = {
            "orderbook_fp": {
                "yes_dollars": [["0.4300", "25"]],
                "no_dollars": [["0.5400", "75"], ["0.5300", "10"]],
            }
        }
        polymarket_book = {
            "bids": [{"price": str(polymarket_bid or Decimal("0.48")), "size": "70"}],
            "asks": [{"price": str(polymarket_ask or Decimal("0.49")), "size": "80"}],
        }
    else:
        kalshi_book = {
            "orderbook_fp": {
                "yes_dollars": [["0.4800", "70"]],
                "no_dollars": [["0.5000", "75"], ["0.4900", "10"]],
            }
        }
        polymarket_book = {
            "bids": [{"price": str(polymarket_bid or Decimal("0.47")), "size": "70"}],
            "asks": [{"price": str(polymarket_ask or Decimal("0.49")), "size": "80"}],
        }

    check = build_spread_check_from_books(
        MarketPair(
            label=label,
            kalshi_ticker=f"KXTEST-26-{label.upper()}",
            polymarket_token_id=f"token-{label}",
            polymarket_condition_id=condition_id,
        ),
        kalshi_book,
        polymarket_book,
        PolymarketClient(
            PolymarketConfig(
                gamma_base_url="https://example.com",
                clob_base_url="https://example.com",
                data_base_url="https://example.com",
            )
        ),
        max_venue_spread=Decimal("0.05"),
        min_buy_size=Decimal("10"),
        min_depth_size=Decimal("50"),
        depth_window=Decimal("0.03"),
        min_edge=Decimal("0"),
        min_fee_adjusted_edge=Decimal("-1"),
        polymarket_open_interest=open_interest,
        polymarket_volume=volume,
    )
    return TimedSpreadCheck(
        run_id=f"run-{label}",
        check=check,
        observed_at=polymarket_response_received_at,
        comparison_started_at="2026-06-20T12:00:00+00:00",
        comparison_completed_at=polymarket_response_received_at,
        kalshi_request_started_at="2026-06-20T12:00:00+00:00",
        kalshi_response_received_at=kalshi_response_received_at,
        kalshi_latency_ms=Decimal("100"),
        polymarket_request_started_at="2026-06-20T12:00:00+00:00",
        polymarket_response_received_at=polymarket_response_received_at,
        polymarket_latency_ms=Decimal("150"),
        response_skew_ms=response_skew_ms,
    )
