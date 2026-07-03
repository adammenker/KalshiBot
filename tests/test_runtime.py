from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

from kalshibot.cli import build_parser
from kalshibot.runtime.active_pairs import (
    active_pair_key,
    load_active_market_pairs,
    mark_pair_inactive,
    reset_pair_failure,
    upsert_active_market_pairs,
    write_active_pairs_snapshot,
)
from kalshibot.runtime.live_sports import (
    fetch_live_sports_kalshi_markets,
    live_data_confirms_active_game,
    milestone_from_dict,
    milestone_is_live_candidate,
)
from kalshibot.spreads import MarketPair
from kalshibot.storage import connect_database, initialize_database


def test_active_market_pairs_round_trip_and_inactivation(tmp_path: Path) -> None:
    db_path = tmp_path / "observations.sqlite"
    initialize_database(db_path)
    pair = MarketPair(
        label="Team A vs Team B",
        kalshi_ticker="KXGAME-TEAMATEAMB-YES",
        polymarket_token_id="token-1",
        polymarket_condition_id="condition-1",
        confidence=Decimal("0.95"),
    )

    with connect_database(db_path) as connection:
        upsert_active_market_pairs(connection, [pair], source="test")
        connection.commit()
        loaded = load_active_market_pairs(connection)

        assert loaded == [pair]

        mark_pair_inactive(
            connection,
            pair,
            status="inactive_closed",
            reason="kalshi_status_closed",
        )
        connection.commit()

        assert load_active_market_pairs(connection) == []


def test_active_market_pair_failure_count_resets(tmp_path: Path) -> None:
    db_path = tmp_path / "observations.sqlite"
    initialize_database(db_path)
    pair = MarketPair(
        label="Team A vs Team B",
        kalshi_ticker="KXGAME-TEAMATEAMB-YES",
        polymarket_token_id="token-1",
    )

    with connect_database(db_path) as connection:
        upsert_active_market_pairs(connection, [pair], source="test")
        from kalshibot.runtime.active_pairs import record_pair_failure

        assert record_pair_failure(connection, pair) == 1
        assert record_pair_failure(connection, pair) == 2
        reset_pair_failure(connection, pair)
        row = connection.execute(
            "SELECT consecutive_failures FROM active_market_pairs WHERE pair_key = ?",
            (active_pair_key(pair),),
        ).fetchone()

    assert row["consecutive_failures"] == 0


def test_write_active_pairs_snapshot(tmp_path: Path) -> None:
    output = tmp_path / "active.json"
    pair = MarketPair(
        label="Team A vs Team B",
        kalshi_ticker="KXGAME-TEAMATEAMB-YES",
        polymarket_token_id="token-1",
    )

    write_active_pairs_snapshot(output, [pair])
    payload = json.loads(output.read_text())

    assert payload["markets"][0]["kalshi_ticker"] == "KXGAME-TEAMATEAMB-YES"
    assert payload["markets"][0]["polymarket_token_id"] == "token-1"


def test_active_pair_storage_creates_active_market_pairs_table(tmp_path: Path) -> None:
    db_path = tmp_path / "observations.sqlite"
    initialize_database(db_path)

    with connect_database(db_path) as connection:
        load_active_market_pairs(connection)
        table_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM sqlite_master
            WHERE type = 'table' AND name = 'active_market_pairs'
            """
        ).fetchone()[0]

    assert table_count == 1


def test_milestone_live_candidate_requires_live_status_and_time_window() -> None:
    now = datetime(2026, 7, 2, 20, 0, tzinfo=timezone.utc)
    live = milestone_from_dict(
        {
            "id": "milestone-1",
            "title": "A vs B",
            "type": "baseball_game",
            "start_date": "2026-07-02T19:00:00Z",
            "details": {"status": "inprogress", "league": "MLB"},
            "primary_event_tickers": ["KXMLBGAME-26JUL02AB"],
        }
    )
    scheduled = milestone_from_dict(
        {
            "id": "milestone-2",
            "title": "C vs D",
            "start_date": "2026-07-02T20:05:00Z",
            "details": {"status": "scheduled"},
            "primary_event_tickers": ["KXMLBGAME-26JUL02CD"],
        }
    )

    assert milestone_is_live_candidate(live, now=now) is True
    assert milestone_is_live_candidate(scheduled, now=now) is False


def test_live_data_rejects_finished_game_even_when_fallback_is_live() -> None:
    assert (
        live_data_confirms_active_game(
            {
                "milestone_id": "milestone-1",
                "details": {
                    "status": "closed",
                    "match_status": "ended",
                    "widget_status": "finished",
                },
            },
            fallback_status="live",
        )
        is False
    )
    assert live_data_confirms_active_game(None, fallback_status="inprogress") is True


def test_fetch_live_sports_kalshi_markets_uses_confirmed_milestone_event_tickers() -> None:
    client = FakeKalshiLiveClient()

    result = fetch_live_sports_kalshi_markets(
        client,  # type: ignore[arg-type]
        now=datetime(2026, 7, 2, 20, 0, tzinfo=timezone.utc),
        kalshi_market_types={"game_winner"},
    )

    assert result.milestones_seen == 2
    assert len(result.live_milestones) == 1
    assert result.event_tickers == ("KXMLBGAME-26JUL02AB",)
    assert [market.ticker for market in result.markets] == ["KXMLBGAME-26JUL02AB-A"]


def test_run_bot_parser_accepts_dynamic_runtime_flags() -> None:
    args = build_parser().parse_args(
        [
            "run-bot",
            "--heartbeat-interval-ms",
            "250",
            "--discovery-interval-seconds",
            "60",
            "--runtime-minutes",
            "1",
            "--no-llm",
        ]
    )

    assert args.command == "run-bot"
    assert args.heartbeat_interval_ms == Decimal("250")
    assert args.discovery_interval_seconds == Decimal("60")
    assert args.runtime_minutes == Decimal("1")
    assert args.no_llm is True


class FakeKalshiLiveClient:
    def list_milestones(self, **_: Any) -> list[dict[str, Any]]:
        return [
            {
                "id": "live-1",
                "title": "A vs B",
                "type": "baseball_game",
                "start_date": "2026-07-02T19:00:00Z",
                "details": {"status": "inprogress", "league": "MLB"},
                "primary_event_tickers": ["KXMLBGAME-26JUL02AB"],
            },
            {
                "id": "done-1",
                "title": "C vs D",
                "type": "baseball_game",
                "start_date": "2026-07-02T18:00:00Z",
                "details": {"status": "complete", "league": "MLB"},
                "primary_event_tickers": ["KXMLBGAME-26JUL02CD"],
            },
        ]

    def get_live_data_batch(self, milestone_ids: list[str]) -> list[dict[str, Any]]:
        assert milestone_ids == ["live-1"]
        return [
            {
                "milestone_id": "live-1",
                "details": {"status": "inprogress"},
            }
        ]

    def list_markets(self, **kwargs: Any) -> list[dict[str, Any]]:
        assert kwargs["event_ticker"] == "KXMLBGAME-26JUL02AB"
        return [
            {
                "ticker": "KXMLBGAME-26JUL02AB-A",
                "event_ticker": "KXMLBGAME-26JUL02AB",
                "title": "Will A beat B?",
                "yes_sub_title": "A",
                "close_time": "2026-07-02T23:00:00Z",
                "volume_24h_fp": "100",
            },
            {
                "ticker": "KXMLBTOTAL-26JUL02AB",
                "event_ticker": "KXMLBTOTAL-26JUL02AB",
                "title": "Total runs scored by A and B",
                "yes_sub_title": "Over 8.5",
            },
        ]
