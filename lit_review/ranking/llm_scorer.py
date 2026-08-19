"""LLM-based semantic re-ranking using the Claude API."""
from __future__ import annotations

import json
import os
import time
from typing import List, Optional, Tuple

from ..models import Paper

LAB_PROFILE = """
You are a research assistant for the Chuong Lab at CU Boulder. The lab studies:
1. Transposable elements (TEs: ERVs, SINEs, LINEs, Alu elements) as drivers of gene regulatory evolution
2. TE co-option as enhancers in innate immune gene networks (type I IFN response, inflammasome)
3. TE exonization — intronic TEs spliced into host mRNAs generating novel protein isoforms
   (flagship example: Alu-derived IFNAR2-S decoy receptor for type I IFN)
4. Aberrant TE reactivation in cancer creating neo-enhancers (LTR10 in colorectal cancer,
   ERV/SP140 in ovarian cancer)
5. m6A RNA methylation regulating TE-derived regulatory elements
6. Pan-genome approaches to polymorphic TE insertions as regulatory variation
7. Long-read (Nanopore) transcriptomics for TE-derived isoform discovery
8. Comparative immunogenomics across primates, rodents, bats, ruminants

Key published systems: MER41 ERV enhancers (IFN response, Science 2016), LTR10 in CRC
(Science Advances 2024), IFNAR2/Alu exonization (Cell 2024), B2 SINE enhancers (eLife 2023),
Bov-A3/LINE-2 in cattle (Genome Research 2022).

Genes/pathways of special interest: IFNAR2, IFNAR1, type I IFN, IL18, IL18R1, IKBKE,
MECP2, TAF1, XDP, SP140, cGAS-STING, AIM2, APOL1, ATG12.
"""

SCORING_PROMPT = """Rate each paper's relevance to the Chuong Lab's research on a scale 0–10:

10 = Directly about TEs (ERVs/SINEs/LINEs) in immunity or cancer — our exact topic
8–9 = Closely related: IFN signaling, TE biology, TE-derived isoforms, enhancer reactivation
6–7 = Useful: enhancer biology, alternative splicing, functional genomics in immunity/cancer,
      long-read transcriptomics, comparative genomics methods, CRISPR screens in immune cells
4–5 = Moderate: chromatin biology, cancer epigenomics, pan-genome methods, m6A/epitranscriptomics
2–3 = Tangential: general genomics/transcriptomics methods without immune/TE angle
0–1 = Not relevant

For each paper, provide:
- score (float 0–10)
- reason (1 sentence explaining relevance to the lab, or why it scores low)

Return ONLY a JSON array in this exact format:
[{"index": 0, "score": 8.5, "reason": "..."}, ...]

Papers to score:
"""


class LLMScorer:
    """Semantic re-ranking using Claude API."""

    def __init__(self, model: str = "claude-sonnet-4-5-20250929", api_key: Optional[str] = None):
        self._model = model
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")

    def _is_available(self) -> bool:
        return bool(self._api_key)

    def rerank(self, papers: List[Paper], top_k: int = 50) -> List[Paper]:
        """
        Re-rank top_k papers using LLM scoring.
        Papers are assumed to be pre-sorted by keyword score (descending).
        """
        if not self._is_available():
            print("  LLM scoring skipped: ANTHROPIC_API_KEY not set. Use --llm flag after setting key.")
            return papers

        try:
            import anthropic
        except ImportError:
            print("  LLM scoring skipped: anthropic package not installed.")
            return papers

        candidates = papers[:top_k]
        print(f"  LLM re-ranking {len(candidates)} candidates...")

        client = anthropic.Anthropic(api_key=self._api_key)
        batch_size = 10

        for i in range(0, len(candidates), batch_size):
            batch = candidates[i : i + batch_size]
            scores = self._score_batch(client, batch, i)
            for idx_offset, (score, reason) in enumerate(scores):
                if idx_offset < len(batch):
                    batch[idx_offset].llm_score = score
                    batch[idx_offset].llm_explanation = reason
            time.sleep(0.5)

        # Re-sort all candidates by LLM score; papers beyond top_k keep keyword score
        candidates.sort(key=lambda p: p.llm_score, reverse=True)

        # Recombine: re-ranked candidates + unscored papers
        scored_dois = {p.doi for p in candidates}
        unscored = [p for p in papers if p.doi not in scored_dois]
        return candidates + unscored

    def _score_batch(
        self, client, papers: List[Paper], global_offset: int
    ) -> List[Tuple[float, str]]:
        """Score a batch of papers. Returns list of (score, reason) tuples."""
        paper_lines = []
        for i, p in enumerate(papers):
            abstract_preview = (p.abstract[:300] + "...") if len(p.abstract) > 300 else p.abstract
            paper_lines.append(
                f"[{i}] Title: {p.title}\n"
                f"    Authors: {p.short_authors}\n"
                f"    Journal: {p.journal}\n"
                f"    Abstract: {abstract_preview}"
            )

        prompt = SCORING_PROMPT + "\n\n".join(paper_lines)

        try:
            resp = client.messages.create(
                model=self._model,
                max_tokens=1024,
                system=LAB_PROFILE,
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.content[0].text.strip()
            # Extract JSON from response (handle markdown code blocks)
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            data = json.loads(text)
            results = [(0.0, "")] * len(papers)
            for item in data:
                idx = int(item.get("index", -1))
                if 0 <= idx < len(papers):
                    results[idx] = (float(item.get("score", 0.0)), str(item.get("reason", "")))
            return results
        except Exception as e:
            print(f"    LLM scoring error: {e}")
            return [(0.0, "")] * len(papers)
