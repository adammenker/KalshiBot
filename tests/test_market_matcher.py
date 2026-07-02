from __future__ import annotations

from kalshibot.market_matcher import (
    MarketTitleMatch,
    match_market_titles,
    parse_json_object,
)


class FakeLLM:
    def __init__(self, match: MarketTitleMatch) -> None:
        self.match = match

    def match_titles(self, polymarket_title: str, kalshi_title: str) -> MarketTitleMatch:
        return self.match


class BrokenLLM:
    def match_titles(self, polymarket_title: str, kalshi_title: str) -> MarketTitleMatch:
        raise ValueError("bad local model output")


def test_match_market_titles_uses_local_llm_result() -> None:
    match = match_market_titles(
        "Spain vs Saudi Arabia",
        "World Cup game: Spain beats Saudi Arabia",
        llm=FakeLLM(MarketTitleMatch(True, 0.93, "same winner and event", "fake")),
    )

    assert match == MarketTitleMatch(True, 0.93, "same winner and event", "fake")


def test_match_market_titles_falls_back_when_llm_fails() -> None:
    match = match_market_titles(
        "Spain vs Saudi Arabia - Spain wins",
        "World Cup game: Spain wins vs Saudi Arabia",
        llm=BrokenLLM(),
    )

    assert match.same_market is True
    assert match.method == "heuristic_fallback"
    assert match.confidence <= 0.60


def test_heuristic_rejects_different_outcomes_in_same_event() -> None:
    match = match_market_titles(
        "Spain vs Saudi Arabia - Spain wins",
        "World Cup game: Spain vs Saudi Arabia tie",
        use_llm=False,
    )

    assert match.same_market is False
    assert "outcome terms conflict" in match.reason


def test_heuristic_rejects_different_threshold_numbers() -> None:
    match = match_market_titles(
        "NYC high temperature 80-81",
        "Highest temperature in NYC above 82",
        use_llm=False,
    )

    assert match.same_market is False
    assert "numeric/date/threshold tokens differ" in match.reason


def test_parse_json_object_accepts_wrapped_json() -> None:
    assert parse_json_object('Here is the answer: {"same_market": true, "confidence": 0.8}') == {
        "same_market": True,
        "confidence": 0.8,
    }
