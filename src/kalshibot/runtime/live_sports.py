from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from kalshibot.client import KalshiClient
from kalshibot.discovery.models import KalshiDiscoveryMarket
from kalshibot.discovery.sources import kalshi_discovery_market, unique_kalshi_markets
from kalshibot.discovery.taxonomy import market_type

LIVE_STATUSES = {
    "inprogress",
    "in_progress",
    "in-progress",
    "live",
    "p",
    "playing",
    "running",
    "started",
}
ENDED_STATUSES = {
    "canceled",
    "cancelled",
    "closed",
    "complete",
    "completed",
    "ended",
    "final",
    "finished",
    "postponed",
    "settled",
}
NOT_STARTED_STATUSES = {
    "created",
    "not_started",
    "not-started",
    "not started",
    "pre",
    "scheduled",
}


@dataclass(frozen=True)
class LiveSportsMilestone:
    id: str
    title: str
    milestone_type: str | None
    start_date: str | None
    end_date: str | None
    status: str | None
    league: str | None
    primary_event_tickers: tuple[str, ...]
    related_event_tickers: tuple[str, ...]
    raw: dict[str, Any]


@dataclass(frozen=True)
class LiveSportsDiscoveryResult:
    milestones_seen: int
    live_milestones: tuple[LiveSportsMilestone, ...]
    event_tickers: tuple[str, ...]
    markets: tuple[KalshiDiscoveryMarket, ...]
    live_data_checked: int
    live_data_rejected: int


def fetch_live_sports_kalshi_markets(
    client: KalshiClient,
    *,
    now: datetime | None = None,
    lookback_hours: int = 8,
    future_window_minutes: int = 15,
    milestone_limit: int = 500,
    milestone_pages: int = 2,
    market_limit_per_event: int = 1000,
    market_pages_per_event: int = 1,
    include_related_event_tickers: bool = False,
    kalshi_market_types: set[str] | None = None,
    confirm_live_data: bool = True,
) -> LiveSportsDiscoveryResult:
    current_time = ensure_utc(now or datetime.now(timezone.utc))
    minimum_start_date = rfc3339(current_time - timedelta(hours=lookback_hours))
    milestones = [
        milestone_from_dict(raw)
        for raw in client.list_milestones(
            category="Sports",
            minimum_start_date=minimum_start_date,
            limit=milestone_limit,
            max_pages=milestone_pages,
        )
    ]
    candidates = [
        milestone
        for milestone in milestones
        if milestone_is_live_candidate(
            milestone,
            now=current_time,
            future_window_minutes=future_window_minutes,
        )
    ]

    live_data_by_milestone: dict[str, dict[str, Any]] = {}
    live_data_rejected = 0
    if confirm_live_data and candidates:
        for chunk in chunked([milestone.id for milestone in candidates], 100):
            for live_data in client.get_live_data_batch(chunk):
                if isinstance(live_data, dict):
                    milestone_id = str(live_data.get("milestone_id") or "")
                    if milestone_id:
                        live_data_by_milestone[milestone_id] = live_data
        confirmed: list[LiveSportsMilestone] = []
        for milestone in candidates:
            live_data = live_data_by_milestone.get(milestone.id)
            if live_data_confirms_active_game(live_data, fallback_status=milestone.status):
                confirmed.append(milestone)
            else:
                live_data_rejected += 1
        candidates = confirmed

    event_tickers = live_event_tickers(
        candidates,
        include_related_event_tickers=include_related_event_tickers,
    )
    markets: list[KalshiDiscoveryMarket] = []
    for event_ticker in event_tickers:
        for raw_market in client.list_markets(
            status="open",
            limit=market_limit_per_event,
            max_pages=market_pages_per_event,
            event_ticker=event_ticker,
            mve_filter="exclude",
        ):
            if isinstance(raw_market, dict):
                markets.append(kalshi_discovery_market(raw_market))
    if kalshi_market_types:
        markets = [
            market
            for market in markets
            if market_type(market.full_title) in kalshi_market_types
        ]

    return LiveSportsDiscoveryResult(
        milestones_seen=len(milestones),
        live_milestones=tuple(candidates),
        event_tickers=event_tickers,
        markets=tuple(unique_kalshi_markets(markets)),
        live_data_checked=len(live_data_by_milestone),
        live_data_rejected=live_data_rejected,
    )


def milestone_from_dict(raw: dict[str, Any]) -> LiveSportsMilestone:
    details = raw.get("details") if isinstance(raw.get("details"), dict) else {}
    return LiveSportsMilestone(
        id=str(raw.get("id") or ""),
        title=str(raw.get("title") or ""),
        milestone_type=optional_string(raw.get("type")),
        start_date=optional_string(raw.get("start_date")),
        end_date=optional_string(raw.get("end_date")),
        status=optional_string(details.get("status")),
        league=optional_string(details.get("league") or details.get("competition")),
        primary_event_tickers=string_tuple(raw.get("primary_event_tickers")),
        related_event_tickers=string_tuple(raw.get("related_event_tickers")),
        raw=raw,
    )


def milestone_is_live_candidate(
    milestone: LiveSportsMilestone,
    *,
    now: datetime | None = None,
    future_window_minutes: int = 15,
) -> bool:
    if not milestone.id:
        return False
    status = normalize_status(milestone.status)
    if status in ENDED_STATUSES or status in NOT_STARTED_STATUSES:
        return False
    if status not in LIVE_STATUSES:
        return False
    start_date = parse_datetime(milestone.start_date)
    if start_date is None:
        return True
    latest_allowed_start = ensure_utc(now or datetime.now(timezone.utc)) + timedelta(
        minutes=future_window_minutes
    )
    return start_date <= latest_allowed_start


def live_data_confirms_active_game(
    live_data: dict[str, Any] | None,
    *,
    fallback_status: str | None = None,
) -> bool:
    if not live_data:
        return normalize_status(fallback_status) in LIVE_STATUSES
    details = live_data.get("details") if isinstance(live_data.get("details"), dict) else {}
    statuses = [
        details.get("status"),
        details.get("match_status"),
        details.get("widget_status"),
        details.get("game_status"),
        details.get("event_status"),
    ]
    normalized_statuses = {normalize_status(status) for status in statuses if status is not None}
    if normalized_statuses & ENDED_STATUSES:
        return False
    if details.get("ended") is True or details.get("is_final") is True:
        return False
    if normalized_statuses & LIVE_STATUSES:
        return True
    if normalized_statuses & NOT_STARTED_STATUSES:
        return False
    return normalize_status(fallback_status) in LIVE_STATUSES


def live_event_tickers(
    milestones: list[LiveSportsMilestone],
    *,
    include_related_event_tickers: bool,
) -> tuple[str, ...]:
    tickers: list[str] = []
    for milestone in milestones:
        source_tickers = (
            (*milestone.primary_event_tickers, *milestone.related_event_tickers)
            if include_related_event_tickers
            else milestone.primary_event_tickers
        )
        for ticker in source_tickers:
            if ticker and ticker not in tickers:
                tickers.append(ticker)
    return tuple(tickers)


def normalize_status(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_")


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return ensure_utc(datetime.fromisoformat(text))
    except ValueError:
        return None


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def rfc3339(value: datetime) -> str:
    return ensure_utc(value).isoformat().replace("+00:00", "Z")


def string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value if item)


def optional_string(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def chunked(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]
