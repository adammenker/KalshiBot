from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, cast

from kalshibot.client import KalshiClient
from kalshibot.defaults import (
    DEFAULT_DEPTH_WINDOW,
    DEFAULT_MAX_VENUE_SPREAD,
    DEFAULT_MIN_BUY_SIZE,
    DEFAULT_MIN_DEPTH_SIZE,
    DEFAULT_MIN_EDGE,
    DEFAULT_MIN_FEE_ADJUSTED_EDGE,
)
from kalshibot.fees import kalshi_round_trip_fee, kalshi_taker_fee
from kalshibot.market_urls import kalshi_market_url, polymarket_market_url
from kalshibot.polymarket import PolymarketClient, parse_order_levels
from kalshibot.utils import optional_decimal, optional_string


Outcome = Literal["yes", "no"]
FeeMode = Literal["round-trip", "entry-only"]
FEE_MODE_CHOICES: tuple[FeeMode, ...] = ("round-trip", "entry-only")
DEFAULT_FEE_MODE: FeeMode = "entry-only"
ONE_DOLLAR = Decimal("1")


@dataclass(frozen=True)
class MarketPair:
    label: str
    kalshi_ticker: str
    polymarket_token_id: str
    polymarket_condition_id: str | None = None
    outcome: Outcome = "yes"
    polymarket_slug: str | None = None
    kalshi_url: str | None = None
    polymarket_url: str | None = None
    polymarket_yes_token_id: str | None = None
    polymarket_no_token_id: str | None = None
    side_mapping: str = "same"
    category: str | None = None
    confidence: Decimal | None = None
    match_status: str | None = None
    target_size: Decimal = Decimal("1")


@dataclass(frozen=True)
class PriceLevel:
    price: Decimal
    size: Decimal


@dataclass(frozen=True)
class KalshiTopOfBook:
    ticker: str
    yes_bid: Decimal | None
    yes_bid_size: Decimal | None
    yes_ask: Decimal | None
    yes_ask_size: Decimal | None
    no_bid: Decimal | None
    no_bid_size: Decimal | None
    no_ask: Decimal | None
    no_ask_size: Decimal | None


@dataclass(frozen=True)
class SpreadCheck:
    label: str
    outcome: Outcome
    kalshi_ticker: str
    polymarket_token_id: str
    polymarket_condition_id: str | None
    polymarket_open_interest: Decimal | None
    polymarket_volume: Decimal | None
    kalshi_mid_price: Decimal | None
    polymarket_mid_price: Decimal | None
    polymarket_mid_minus_kalshi_mid: Decimal | None
    kalshi_buy_price: Decimal
    kalshi_sell_price: Decimal | None
    kalshi_buy_size: Decimal | None
    kalshi_buy_depth: Decimal
    kalshi_spread: Decimal | None
    polymarket_buy_price: Decimal
    polymarket_sell_price: Decimal | None
    polymarket_buy_size: Decimal | None
    polymarket_buy_depth: Decimal
    polymarket_spread: Decimal | None
    depth_window: Decimal
    polymarket_minus_kalshi: Decimal
    kalshi_lower: bool
    passes_filters: bool
    filter_reasons: tuple[str, ...]
    kalshi_url: str = ""
    polymarket_url: str = ""
    kalshi_entry_fee: Decimal = Decimal("0")
    kalshi_exit_fee: Decimal = Decimal("0")
    kalshi_round_trip_fee: Decimal = Decimal("0")
    fee_mode: FeeMode = DEFAULT_FEE_MODE
    fee_adjustment: Decimal = Decimal("0")
    fee_adjusted_edge: Decimal = Decimal("0")
    target_size: Decimal = Decimal("1")


def load_market_pairs(path: Path) -> list[MarketPair]:
    payload = json.loads(path.read_text())
    pairs = payload.get("markets") if isinstance(payload, dict) else payload
    if not isinstance(pairs, list):
        raise ValueError("Market pair file must contain a list or an object with a 'markets' list")

    return [market_pair_from_dict(pair) for pair in pairs]


