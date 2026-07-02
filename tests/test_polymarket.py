from __future__ import annotations

from decimal import Decimal

import pytest

from kalshibot.polymarket import (
    TopOfBook,
    extract_market_tokens,
    format_top_of_book,
    parse_jsonish_list,
    parse_order_levels,
)


def test_parse_jsonish_list_accepts_gamma_json_string_fields() -> None:
    assert parse_jsonish_list('["Yes", "No"]') == ["Yes", "No"]


def test_extract_market_tokens_pairs_outcomes_with_clob_token_ids() -> None:
    market = {
        "outcomes": '["Yes", "No"]',
        "clobTokenIds": '["123", "456"]',
    }

    assert extract_market_tokens(market) == [
        {"outcome": "Yes", "token_id": "123"},
        {"outcome": "No", "token_id": "456"},
    ]


def test_extract_market_tokens_rejects_mismatched_market_fields() -> None:
    market = {
        "outcomes": '["Yes", "No"]',
        "clobTokenIds": '["123"]',
    }

    with pytest.raises(ValueError, match="different lengths"):
        extract_market_tokens(market)


def test_parse_order_levels_uses_decimal_values() -> None:
    levels = parse_order_levels([{"price": "0.45", "size": "100"}])

    assert levels[0].price == Decimal("0.45")
    assert levels[0].size == Decimal("100")


def test_format_top_of_book_handles_missing_sides() -> None:
    formatted = format_top_of_book(
        top=TopOfBook(
            token_id="123",
            best_bid=None,
            best_ask=None,
            last_trade_price=None,
        )
    )

    assert formatted == {
        "token_id": "123",
        "best_bid": None,
        "best_bid_size": None,
        "best_ask": None,
        "best_ask_size": None,
        "last_trade_price": None,
    }
