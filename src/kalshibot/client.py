from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import requests

from kalshibot.auth import auth_headers, load_private_key
from kalshibot.config import KalshiConfig


class KalshiClient:
    def __init__(self, config: KalshiConfig, timeout_seconds: float = 10.0) -> None:
        self.config = config
        self.timeout_seconds = timeout_seconds
        self.private_key = load_private_key(config.private_key_path)
        self.session = requests.Session()

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.request("GET", path, params=params)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        path = "/" + path.lstrip("/")
        url = f"{self.config.base_url}{path}"
        sign_path = urlparse(url).path

        response = self.session.request(
            method=method.upper(),
            url=url,
            params=params,
            json=json,
            headers=auth_headers(
                self.config.api_key_id,
                self.private_key,
                method,
                sign_path,
            ),
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    def get_balance(self) -> dict[str, Any]:
        return self.get("/portfolio/balance")

    def get_market(self, ticker: str) -> dict[str, Any]:
        return self.get(f"/markets/{ticker}")

    def list_markets(
        self,
        *,
        status: str = "open",
        limit: int = 1000,
        max_pages: int = 1,
        event_ticker: str | None = None,
        series_ticker: str | None = None,
        mve_filter: str = "exclude",
    ) -> list[dict[str, Any]]:
        markets: list[dict[str, Any]] = []
        cursor = ""
        for _ in range(max_pages):
            params: dict[str, Any] = {
                "limit": limit,
                "status": status,
                "mve_filter": mve_filter,
            }
            if cursor:
                params["cursor"] = cursor
            if event_ticker:
                params["event_ticker"] = event_ticker
            if series_ticker:
                params["series_ticker"] = series_ticker
            response = self.get("/markets", params=params)
            markets.extend(response.get("markets", []))
            cursor = str(response.get("cursor") or "")
            if not cursor:
                break
        return markets

    def get_market_orderbook(self, ticker: str, depth: int = 1) -> dict[str, Any]:
        return self.get(f"/markets/{ticker}/orderbook", params={"depth": depth})

    def get_event(self, event_ticker: str, *, with_nested_markets: bool = False) -> dict[str, Any]:
        return self.get(
            f"/events/{event_ticker}",
            params={"with_nested_markets": str(with_nested_markets).lower()},
        )

    def list_milestones(
        self,
        *,
        limit: int = 500,
        max_pages: int = 1,
        minimum_start_date: str | None = None,
        category: str | None = None,
        competition: str | None = None,
        milestone_type: str | None = None,
        related_event_ticker: str | None = None,
    ) -> list[dict[str, Any]]:
        milestones: list[dict[str, Any]] = []
        cursor = ""
        for _ in range(max_pages):
            params: dict[str, Any] = {"limit": limit}
            if cursor:
                params["cursor"] = cursor
            if minimum_start_date:
                params["minimum_start_date"] = minimum_start_date
            if category:
                params["category"] = category
            if competition:
                params["competition"] = competition
            if milestone_type:
                params["type"] = milestone_type
            if related_event_ticker:
                params["related_event_ticker"] = related_event_ticker
            response = self.get("/milestones", params=params)
            milestones.extend(response.get("milestones", []))
            cursor = str(response.get("cursor") or "")
            if not cursor:
                break
        return milestones

    def get_live_data(self, milestone_id: str, *, include_player_stats: bool = False) -> dict[str, Any]:
        return self.get(
            f"/live_data/milestone/{milestone_id}",
            params={"include_player_stats": str(include_player_stats).lower()},
        )

    def get_live_data_batch(
        self,
        milestone_ids: list[str],
        *,
        include_player_stats: bool = False,
    ) -> list[dict[str, Any]]:
        if not milestone_ids:
            return []
        response = self.get(
            "/live_data/batch",
            params={
                "milestone_ids": milestone_ids,
                "include_player_stats": str(include_player_stats).lower(),
            },
        )
        return response.get("live_datas", [])

    def get_market_candlesticks(
        self,
        series_ticker: str,
        ticker: str,
        *,
        start_ts: int,
        end_ts: int,
        period_interval: int = 1,
    ) -> dict[str, Any]:
        return self.get(
            f"/series/{series_ticker}/markets/{ticker}/candlesticks",
            params={
                "start_ts": start_ts,
                "end_ts": end_ts,
                "period_interval": period_interval,
            },
        )
