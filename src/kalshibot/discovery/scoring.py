from __future__ import annotations

from kalshibot.market_matcher import important_terms

MIN_SEMANTIC_SCORE_WITHOUT_TERM_OVERLAP = 0.55


def lexical_overlap(left: str, right: str) -> float:
    left_terms = comparable_terms(left)
    right_terms = comparable_terms(right)
    if not left_terms or not right_terms:
        return 0
    return len(left_terms & right_terms) / len(left_terms | right_terms)


def comparable_terms(title: str) -> set[str]:
    return important_terms(title)


def candidate_passes_prefilter(
    semantic_score: float,
    lexical_score: float,
    prefilter_threshold: float,
) -> bool:
    if lexical_score > 0:
        return hybrid_similarity(semantic_score, lexical_score) >= prefilter_threshold
    return semantic_score >= max(prefilter_threshold, MIN_SEMANTIC_SCORE_WITHOUT_TERM_OVERLAP)


def hybrid_similarity(semantic_score: float, lexical_score: float) -> float:
    return (semantic_score * 0.7) + (lexical_score * 0.3)
