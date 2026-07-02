from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Protocol

import requests

from kalshibot.config import LocalLLMConfig


@dataclass(frozen=True)
class MarketTitleMatch:
    same_market: bool
    confidence: float
    reason: str
    method: str
    is_same_event: bool | None = None
    side_mapping: str | None = None
    blocking_issues: tuple[str, ...] = ()
    differences: tuple[str, ...] = ()


class TitleMatcherLLM(Protocol):
    def match_titles(self, polymarket_title: str, kalshi_title: str) -> MarketTitleMatch:
        ...


class OllamaTitleMatcher:
    def __init__(self, config: LocalLLMConfig) -> None:
        self.config = config
        self.session = requests.Session()

    def match_titles(self, polymarket_title: str, kalshi_title: str) -> MarketTitleMatch:
        response = self.session.post(
            f"{self.config.base_url}/api/generate",
            json={
                "model": self.config.model,
                "prompt": market_match_prompt(polymarket_title, kalshi_title),
                "stream": False,
                "format": "json",
                "options": {
                    "temperature": 0,
                },
            },
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        parsed = parse_json_object(str(payload.get("response", "")))
        blocking_issues = tuple(str(issue) for issue in parsed.get("blocking_issues", []) or [])
        differences = tuple(str(issue) for issue in parsed.get("differences", []) or [])
        same_contract = parsed.get("is_same_tradable_contract", parsed.get("same_market", False))
        return MarketTitleMatch(
            same_market=bool(same_contract),
            confidence=clamp_confidence(parsed.get("confidence", 0)),
            reason=str(parsed.get("reason", "")).strip()
            or "; ".join(blocking_issues or differences)
            or "No reason provided",
            method="ollama",
            is_same_event=bool(parsed.get("is_same_event", parsed.get("same_market", False))),
            side_mapping=str(parsed.get("side_mapping", "") or "") or None,
            blocking_issues=blocking_issues,
            differences=differences,
        )


def match_market_titles(
    polymarket_title: str,
    kalshi_title: str,
    *,
    llm: TitleMatcherLLM | None = None,
    use_llm: bool = True,
) -> MarketTitleMatch:
    if use_llm and llm is not None:
        try:
            return llm.match_titles(polymarket_title, kalshi_title)
        except (OSError, ValueError, requests.RequestException) as exc:
            fallback = heuristic_match_market_titles(polymarket_title, kalshi_title)
            return MarketTitleMatch(
                same_market=fallback.same_market,
                confidence=min(fallback.confidence, 0.60),
                reason=f"Local LLM unavailable or returned invalid output: {exc}. {fallback.reason}",
                method="heuristic_fallback",
            )
    return heuristic_match_market_titles(polymarket_title, kalshi_title)


def market_match_prompt(polymarket_title: str, kalshi_title: str) -> str:
    return f"""
You are matching prediction-market contracts from Polymarket and Kalshi.

Decide whether these two titles refer to the same tradable contract.
Be strict. They must match the same event, outcome/side, date or period, threshold, and settlement
condition. Same event but different winner/outcome is NOT the same market. Same topic but different
threshold, date, city, league, candidate, or resolution rule is NOT the same market.

Return only JSON with this schema:
{{
  "is_same_event": true or false,
  "is_same_tradable_contract": true or false,
  "side_mapping": "same", "inverse", or "unknown",
  "confidence": number from 0 to 1,
  "blocking_issues": ["strict blockers, if any"],
  "differences": ["non-blocking differences, if any"],
  "reason": "short explanation",
  "recommended_status": "approved", "maybe", or "rejected"
}}

Polymarket title: {polymarket_title}
Kalshi title: {kalshi_title}
""".strip()


def parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
        if not match:
            raise ValueError("Local LLM response did not contain a JSON object")
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("Local LLM response JSON must be an object")
    return parsed


def heuristic_match_market_titles(polymarket_title: str, kalshi_title: str) -> MarketTitleMatch:
    poly_terms = important_terms(polymarket_title)
    kalshi_terms = important_terms(kalshi_title)
    if not poly_terms or not kalshi_terms:
        return MarketTitleMatch(
            same_market=False,
            confidence=0,
            reason="One or both titles did not contain enough comparable terms",
            method="heuristic",
        )

    overlap = poly_terms & kalshi_terms
    union = poly_terms | kalshi_terms
    jaccard = len(overlap) / len(union)
    numeric_consistent = numeric_tokens(polymarket_title) == numeric_tokens(kalshi_title)
    conflicts = conflicting_outcome_terms(poly_terms, kalshi_terms)
    same_market = jaccard >= 0.58 and numeric_consistent and not conflicts
    confidence = jaccard
    if not numeric_consistent:
        confidence = min(confidence, 0.35)
    if conflicts:
        confidence = min(confidence, 0.25)

    reasons = [f"term overlap={jaccard:.2f}"]
    if not numeric_consistent:
        reasons.append("numeric/date/threshold tokens differ")
    if conflicts:
        reasons.append("outcome terms conflict")
    return MarketTitleMatch(
        same_market=same_market,
        confidence=round(confidence, 4),
        reason=", ".join(reasons),
        method="heuristic",
    )


def important_terms(title: str) -> set[str]:
    normalized = normalize_title(title)
    return {
        token
        for token in normalized.split()
        if len(token) > 1 and token not in STOP_WORDS and not token.isdigit()
    }


def normalize_title(title: str) -> str:
    lowered = title.lower()
    lowered = lowered.replace("–", " ").replace("-", " ")
    lowered = re.sub(r"[^a-z0-9.]+", " ", lowered)
    replacements = {
        "saudi arabia": "saudiarabia",
        "ir iran": "iran",
        "iran": "iran",
        "curacao": "curacao",
        "cura ao": "curacao",
        "tie": "draw",
        "draw": "draw",
        "wins": "win",
        "winner": "win",
        "beat": "win",
        "beats": "win",
        "vs": "versus",
        "v": "versus",
    }
    for source, target in replacements.items():
        lowered = re.sub(rf"\b{re.escape(source)}\b", target, lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def numeric_tokens(title: str) -> tuple[str, ...]:
    return tuple(re.findall(r"\d+(?:\.\d+)?", title.lower()))


def conflicting_outcome_terms(left_terms: set[str], right_terms: set[str]) -> bool:
    outcomes = {"draw", "win", "yes", "no", "over", "under"}
    left_outcomes = left_terms & outcomes
    right_outcomes = right_terms & outcomes
    if not left_outcomes or not right_outcomes:
        return False
    return left_outcomes != right_outcomes


def clamp_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, confidence))


STOP_WORDS = {
    "a",
    "an",
    "and",
    "at",
    "be",
    "by",
    "contract",
    "cup",
    "fifa",
    "for",
    "game",
    "in",
    "market",
    "match",
    "of",
    "on",
    "or",
    "the",
    "to",
    "will",
    "world",
}
