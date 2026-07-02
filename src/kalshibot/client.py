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
