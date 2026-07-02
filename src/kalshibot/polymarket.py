from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import requests

from kalshibot.config import PolymarketConfig


@dataclass(frozen=True)
class OrderLevel:
    price: Decimal
    size: Decimal


@dataclass(frozen=True)
class TopOfBook:
    token_id: str
    best_bid: OrderLevel | None
    best_ask: OrderLevel | None
    last_trade_price: Decimal | None


class PolymarketClient:
    """Read-only client for Polymarket Gamma and CLOB public market-data APIs."""

    def __init__(self, config: PolymarketConfig, timeout_seconds: float = 10.0) -> None:
        self.config = config
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()

    def get_market_by_slug(self, slug: str) -> dict[str, Any]:
        return self._get_gamma(f"/markets/slug/{slug}")

    def get_market_by_clob_token(self, token_id: str) -> dict[str, Any] | None:
        markets = self._get_gamma("/markets", params={"clob_token_ids": token_id})
        if not isinstance(markets, list):
            raise ValueError("Expected Polymarket markets endpoint to return a list")
        return markets[0] if markets else None

    def get_event_by_slug(self, slug: str) -> dict[str, Any]:
        return self._get_gamma(f"/events/slug/{slug}")

    def list_events(
        self,
        *,
        active: bool = True,
        closed: bool = False,
        limit: int = 20,
        order: str = "volume_24hr",
        ascending: bool = False,
    ) -> list[dict[str, Any]]:
        events = self._get_gamma(
            "/events",
            params={
                "active": str(active).lower(),
                "closed": str(closed).lower(),
                "limit": limit,
                "order": order,
                "ascending": str(ascending).lower(),
            },
        )
        if not isinstance(events, list):
            raise ValueError("Expected Polymarket events endpoint to return a list")
        return events

    def public_search(
        self,
        query: str,
        *,
        limit_per_type: int = 10,
        events_status: str = "active",
        keep_closed_markets: int = 0,
    ) -> dict[str, Any]:
        result = self._get_gamma(
            "/public-search",
            params={
                "q": query,
                "limit_per_type": limit_per_type,
                "events_status": events_status,
                "keep_closed_markets": keep_closed_markets,
                "search_profiles": "false",
                "search_tags": "false",
            },
        )
        if not isinstance(result, dict):
            raise ValueError("Expected Polymarket public-search endpoint to return an object")
        return result

    def get_order_book(self, token_id: str) -> dict[str, Any]:
        return self._get_clob("/book", params={"token_id": token_id})

    def get_price(self, token_id: str, side: str) -> dict[str, Any]:
        side = side.upper()
        if side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        return self._get_clob("/price", params={"token_id": token_id, "side": side})

    def get_prices_history(
        self,
        token_id: str,
        *,
        start_ts: int,
        end_ts: int,
        interval: str = "1m",
        fidelity: int = 1,
    ) -> dict[str, Any]:
        return self._get_clob(
            "/prices-history",
            params={
                "market": token_id,
                "startTs": start_ts,
                "endTs": end_ts,
                "interval": interval,
                "fidelity": fidelity,
            },
        )

    def get_open_interest(self, condition_id: str) -> Decimal | None:
        rows = self._get_data("/oi", params={"market": condition_id})
        if not isinstance(rows, list):
            raise ValueError("Expected Polymarket open interest endpoint to return a list")
        for row in rows:
            if isinstance(row, dict) and str(row.get("market", "")).lower() == condition_id.lower():
                return parse_optional_decimal(row.get("value"))
        return None

    def get_top_of_book(self, token_id: str) -> TopOfBook:
        book = self.get_order_book(token_id)
        return self.top_of_book_from_order_book(token_id, book)

    def top_of_book_from_order_book(self, token_id: str, book: dict[str, Any]) -> TopOfBook:
        bids = parse_order_levels(book.get("bids", []))
        asks = parse_order_levels(book.get("asks", []))
        last_trade_price = parse_optional_decimal(book.get("last_trade_price"))

        return TopOfBook(
            token_id=token_id,
            best_bid=max(bids, key=lambda level: level.price) if bids else None,
            best_ask=min(asks, key=lambda level: level.price) if asks else None,
            last_trade_price=last_trade_price,
        )

    def _get_gamma(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._get(self.config.gamma_base_url, path, params=params)

    def _get_clob(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._get(self.config.clob_base_url, path, params=params)

    def _get_data(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._get(self.config.data_base_url, path, params=params)

    def _get(
        self,
        base_url: str,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        response = self.session.get(
            f"{base_url}{'/' + path.lstrip('/')}",
            params=params,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return response.json()


def parse_jsonish_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return parsed
    raise ValueError(f"Expected JSON list or list value, got {type(value).__name__}")


def extract_market_tokens(market: dict[str, Any]) -> list[dict[str, str]]:
    outcomes = parse_jsonish_list(market.get("outcomes"))
    token_ids = parse_jsonish_list(market.get("clobTokenIds"))

    if len(outcomes) != len(token_ids):
        raise ValueError("Market outcomes and clobTokenIds have different lengths")

    return [
        {"outcome": str(outcome), "token_id": str(token_id)}
        for outcome, token_id in zip(outcomes, token_ids)
    ]


def parse_order_levels(levels: Any) -> list[OrderLevel]:
    return [
        OrderLevel(price=Decimal(str(level["price"])), size=Decimal(str(level["size"])))
        for level in levels
    ]


def parse_optional_decimal(value: Any) -> Decimal | None:
    if value in {None, ""}:
        return None
    return Decimal(str(value))


def format_top_of_book(top: TopOfBook) -> dict[str, str | None]:
    return {
        "token_id": top.token_id,
        "best_bid": str(top.best_bid.price) if top.best_bid else None,
        "best_bid_size": str(top.best_bid.size) if top.best_bid else None,
        "best_ask": str(top.best_ask.price) if top.best_ask else None,
        "best_ask_size": str(top.best_ask.size) if top.best_ask else None,
        "last_trade_price": str(top.last_trade_price) if top.last_trade_price is not None else None,
    }
