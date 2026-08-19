"""Keyword-based relevance scorer for papers."""
from __future__ import annotations

import re
from typing import Dict, List

from ..config import Config, KeywordTier
from ..models import Paper


def _normalize(text: str) -> str:
    """Lowercase, collapse whitespace."""
    return re.sub(r"\s+", " ", text.lower().strip())


def _build_pattern(term: str) -> re.Pattern:
    """Build a word-boundary-aware regex for a term."""
    escaped = re.escape(term.lower())
    # Allow for hyphen/space variation (e.g., "LINE-1" matches "LINE 1")
    escaped = escaped.replace(r"\-", r"[-\s]?")
    return re.compile(r"(?<![a-zA-Z0-9])" + escaped + r"(?![a-zA-Z0-9])", re.IGNORECASE)


class KeywordScorer:
    """
    Scores papers by weighted keyword matching.

    Scoring formula:
        score = sum over tiers of (weight * title_count * title_multiplier + weight * abstract_count)
    """

    def __init__(self, config: Config):
        self._tiers: Dict[str, KeywordTier] = config.keyword_tiers
        # Pre-compile all patterns for speed
        self._compiled: Dict[str, List[tuple]] = {}
        for tier_name, tier in self._tiers.items():
            self._compiled[tier_name] = [
                (term, _build_pattern(term)) for term in tier.terms
            ]

    def score(self, paper: Paper) -> float:
        """Compute a relevance score for a single paper."""
        title = _normalize(paper.title)
        abstract = _normalize(paper.abstract)

        total = 0.0
        for tier_name, tier in self._tiers.items():
            compiled = self._compiled.get(tier_name, [])
            for term, pattern in compiled:
                title_hits = len(pattern.findall(title))
                abstract_hits = len(pattern.findall(abstract))

                if title_hits > 0:
                    total += tier.weight * title_hits * tier.title_multiplier
                if abstract_hits > 0:
                    total += tier.weight * abstract_hits

        return round(total, 2)

    def score_batch(self, papers: List[Paper]) -> List[Paper]:
        """Score all papers in place and return them."""
        for paper in papers:
            paper.keyword_score = self.score(paper)
        return papers

    def filter_and_rank(
        self, papers: List[Paper], threshold: float = 3.0, top_k: int = 100
    ) -> List[Paper]:
        """Score, filter by threshold, and return top_k papers by score."""
        self.score_batch(papers)
        filtered = [p for p in papers if p.keyword_score >= threshold]
        return sorted(filtered, key=lambda p: p.keyword_score, reverse=True)[:top_k]
