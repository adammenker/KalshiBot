from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

from kalshibot.analysis import analyze_database
from kalshibot.backtest import (
    backfill_pair_history,
    format_backfill_summary,
    run_historical_backtest,
)
from kalshibot.client import KalshiClient
from kalshibot.config import load_config, load_polymarket_config
from kalshibot.polymarket import PolymarketClient
from kalshibot.spreads import load_market_pairs


def add_history_parsers(subparsers: Any) -> None:
    analyze = subparsers.add_parser(
        "analyze",
        help="Summarize heartbeat observations and paper signals from SQLite",
    )
    analyze.add_argument(
        "--db",
        type=Path,
        default=Path("data/observations.sqlite"),
        help="SQLite database path",
    )
    analyze.add_argument(
        "--market-limit",
        type=int,
        default=20,
        help="Maximum per-market summaries to include",
    )
    analyze.add_argument(
        "--strategy-signal-limit",
        type=int,
        default=20,
        help="Maximum recent strategy signals to include",
    )

    backfill = subparsers.add_parser(
        "backfill-history",
        help="Fetch historical Kalshi/Polymarket prices for mapped pairs",
    )
    backfill.add_argument("--pairs", type=Path, required=True, help="Path to a JSON market-pair file")
    backfill.add_argument(
        "--db",
        type=Path,
        default=Path("data/history.sqlite"),
        help="SQLite database path",
    )
    backfill.add_argument("--start", required=True, help="Start time as Unix timestamp or ISO string")
    backfill.add_argument("--end", required=True, help="End time as Unix timestamp or ISO string")
    backfill.add_argument(
        "--period-interval",
        type=int,
        default=1,
        choices=[1, 60, 1440],
        help="Kalshi candle period in minutes",
    )
    backfill.add_argument(
        "--polymarket-interval",
        default="1m",
        help="Polymarket history interval, such as 1m, 1h, 1d, or max",
    )
    backfill.add_argument(
        "--series-ticker",
        help="Optional Kalshi series ticker override. Defaults to ticker prefix before first dash.",
    )

    backtest = subparsers.add_parser(
        "backtest-history",
        help="Run a conservative historical price backtest from backfilled data",
    )
    backtest.add_argument(
        "--db",
        type=Path,
        default=Path("data/history.sqlite"),
        help="SQLite database path",
    )
    backtest.add_argument(
        "--min-edge",
        type=Decimal,
        default=Decimal("0.02"),
        help="Minimum Polymarket-minus-Kalshi edge required to enter",
    )
    backtest.add_argument(
        "--hold-period-minutes",
        type=int,
        default=10,
        help="Exit after this many historical periods if spread has not closed",
    )
    backtest.add_argument(
        "--slippage",
        type=Decimal,
        default=Decimal("0"),
        help="Conservative price penalty applied on entry and exit",
    )


def run_analyze(db_path: Path, market_limit: int, strategy_signal_limit: int = 20) -> int:
    if market_limit < 1:
        raise ValueError("--market-limit must be at least 1")
    if strategy_signal_limit < 0:
        raise ValueError("--strategy-signal-limit cannot be negative")
    print(
        json.dumps(
            analyze_database(
                db_path,
                market_limit=market_limit,
                strategy_signal_limit=strategy_signal_limit,
            ),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def run_backfill_history(
    *,
    pairs_path: Path,
    db_path: Path,
    start: str,
    end: str,
    period_interval: int,
    polymarket_interval: str,
    series_ticker: str | None,
) -> int:
    start_ts = parse_timestamp(start)
    end_ts = parse_timestamp(end)
    if end_ts <= start_ts:
        raise ValueError("--end must be after --start")

    kalshi_client = KalshiClient(load_config())
    polymarket_client = PolymarketClient(load_polymarket_config())
    results = [
        format_backfill_summary(
            backfill_pair_history(
                db_path=db_path,
                pair=pair,
                kalshi_client=kalshi_client,
                polymarket_client=polymarket_client,
                start_ts=start_ts,
                end_ts=end_ts,
                period_interval=period_interval,
                polymarket_interval=polymarket_interval,
                series_ticker=series_ticker,
            )
        )
        for pair in load_market_pairs(pairs_path)
    ]
    print(json.dumps(results, indent=2, sort_keys=True))
    return 0


def run_backtest_history(
    *,
    db_path: Path,
    min_edge: Decimal,
    hold_period_minutes: int,
    slippage: Decimal,
) -> int:
    print(
        json.dumps(
            run_historical_backtest(
                db_path=db_path,
                min_edge=min_edge,
                hold_period_minutes=hold_period_minutes,
                slippage=slippage,
            ),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def parse_timestamp(value: str) -> int:
    stripped = value.strip()
    if stripped.isdigit():
        return int(stripped)

    normalized = stripped.removesuffix("Z") + "+00:00" if stripped.endswith("Z") else stripped
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())
