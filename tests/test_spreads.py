from __future__ import annotations

from decimal import Decimal

import pytest

from kalshibot.fees import kalshi_taker_fee
from kalshibot.spreads import (
    MarketPair,
    SpreadCheck,
    average_kalshi_fill_price_for_outcome,
    format_spread_check,
    kalshi_depth_near_buy_price,
    liquidity_filter_reasons,
    market_pair_from_dict,
    parse_kalshi_top_of_book,
    polymarket_depth_near_buy_price,
)


def test_parse_kalshi_top_of_book_computes_implied_asks() -> None:
    top = parse_kalshi_top_of_book(
        "TEST-TICKER",
        {
            "orderbook_fp": {
                "yes_dollars": [["0.4200", "13.00"], ["0.4100", "10.00"]],
                "no_dollars": [["0.5600", "17.00"], ["0.4400", "29.00"]],
            }
        },
    )

    assert top.yes_bid == Decimal("0.4200")
    assert top.yes_bid_size == Decimal("13.00")
    assert top.yes_ask == Decimal("0.4400")
    assert top.yes_ask_size == Decimal("17.00")
    assert top.no_bid == Decimal("0.5600")
    assert top.no_ask == Decimal("0.5800")


def test_market_pair_from_dict_defaults_to_yes() -> None:
    pair = market_pair_from_dict(
        {
            "kalshi_ticker": "KXTEST-26",
            "polymarket_token_id": "123",
        }
    )

    assert pair == MarketPair(
        label="KXTEST-26",
        kalshi_ticker="KXTEST-26",
        polymarket_token_id="123",
        polymarket_condition_id=None,
        outcome="yes",
    )


def test_market_pair_from_dict_rejects_unknown_outcome() -> None:
    with pytest.raises(ValueError, match="outcome"):
        market_pair_from_dict(
            {
                "kalshi_ticker": "KXTEST-26",
                "polymarket_token_id": "123",
                "outcome": "maybe",
            }
        )


def test_format_spread_check_uses_positive_diff_when_polymarket_is_higher() -> None:
    check = SpreadCheck(
        label="Example",
        outcome="yes",
        kalshi_ticker="KXTEST-26",
        polymarket_token_id="123",
        polymarket_condition_id=None,
        polymarket_open_interest=None,
        polymarket_volume=None,
        kalshi_mid_price=Decimal("0.43"),
        polymarket_mid_price=Decimal("0.48"),
        polymarket_mid_minus_kalshi_mid=Decimal("0.05"),
        kalshi_buy_price=Decimal("0.44"),
        kalshi_sell_price=Decimal("0.42"),
        kalshi_buy_size=Decimal("13"),
        kalshi_buy_depth=Decimal("52"),
        kalshi_spread=Decimal("0.02"),
        polymarket_buy_price=Decimal("0.49"),
        polymarket_sell_price=Decimal("0.47"),
        polymarket_buy_size=Decimal("25"),
        polymarket_buy_depth=Decimal("80"),
        polymarket_spread=Decimal("0.02"),
        depth_window=Decimal("0.03"),
        polymarket_minus_kalshi=Decimal("0.05"),
        kalshi_lower=True,
        passes_filters=True,
        filter_reasons=(),
    )

    assert format_spread_check(check)["spread_cents"] == "5.00"
    assert format_spread_check(check)["kalshi_lower"] is True
    assert format_spread_check(check)["passes_filters"] is True


def test_liquidity_filter_reasons_flags_wide_polymarket_spread() -> None:
    reasons = liquidity_filter_reasons(
        kalshi_lower=True,
        edge=Decimal("0.15"),
        min_edge=Decimal("0.00"),
        fee_adjusted_edge=Decimal("0.11"),
        min_fee_adjusted_edge=Decimal("0.01"),
        max_venue_spread=Decimal("0.05"),
        min_buy_size=Decimal("10"),
        min_depth_size=Decimal("50"),
        kalshi_spread=Decimal("0.03"),
        polymarket_spread=Decimal("0.46"),
        kalshi_buy_size=Decimal("50"),
        polymarket_buy_size=Decimal("100"),
        kalshi_buy_depth=Decimal("200"),
        polymarket_buy_depth=Decimal("200"),
    )

    assert reasons == ["polymarket_spread_too_wide"]


def test_liquidity_filter_reasons_requires_kalshi_to_be_lower() -> None:
    reasons = liquidity_filter_reasons(
        kalshi_lower=False,
        edge=Decimal("-0.01"),
        min_edge=Decimal("0.00"),
        fee_adjusted_edge=Decimal("-0.05"),
        min_fee_adjusted_edge=Decimal("0.01"),
        max_venue_spread=Decimal("0.05"),
        min_buy_size=Decimal("10"),
        min_depth_size=Decimal("50"),
        kalshi_spread=Decimal("0.01"),
        polymarket_spread=Decimal("0.01"),
        kalshi_buy_size=Decimal("50"),
        polymarket_buy_size=Decimal("100"),
        kalshi_buy_depth=Decimal("200"),
        polymarket_buy_depth=Decimal("200"),
    )

    assert reasons == [
        "kalshi_not_lower",
        "edge_below_minimum",
        "fee_adjusted_edge_below_minimum",
    ]


def test_kalshi_depth_near_buy_price_sums_implied_asks_in_window() -> None:
    response = {
        "orderbook_fp": {
            "no_dollars": [
                ["0.5100", "10.00"],
                ["0.5400", "20.00"],
                ["0.5600", "30.00"],
            ]
        }
    }

    assert kalshi_depth_near_buy_price(response, "yes", Decimal("0.03")) == Decimal("50.00")


def test_average_kalshi_fill_price_uses_depth_for_target_size() -> None:
    response = {
        "orderbook_fp": {
            "no_dollars": [
                ["0.5500", "1.00"],
                ["0.5400", "2.00"],
            ]
        }
    }

    assert average_kalshi_fill_price_for_outcome(response, "yes", Decimal("3")) == Decimal(
        "0.4566666666666666666666666667"
    )


def test_kalshi_taker_fee_rounds_trade_fee_up_to_cent() -> None:
    assert kalshi_taker_fee(Decimal("0.50"), Decimal("1")) == Decimal("0.02")
    assert kalshi_taker_fee(Decimal("0.50"), Decimal("100")) == Decimal("1.75")


def test_polymarket_depth_near_buy_price_sums_asks_in_window() -> None:
    response = {
        "asks": [
            {"price": "0.51", "size": "10"},
            {"price": "0.53", "size": "15"},
            {"price": "0.56", "size": "20"},
        ]
    }

    assert polymarket_depth_near_buy_price(response, Decimal("0.03")) == Decimal("25")


def test_liquidity_filter_reasons_flags_shallow_depth() -> None:
    reasons = liquidity_filter_reasons(
        kalshi_lower=True,
        edge=Decimal("0.05"),
        min_edge=Decimal("0.00"),
        fee_adjusted_edge=Decimal("0.01"),
        min_fee_adjusted_edge=Decimal("0.01"),
        max_venue_spread=Decimal("0.05"),
        min_buy_size=Decimal("10"),
        min_depth_size=Decimal("50"),
        kalshi_spread=Decimal("0.02"),
        polymarket_spread=Decimal("0.02"),
        kalshi_buy_size=Decimal("25"),
        polymarket_buy_size=Decimal("25"),
        kalshi_buy_depth=Decimal("49.99"),
        polymarket_buy_depth=Decimal("40"),
    )

    assert reasons == ["kalshi_depth_too_small", "polymarket_depth_too_small"]