def market_pair_from_dict(payload: dict[str, Any]) -> MarketPair:
    kalshi_ticker = str(payload["kalshi_ticker"])
    side_mapping = str(payload.get("side_mapping") or "same")
    raw_polymarket_token_id = payload.get("polymarket_token_id") or payload.get("polymarket_yes_token_id")
    if not raw_polymarket_token_id:
        raise KeyError("polymarket_token_id")
    polymarket_token_id = str(raw_polymarket_token_id)
    if side_mapping == "same":
        polymarket_token_id = str(payload.get("polymarket_yes_token_id") or polymarket_token_id)
    return MarketPair(
        label=str(payload.get("label") or kalshi_ticker),
        kalshi_ticker=kalshi_ticker,
        polymarket_token_id=polymarket_token_id,
        polymarket_condition_id=optional_string(payload.get("polymarket_condition_id")),
        outcome=parse_outcome(payload.get("outcome", "yes")),
        polymarket_slug=optional_string(payload.get("polymarket_slug")),
        kalshi_url=optional_string(payload.get("kalshi_url")),
        polymarket_url=optional_string(payload.get("polymarket_url")),
        polymarket_yes_token_id=optional_string(payload.get("polymarket_yes_token_id")),
        polymarket_no_token_id=optional_string(payload.get("polymarket_no_token_id")),
        side_mapping=side_mapping,
        category=optional_string(payload.get("category")),
        confidence=optional_decimal(payload.get("confidence")),
        match_status=optional_string(payload.get("match_status")),
        target_size=optional_decimal(payload.get("target_size") or payload.get("quantity"))
        or Decimal("1"),
    )


def parse_outcome(value: Any) -> Outcome:
    outcome = str(value).lower()
    if outcome not in {"yes", "no"}:
        raise ValueError("Market pair outcome must be 'yes' or 'no'")
    return cast(Outcome, outcome)


def check_spread(
    pair: MarketPair,
    kalshi_client: KalshiClient,
    polymarket_client: PolymarketClient,
    *,
    max_venue_spread: Decimal = DEFAULT_MAX_VENUE_SPREAD,
    min_buy_size: Decimal = DEFAULT_MIN_BUY_SIZE,
    min_depth_size: Decimal = DEFAULT_MIN_DEPTH_SIZE,
    depth_window: Decimal = DEFAULT_DEPTH_WINDOW,
    min_edge: Decimal = DEFAULT_MIN_EDGE,
    min_fee_adjusted_edge: Decimal = DEFAULT_MIN_FEE_ADJUSTED_EDGE,
    fee_mode: FeeMode = DEFAULT_FEE_MODE,
) -> SpreadCheck:
    kalshi_book = kalshi_client.get_market_orderbook(pair.kalshi_ticker, depth=100)
    polymarket_book = polymarket_client.get_order_book(pair.polymarket_token_id)

    return build_spread_check_from_books(
        pair,
        kalshi_book,
        polymarket_book,
        polymarket_client,
        max_venue_spread=max_venue_spread,
        min_buy_size=min_buy_size,
        min_depth_size=min_depth_size,
        depth_window=depth_window,
        min_edge=min_edge,
        min_fee_adjusted_edge=min_fee_adjusted_edge,
        fee_mode=fee_mode,
    )


