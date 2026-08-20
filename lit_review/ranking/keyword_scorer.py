"""Keyword-based relevance scorer for papers."""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

from ..config import Config, KeywordTier
from ..models import Paper

Span = Tuple[int, int]


def _normalize(text: str) -> str:
    """Lowercase, collapse whitespace."""
    return re.sub(r"\s+", " ", text.lower().strip())


def _build_pattern(term: str) -> re.Pattern:
    """Build a word-boundary-aware regex for a term."""
    escaped = re.escape(term.lower())
    # Allow for hyphen/space variation (e.g., "LINE-1" matches "LINE 1")
    escaped = escaped.replace(r"\-", r"[-\s]?")
    return re.compile(r"(?<![a-zA-Z0-9])" + escaped + r"(?![a-zA-Z0-9])", re.IGNORECASE)


def _subsumed(span: Span, spans: List[Span]) -> bool:
    """
    True if this match sits entirely inside a strictly longer match.

    Several terms deliberately nest: "retrotransposon" inside "LTR
    retrotransposon", "enhancer" inside "TE-derived enhancer". Crediting both
    scores one phrase two or three times over, so only the most specific term
    matching a given span is counted.
    """
    start, end = span
    width = end - start
    return any(
        other_start <= start and end <= other_end and (other_end - other_start) > width
        for other_start, other_end in spans
    )


class KeywordScorer:
    """
    Scores papers by weighted keyword matching.

        score = sum over distinct matched terms of (tier weight x field multiplier)

    Each term is credited **at most once per field**, and a term nested inside a
    longer match is not credited at all. The score therefore measures how many
    distinct relevant concepts a paper touches, not how often it repeats one of
    them.

    Crediting every occurrence instead let a single word dominate the digest: in
    the 2026-08-19 run the top-ranked paper was a telomere/aging study that drew
    64 of its 72 points from five mentions of "STING", placing it above every
    transposable-element paper in the corpus.
    """

    def __init__(self, config: Config):
        self._tiers: Dict[str, KeywordTier] = config.keyword_tiers
        # Pre-compile all patterns for speed
        self._compiled: Dict[str, List[tuple]] = {}
        for tier_name, tier in self._tiers.items():
            self._compiled[tier_name] = [
                (term, _build_pattern(term)) for term in tier.terms
            ]

    def _field_score(self, text: str, title_field: bool) -> float:
        """
        Score one field. Subsumption is resolved across all tiers at once, so a
        tier-2 "enhancer" nested in a tier-1 "TE-derived enhancer" drops out.
        """
        if not text:
            return 0.0

        matched: List[Tuple[float, List[Span]]] = []
        for tier_name, tier in self._tiers.items():
            points = tier.weight * (tier.title_multiplier if title_field else 1.0)
            for _term, pattern in self._compiled[tier_name]:
                spans = [m.span() for m in pattern.finditer(text)]
                if spans:
                    matched.append((points, spans))

        all_spans = [s for _points, spans in matched for s in spans]

        total = 0.0
        for points, spans in matched:
            # Credit the term when at least one occurrence stands on its own
            if any(not _subsumed(s, all_spans) for s in spans):
                total += points
        return total

    def score(self, paper: Paper) -> float:
        """Compute a relevance score for a single paper."""
        title = self._field_score(_normalize(paper.title), title_field=True)
        abstract = self._field_score(_normalize(paper.abstract), title_field=False)
        return round(title + abstract, 2)

    def score_batch(self, papers: List[Paper]) -> List[Paper]:
        """Score all papers in place and return them."""
        for paper in papers:
            paper.keyword_score = self.score(paper)
        return papers