def build_spread_check_from_books(
    pair: MarketPair,
    kalshi_book: dict[str, Any],
    polymarket_book: dict[str, Any],
    polymarket_client: PolymarketClient,
    *,
    polymarket_open_interest: Decimal | None = None,
    polymarket_volume: Decimal | None = None,
    max_venue_spread: Decimal,
    min_buy_size: Decimal,
    min_depth_size: Decimal,
    depth_window: Decimal,
    min_edge: Decimal,
    min_fee_adjusted_edge: Decimal = DEFAULT_MIN_FEE_ADJUSTED_EDGE,
    fee_mode: FeeMode = DEFAULT_FEE_MODE,
) -> SpreadCheck:
    fee_mode = parse_fee_mode(fee_mode)
    kalshi_top = parse_kalshi_top_of_book(pair.kalshi_ticker, kalshi_book)
    polymarket_top = polymarket_client.top_of_book_from_order_book(
        pair.polymarket_token_id,
        polymarket_book,
    )

    kalshi_buy_price = average_kalshi_fill_price_for_outcome(
        kalshi_book,
        pair.outcome,
        pair.target_size,
    )
    kalshi_sell_price = kalshi_sell_price_for_outcome(kalshi_top, pair.outcome)
    kalshi_buy_size = kalshi_buy_size_for_outcome(kalshi_top, pair.outcome)
    kalshi_buy_depth = kalshi_depth_near_buy_price(kalshi_book, pair.outcome, depth_window)
    kalshi_spread = market_spread(kalshi_buy_price, kalshi_sell_price)
    kalshi_mid = market_midpoint(kalshi_buy_price, kalshi_sell_price)
    polymarket_buy_price = price_required(
        polymarket_top.best_ask.price if polymarket_top.best_ask else None,
        f"Polymarket {pair.outcome.upper()} best ask",
    )
    polymarket_sell_price = polymarket_top.best_bid.price if polymarket_top.best_bid else None
    polymarket_buy_size = polymarket_top.best_ask.size if polymarket_top.best_ask else None
    polymarket_buy_depth = polymarket_depth_near_buy_price(polymarket_book, depth_window)
    polymarket_spread = market_spread(polymarket_buy_price, polymarket_sell_price)
    polymarket_mid = market_midpoint(polymarket_buy_price, polymarket_sell_price)
    edge = polymarket_buy_price - kalshi_buy_price
    estimated_exit_price = kalshi_sell_price or kalshi_buy_price
    entry_fee = kalshi_taker_fee(kalshi_buy_price, pair.target_size)
    exit_fee = kalshi_taker_fee(estimated_exit_price, pair.target_size)
    round_trip_fee = kalshi_round_trip_fee(
        entry_price=kalshi_buy_price,
        exit_price=estimated_exit_price,
        contracts=pair.target_size,
    )
    fee_adjustment = fee_adjustment_for_mode(
        fee_mode=fee_mode,
        entry_fee=entry_fee,
        round_trip_fee=round_trip_fee,
    )
    fee_adjusted_edge = edge - (fee_adjustment / pair.target_size)
    mid_edge = (
        polymarket_mid - kalshi_mid
        if polymarket_mid is not None and kalshi_mid is not None
        else None
    )
    kalshi_lower = kalshi_buy_price < polymarket_buy_price
    filter_reasons = liquidity_filter_reasons(
        kalshi_lower=kalshi_lower,
        edge=edge,
        min_edge=min_edge,
        fee_adjusted_edge=fee_adjusted_edge,
        min_fee_adjusted_edge=min_fee_adjusted_edge,
        max_venue_spread=max_venue_spread,
        min_buy_size=min_buy_size,
        min_depth_size=min_depth_size,
        kalshi_spread=kalshi_spread,
        polymarket_spread=polymarket_spread,
        kalshi_buy_size=kalshi_buy_size,
        polymarket_buy_size=polymarket_buy_size,
        kalshi_buy_depth=kalshi_buy_depth,
        polymarket_buy_depth=polymarket_buy_depth,
    )

    return SpreadCheck(
        label=pair.label,
        outcome=pair.outcome,
        kalshi_ticker=pair.kalshi_ticker,
        polymarket_token_id=pair.polymarket_token_id,
        polymarket_condition_id=pair.polymarket_condition_id,
        polymarket_open_interest=polymarket_open_interest,
        polymarket_volume=polymarket_volume,
        kalshi_mid_price=kalshi_mid,
        polymarket_mid_price=polymarket_mid,
        polymarket_mid_minus_kalshi_mid=mid_edge,
        kalshi_buy_price=kalshi_buy_price,
        kalshi_sell_price=kalshi_sell_price,
        kalshi_buy_size=kalshi_buy_size,
        kalshi_buy_depth=kalshi_buy_depth,
        kalshi_spread=kalshi_spread,
        polymarket_buy_price=polymarket_buy_price,
        polymarket_sell_price=polymarket_sell_price,
        polymarket_buy_size=polymarket_buy_size,
        polymarket_buy_depth=polymarket_buy_depth,
        polymarket_spread=polymarket_spread,
        depth_window=depth_window,
        polymarket_minus_kalshi=edge,
        kalshi_lower=kalshi_lower,
        passes_filters=not filter_reasons,
        filter_reasons=tuple(filter_reasons),
        kalshi_url=pair.kalshi_url or kalshi_market_url(pair.kalshi_ticker),
        polymarket_url=pair.polymarket_url
        or polymarket_market_url(slug=pair.polymarket_slug, token_id=pair.polymarket_token_id),
        kalshi_entry_fee=entry_fee,
        kalshi_exit_fee=exit_fee,
        kalshi_round_trip_fee=round_trip_fee,
        fee_mode=fee_mode,
        fee_adjustment=fee_adjustment,
        fee_adjusted_edge=fee_adjusted_edge,
        target_size=pair.target_size,
    )


def parse_fee_mode(value: str) -> FeeMode:
    if value in FEE_MODE_CHOICES:
        return cast(FeeMode, value)
    raise ValueError(f"fee mode must be one of: {', '.join(FEE_MODE_CHOICES)}")


def fee_adjustment_for_mode(
    *,
    fee_mode: FeeMode,
    entry_fee: Decimal,
    round_trip_fee: Decimal,
) -> Decimal:
    if fee_mode == "entry-only":
        return entry_fee
    if fee_mode == "round-trip":
        return round_trip_fee
    raise ValueError(f"Unsupported fee mode: {fee_mode}")


def parse_kalshi_top_of_book(ticker: str, response: dict[str, Any]) -> KalshiTopOfBook:
    orderbook = response.get("orderbook_fp", {})
    yes_bid = best_bid(orderbook.get("yes_dollars", []))
    no_bid = best_bid(orderbook.get("no_dollars", []))

    return KalshiTopOfBook(
        ticker=ticker,
        yes_bid=yes_bid.price if yes_bid else None,
        yes_bid_size=yes_bid.size if yes_bid else None,
        yes_ask=ONE_DOLLAR - no_bid.price if no_bid else None,
        yes_ask_size=no_bid.size if no_bid else None,
        no_bid=no_bid.price if no_bid else None,
        no_bid_size=no_bid.size if no_bid else None,
        no_ask=ONE_DOLLAR - yes_bid.price if yes_bid else None,
        no_ask_size=yes_bid.size if yes_bid else None,
    )


def best_bid(levels: list[list[str]]) -> PriceLevel | None:
    parsed = [PriceLevel(price=Decimal(str(price)), size=Decimal(str(size))) for price, size in levels]
    return max(parsed, key=lambda level: level.price) if parsed else None


def kalshi_buy_price_for_outcome(top: KalshiTopOfBook, outcome: Outcome) -> Decimal:
    if outcome == "yes":
        return price_required(top.yes_ask, "Kalshi YES ask")
    return price_required(top.no_ask, "Kalshi NO ask")


def kalshi_sell_price_for_outcome(top: KalshiTopOfBook, outcome: Outcome) -> Decimal | None:
    return top.yes_bid if outcome == "yes" else top.no_bid


def kalshi_buy_size_for_outcome(top: KalshiTopOfBook, outcome: Outcome) -> Decimal | None:
    return top.yes_ask_size if outcome == "yes" else top.no_ask_size


def average_kalshi_fill_price_for_outcome(
    response: dict[str, Any],
    outcome: Outcome,
    quantity: Decimal,
) -> Decimal:
    if quantity <= 0:
        raise ValueError("target trade size must be positive")
    orderbook = response.get("orderbook_fp", {})
    if outcome == "yes":
        levels = [
            PriceLevel(price=ONE_DOLLAR - level.price, size=level.size)
            for level in best_to_worst_bid(orderbook.get("no_dollars", []))
        ]
    else:
        levels = [
            PriceLevel(price=ONE_DOLLAR - level.price, size=level.size)
            for level in best_to_worst_bid(orderbook.get("yes_dollars", []))
        ]
    return average_fill_price(levels, quantity, f"Kalshi {outcome.upper()} ask")


def average_fill_price(
    levels: list[PriceLevel],
    quantity: Decimal,
    label: str,
) -> Decimal:
    remaining = quantity
    cost = Decimal("0")
    for level in levels:
        fill_size = min(remaining, level.size)
        cost += fill_size * level.price
        remaining -= fill_size
        if remaining <= 0:
            return cost / quantity
    raise ValueError(f"Missing {label}; insufficient depth for {quantity} contracts")


def kalshi_depth_near_buy_price(
    response: dict[str, Any],
    outcome: Outcome,
    depth_window: Decimal,
) -> Decimal:
    orderbook = response.get("orderbook_fp", {})
    if outcome == "yes":
        no_bids = best_to_worst_bid(orderbook.get("no_dollars", []))
        if not no_bids:
            return Decimal("0")
        best_yes_ask = ONE_DOLLAR - no_bids[0].price
        return sum(
            level.size
            for level in no_bids
            if (ONE_DOLLAR - level.price) - best_yes_ask <= depth_window
        )

    yes_bids = best_to_worst_bid(orderbook.get("yes_dollars", []))
    if not yes_bids:
        return Decimal("0")
    best_no_ask = ONE_DOLLAR - yes_bids[0].price
    return sum(
        level.size for level in yes_bids if (ONE_DOLLAR - level.price) - best_no_ask <= depth_window
    )


def polymarket_depth_near_buy_price(response: dict[str, Any], depth_window: Decimal) -> Decimal:
    asks = sorted(parse_order_levels(response.get("asks", [])), key=lambda level: level.price)
    if not asks:
        return Decimal("0")
    best_ask = asks[0].price
    return sum(level.size for level in asks if level.price - best_ask <= depth_window)


def best_to_worst_bid(levels: list[list[str]]) -> list[PriceLevel]:
    parsed = [PriceLevel(price=Decimal(str(price)), size=Decimal(str(size))) for price, size in levels]
    return sorted(parsed, key=lambda level: level.price, reverse=True)


def market_spread(buy_price: Decimal, sell_price: Decimal | None) -> Decimal | None:
    return buy_price - sell_price if sell_price is not None else None


def market_midpoint(buy_price: Decimal, sell_price: Decimal | None) -> Decimal | None:
    return (buy_price + sell_price) / Decimal("2") if sell_price is not None else None


def liquidity_filter_reasons(
    *,
    kalshi_lower: bool,
    edge: Decimal,
    min_edge: Decimal,
    fee_adjusted_edge: Decimal,
    min_fee_adjusted_edge: Decimal,
    max_venue_spread: Decimal,
    min_buy_size: Decimal,
    min_depth_size: Decimal,
    kalshi_spread: Decimal | None,
    polymarket_spread: Decimal | None,
    kalshi_buy_size: Decimal | None,
    polymarket_buy_size: Decimal | None,
    kalshi_buy_depth: Decimal,
    polymarket_buy_depth: Decimal,
) -> list[str]:
    reasons = []
    if not kalshi_lower:
        reasons.append("kalshi_not_lower")
    if edge < min_edge:
        reasons.append("edge_below_minimum")
    if fee_adjusted_edge < min_fee_adjusted_edge:
        reasons.append("fee_adjusted_edge_below_minimum")
    if kalshi_spread is None or kalshi_spread > max_venue_spread:
        reasons.append("kalshi_spread_too_wide")
    if polymarket_spread is None or polymarket_spread > max_venue_spread:
        reasons.append("polymarket_spread_too_wide")
    if kalshi_buy_size is None or kalshi_buy_size < min_buy_size:
        reasons.append("kalshi_buy_size_too_small")
    if polymarket_buy_size is None or polymarket_buy_size < min_buy_size:
        reasons.append("polymarket_buy_size_too_small")
    if kalshi_buy_depth < min_depth_size:
        reasons.append("kalshi_depth_too_small")
    if polymarket_buy_depth < min_depth_size:
        reasons.append("polymarket_depth_too_small")
    return reasons


def price_required(price: Decimal | None, label: str) -> Decimal:
    if price is None:
        raise ValueError(f"Missing {label}; market may not have enough orderbook liquidity")
    return price


def format_spread_check(check: SpreadCheck) -> dict[str, str | bool]:
    return {
        "label": check.label,
        "outcome": check.outcome,
        "kalshi_ticker": check.kalshi_ticker,
        "kalshi_url": check.kalshi_url,
        "polymarket_token_id": check.polymarket_token_id,
        "polymarket_url": check.polymarket_url,
        "polymarket_condition_id": check.polymarket_condition_id or "",
        "polymarket_open_interest": str(check.polymarket_open_interest)
        if check.polymarket_open_interest is not None
        else "",
        "polymarket_volume": str(check.polymarket_volume)
        if check.polymarket_volume is not None
        else "",
        "kalshi_mid_price": str(check.kalshi_mid_price) if check.kalshi_mid_price is not None else "",
        "polymarket_mid_price": str(check.polymarket_mid_price)
        if check.polymarket_mid_price is not None
        else "",
        "polymarket_mid_minus_kalshi_mid": str(check.polymarket_mid_minus_kalshi_mid)
        if check.polymarket_mid_minus_kalshi_mid is not None
        else "",
        "kalshi_buy_price": str(check.kalshi_buy_price),
        "kalshi_sell_price": str(check.kalshi_sell_price) if check.kalshi_sell_price else "",
        "kalshi_buy_size": str(check.kalshi_buy_size) if check.kalshi_buy_size else "",
        "kalshi_buy_depth": str(check.kalshi_buy_depth),
        "kalshi_spread": str(check.kalshi_spread) if check.kalshi_spread else "",
        "polymarket_buy_price": str(check.polymarket_buy_price),
        "polymarket_sell_price": str(check.polymarket_sell_price)
        if check.polymarket_sell_price
        else "",
        "polymarket_buy_size": str(check.polymarket_buy_size)
        if check.polymarket_buy_size
        else "",
        "polymarket_buy_depth": str(check.polymarket_buy_depth),
        "polymarket_spread": str(check.polymarket_spread) if check.polymarket_spread else "",
        "depth_window": str(check.depth_window),
        "polymarket_minus_kalshi": str(check.polymarket_minus_kalshi),
        "spread_cents": str(check.polymarket_minus_kalshi * Decimal("100")),
        "kalshi_entry_fee": str(check.kalshi_entry_fee),
        "kalshi_exit_fee": str(check.kalshi_exit_fee),
        "kalshi_round_trip_fee": str(check.kalshi_round_trip_fee),
        "fee_mode": check.fee_mode,
        "fee_adjustment": str(check.fee_adjustment),
        "fee_adjusted_edge": str(check.fee_adjusted_edge),
        "fee_adjusted_edge_cents": str(check.fee_adjusted_edge * Decimal("100")),
        "kalshi_lower": check.kalshi_lower,
        "passes_filters": check.passes_filters,
        "filter_reasons": ", ".join(check.filter_reasons),
        "target_size": str(check.target_size),
    }
